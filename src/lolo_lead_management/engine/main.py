from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.enums import PlannerAction, QualificationOutcome, RunStatus, StageName
from lolo_lead_management.domain.models import (
    AcceptedLeadRecord,
    LeadSearchStartRequest,
    LeadSearchStartResponse,
    RunStageEvent,
    RunIteration,
    SearchBudget,
    SearchRunSnapshot,
    SourceAnchorCandidate,
    SourcePassResult,
    SourceStageTrace,
)
from lolo_lead_management.engine.stages.continue_or_finish import ContinueOrFinishStage
from lolo_lead_management.engine.stages.crm_write import CrmWriteStage
from lolo_lead_management.engine.stages.draft import DraftStage
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.enrich import EnrichStage
from lolo_lead_management.engine.stages.load_state import LoadStateStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.stages.plan import PlanStage
from lolo_lead_management.engine.stages.qualify import QualifyStage
from lolo_lead_management.engine.stages.source import SourceStage
from lolo_lead_management.infrastructure.run_archive import ExecutionArchiveWriter
from lolo_lead_management.ports.stores import SearchRunStore, ShortlistStore
from lolo_lead_management.engine.rules import dedupe_preserve_order, downgrade_enrich_to_close_match, merge_documents


class LeadManagementEngine:
    def __init__(
        self,
        *,
        normalize_stage: NormalizeStage,
        load_state_stage: LoadStateStage,
        plan_stage: PlanStage,
        source_stage: SourceStage,
        assemble_stage: AssembleStage,
        qualify_stage: QualifyStage,
        enrich_stage: EnrichStage,
        draft_stage: DraftStage,
        crm_write_stage: CrmWriteStage,
        continue_stage: ContinueOrFinishStage,
        run_store: SearchRunStore,
        shortlist_store: ShortlistStore,
        search_call_budget: int,
        source_attempt_budget: int,
        enrich_attempt_budget: int,
        archive_writer: ExecutionArchiveWriter | None = None,
    ) -> None:
        self._normalize_stage = normalize_stage
        self._load_state_stage = load_state_stage
        self._plan_stage = plan_stage
        self._source_stage = source_stage
        self._assemble_stage = assemble_stage
        self._qualify_stage = qualify_stage
        self._enrich_stage = enrich_stage
        self._draft_stage = draft_stage
        self._crm_write_stage = crm_write_stage
        self._continue_stage = continue_stage
        self._run_store = run_store
        self._shortlist_store = shortlist_store
        self._search_call_budget = search_call_budget
        self._source_attempt_budget = source_attempt_budget
        self._enrich_attempt_budget = enrich_attempt_budget
        self._archive_writer = archive_writer

    def start(self, payload: LeadSearchStartRequest) -> LeadSearchStartResponse:
        run = self.initialize_run(payload)
        completed = self.run_to_completion(run.run_id)
        return self.build_start_response(completed)

    def initialize_run(self, payload: LeadSearchStartRequest) -> SearchRunSnapshot:
        normalized = self._normalize_stage.execute(payload)
        run = SearchRunSnapshot(
            request=normalized,
            budget=SearchBudget(
                source_attempt_budget=self._source_attempt_budget,
                enrich_attempt_budget=self._enrich_attempt_budget,
                search_call_budget=self._search_call_budget,
            ),
        )
        self._update_progress(
            run,
            stage=StageName.NORMALIZE,
            message="Request normalized. Preparing durable search state.",
        )
        return run

    def run_to_completion(self, run_id: str, *, raise_on_error: bool = True) -> SearchRunSnapshot:
        run = self._run_store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        active_stage = run.current_stage or StageName.LOAD_STATE
        state = None
        try:
            self._update_progress(run, stage=StageName.LOAD_STATE, message="Loading exploration memory.")
            state = self._load_state_stage.execute(run)

            while state.should_continue:
                self._update_progress(
                    state.run,
                    stage=StageName.PLAN,
                    message=self._planning_message(state.run),
                )
                state.current_decision = self._plan_stage.execute(state)
                state.run.applied_relaxation_stage = state.current_decision.relaxation_stage

                if state.current_decision.action == PlannerAction.FINISH_ACCEPTED:
                    state.run.status = RunStatus.COMPLETED
                    state.run.completed_reason = state.current_decision.reason
                    break
                if state.current_decision.action == PlannerAction.FINISH_SHORTLIST:
                    state.run.status = RunStatus.COMPLETED
                    state.run.completed_reason = state.current_decision.reason
                    break
                if state.current_decision.action == PlannerAction.FINISH_NO_RESULT:
                    state.run.status = RunStatus.NO_RESULT
                    state.run.completed_reason = state.current_decision.reason
                    break

                active_stage = StageName.SOURCE
                state.current_dossier = None
                state.current_source_result = None
                state.current_source_trace = None
                state.current_discovery_source_trace = None
                state.current_anchored_source_trace = None
                state.current_enrich_trace = None
                state.current_assembler_trace = None
                state.current_qualification_trace = None
                state.current_continue_trace = None
                state.current_focus_company_resolution = None
                state.focus_company_locked = False
                state.pending_discovery_documents = []
                state.pending_discovery_traces = []
                state.pending_discovery_research_trace = []
                state.pending_discovery_queries = []
                state.discovery_attempts_for_current_pass = 0
                state.run.budget.source_attempts_used += 1
                aggregated_discovery: SourcePassResult | None = None
                focus_resolution = None
                query = None

                while (
                    state.discovery_attempts_for_current_pass < 2
                    and state.run.budget.can_search()
                    and not (focus_resolution and focus_resolution.selected_company)
                ):
                    state.discovery_attempts_for_current_pass += 1
                    self._update_progress(
                        state.run,
                        stage=StageName.SOURCE,
                        message=(
                            "Running discovery query 1 of 2 to gather plausible Spanish company candidates."
                            if state.discovery_attempts_for_current_pass == 1
                            else "Running discovery query 2 of 2 to resolve a plausible company focus."
                        ),
                    )
                    discovery_result = self._source_stage.execute(state)
                    batch_query = discovery_result.executed_queries[0].query if discovery_result.executed_queries else None
                    if batch_query is not None:
                        query = batch_query
                        state.current_query = batch_query
                    state.pending_discovery_documents = merge_documents([*state.pending_discovery_documents, *discovery_result.documents])
                    if discovery_result.source_trace is not None:
                        state.pending_discovery_traces.append(discovery_result.source_trace)
                    state.pending_discovery_research_trace = [*state.pending_discovery_research_trace, *discovery_result.research_trace]
                    state.pending_discovery_queries = [*state.pending_discovery_queries, *discovery_result.executed_queries]
                    aggregated_discovery = self._merge_discovery_results(aggregated_discovery, discovery_result, state)
                    state.current_source_result = aggregated_discovery
                    state.current_source_trace = aggregated_discovery.source_trace
                    state.current_discovery_source_trace = aggregated_discovery.source_trace

                    active_stage = StageName.ASSEMBLE
                    self._update_progress(
                        state.run,
                        stage=StageName.ASSEMBLE,
                        message=(
                            "Selecting one company focus from the first discovery batch."
                            if state.discovery_attempts_for_current_pass == 1
                            else "Selecting one company focus from the accumulated discovery batches."
                        ),
                    )
                    focus_resolution = self._assemble_stage.select_focus_company(state)
                    state.current_focus_company_resolution = focus_resolution
                    state.current_assembler_trace = {
                        "company_selection": self._assemble_stage.last_company_selection_trace or {},
                    }
                    if focus_resolution.selected_company or not discovery_result.executed_queries:
                        break

                if not focus_resolution or not focus_resolution.selected_company:
                    state.run.iterations.append(
                        RunIteration(
                            index=len(state.run.iterations) + 1,
                            planner_action=PlannerAction.SOURCE,
                            planner_reason=state.current_decision.reason if state.current_decision else None,
                            planner_relaxation_stage=state.current_decision.relaxation_stage if state.current_decision else None,
                            query=query,
                            focus_company_resolution=focus_resolution,
                            source_trace=state.current_discovery_source_trace,
                            assembler_trace=state.current_assembler_trace or {},
                        )
                    )

                    active_stage = StageName.CONTINUE_OR_FINISH
                    self._update_progress(
                        state.run,
                        stage=StageName.CONTINUE_OR_FINISH,
                        message="No clear company focus found. Checking whether another discovery attempt is needed.",
                    )
                    self._continue_stage.execute(state)
                    if state.run.iterations:
                        state.run.iterations[-1].continue_trace = state.current_continue_trace
                        self._run_store.save_run(state.run)
                    continue

                state.focus_company_locked = True

                active_stage = StageName.SOURCE
                self._update_progress(
                    state.run,
                    stage=StageName.SOURCE,
                    message="Gathering precise evidence only for the selected company focus.",
                )
                source_result = self._source_stage.execute(state)
                state.current_source_result = source_result
                state.current_source_trace = source_result.source_trace
                state.current_anchored_source_trace = source_result.source_trace

                active_stage = StageName.ASSEMBLE
                self._update_progress(
                    state.run,
                    stage=StageName.ASSEMBLE,
                    message="Compacting focus-locked public web evidence into a structured lead dossier.",
                )
                state.current_dossier = self._assemble_stage.execute(state)
                state.current_assembler_trace = {
                    "company_selection": self._assemble_stage.last_company_selection_trace or {},
                    "focus_locked_assembly": state.current_assembler_trace or {},
                }

                active_stage = StageName.QUALIFY
                self._update_progress(
                    state.run,
                    stage=StageName.QUALIFY,
                    message="Evaluating whether the candidate is an exact match or a close match.",
                )
                state.current_qualification = self._qualify_stage.execute(
                    request_payload=state.run.request.model_dump(mode="json"),
                    dossier_payload=state.current_dossier.model_dump(mode="json"),
                )
                state.current_qualification_trace = self._qualify_stage.last_trace

                if state.current_qualification.outcome == QualificationOutcome.ENRICH and state.run.budget.can_enrich():
                    active_stage = StageName.ENRICH
                    self._update_progress(
                        state.run,
                        stage=StageName.ENRICH,
                        message="Collecting extra evidence for a promising candidate.",
                    )
                    state.run.budget.enrich_attempts_used += 1
                    state.current_source_result = self._enrich_stage.execute(state)
                    state.current_enrich_trace = state.current_source_result.source_trace

                    active_stage = StageName.ASSEMBLE
                    self._update_progress(
                        state.run,
                        stage=StageName.ASSEMBLE,
                        message="Merging newly found evidence into the current lead dossier.",
                    )
                    state.current_dossier = self._assemble_stage.execute(state)
                    state.current_assembler_trace = {
                        "company_selection": self._assemble_stage.last_company_selection_trace or {},
                        "focus_locked_assembly": state.current_assembler_trace or {},
                    }

                    active_stage = StageName.REQUALIFY
                    self._update_progress(
                        state.run,
                        stage=StageName.REQUALIFY,
                        message="Re-checking the enriched candidate against the request.",
                    )
                    state.current_qualification = self._qualify_stage.execute(
                        request_payload=state.run.request.model_dump(mode="json"),
                        dossier_payload=state.current_dossier.model_dump(mode="json"),
                    )
                    state.current_qualification_trace = self._qualify_stage.last_trace
                if (
                    state.current_qualification.outcome == QualificationOutcome.ENRICH
                    and not state.run.budget.can_enrich()
                ):
                    state.current_qualification = downgrade_enrich_to_close_match(
                        state.current_qualification,
                        state.current_dossier,
                        state.run.request,
                    )
                    if state.current_qualification_trace is not None:
                        state.current_qualification_trace = state.current_qualification_trace.model_copy(
                            update={
                                "merged_decision": state.current_qualification,
                                "notes": [
                                    *state.current_qualification_trace.notes,
                                    "downgraded_after_enrich_budget_exhausted",
                                ],
                            }
                        )

                if state.current_qualification.outcome in {
                    QualificationOutcome.ACCEPT,
                    QualificationOutcome.REJECT_CLOSE_MATCH,
                }:
                    active_stage = StageName.DRAFT
                    self._update_progress(
                        state.run,
                        stage=StageName.DRAFT,
                        message="Preparing outreach drafts for a validated lead candidate.",
                    )
                    state.current_commercial = self._draft_stage.execute(
                        request_payload=state.run.request.model_dump(mode="json"),
                        dossier_payload=state.current_dossier.model_dump(mode="json"),
                        qualification_payload=state.current_qualification.model_dump(mode="json"),
                    )
                else:
                    state.current_commercial = None

                state.run.iterations.append(
                    RunIteration(
                        index=len(state.run.iterations) + 1,
                        planner_action=PlannerAction.SOURCE,
                        planner_reason=state.current_decision.reason if state.current_decision else None,
                        planner_relaxation_stage=state.current_decision.relaxation_stage if state.current_decision else None,
                        query=query,
                        focus_company_resolution=focus_resolution,
                        dossier=state.current_dossier,
                        qualification=state.current_qualification,
                        research_trace=state.current_dossier.research_trace if state.current_dossier else [],
                        documents_considered=state.current_dossier.documents_considered if state.current_dossier else 0,
                        documents_selected=state.current_dossier.documents_selected if state.current_dossier else 0,
                        source_trace=state.current_discovery_source_trace,
                        anchored_source_trace=state.current_anchored_source_trace,
                        enrich_trace=state.current_enrich_trace,
                        assembler_trace=state.current_assembler_trace or {},
                        qualification_trace=state.current_qualification_trace,
                    )
                )

                active_stage = StageName.CRM_WRITE
                self._update_progress(
                    state.run,
                    stage=StageName.CRM_WRITE,
                    message="Saving structured candidate state.",
                )
                self._crm_write_stage.execute(state)

                active_stage = StageName.CONTINUE_OR_FINISH
                self._update_progress(
                    state.run,
                    stage=StageName.CONTINUE_OR_FINISH,
                    message="Checking whether more sourcing is needed.",
                )
                self._continue_stage.execute(state)
                if state.run.iterations:
                    state.run.iterations[-1].continue_trace = state.current_continue_trace
                self._run_store.save_run(state.run)

            final_run = state.run if state is not None else run
            self._update_progress(
                final_run,
                stage=StageName.CONTINUE_OR_FINISH,
                message=self._final_progress_message(final_run),
            )
            self._archive_final_run(final_run)
            return final_run
        except Exception as exc:
            failed_run = state.run if state is not None else run
            failed_run.status = RunStatus.FAILED
            failed_run.completed_reason = "engine_failed"
            failed_run.errors = [*failed_run.errors, str(exc)]
            self._update_progress(
                failed_run,
                stage=active_stage,
                message="Search failed. Check the stored errors for details.",
            )
            self._archive_final_run(failed_run)
            if raise_on_error:
                raise
            return failed_run

    def build_start_response(self, run: SearchRunSnapshot) -> LeadSearchStartResponse:
        return LeadSearchStartResponse(
            run_id=run.run_id,
            status=run.status,
            normalized_request=run.request,
            current_stage=run.current_stage,
            progress_message=run.progress_message,
            last_heartbeat_at=run.last_heartbeat_at,
            accepted_leads=run.accepted_leads,
            shortlist_id=run.shortlist_id,
            shortlist_options=run.shortlist_options,
            errors=run.errors,
            budget_summary=run.budget,
            applied_relaxation_stage=run.applied_relaxation_stage,
            completed_reason=run.completed_reason,
        )

    def get_run(self, run_id: str) -> SearchRunSnapshot | None:
        return self._run_store.get_run(run_id)

    def get_shortlist(self, shortlist_id: str):
        return self._shortlist_store.get_pending_shortlist(shortlist_id)

    def get_shortlist_option(self, shortlist_id: str, option_number: int):
        shortlist = self.get_shortlist(shortlist_id)
        if shortlist is None:
            return None
        return next((item for item in shortlist.options if item.option_number == option_number), None)

    def select_shortlist_option(self, shortlist_id: str, option_number: int) -> SearchRunSnapshot | None:
        shortlist = self.get_shortlist(shortlist_id)
        if shortlist is None:
            return None
        option = next((item for item in shortlist.options if item.option_number == option_number), None)
        if option is None:
            return None

        run = self._run_store.get_run(shortlist.run_id)
        if run is None:
            return None

        accepted_record = AcceptedLeadRecord(
            person_name=option.person_name,
            role_title=option.role_title,
            lead_source_type=option.lead_source_type,
            person_confidence=option.person_confidence,
            primary_person_source_url=option.primary_person_source_url,
            company_name=option.company_name,
            website=option.website,
            website_resolution=option.website_resolution,
            country_code=option.country_code,
            evidence=option.evidence,
            qualification=option.qualification,
            commercial=option.commercial,
            research_trace=option.research_trace,
            field_evidence=option.field_evidence,
            contradictions=option.contradictions,
            evidence_quality=option.evidence_quality,
        )
        run.accepted_leads.append(accepted_record)
        remaining_options = [item for item in shortlist.options if item.option_number != option_number]
        run.shortlist_options = remaining_options
        run.status = RunStatus.COMPLETED
        remaining_count = len(remaining_options)
        self._update_progress(
            run,
            stage=StageName.CONTINUE_OR_FINISH,
            message=(
                "Shortlist option selected and promoted to accepted lead."
                if remaining_count == 0
                else f"Shortlist option selected and promoted to accepted lead. {remaining_count} shortlist options remain."
            ),
        )
        if remaining_options:
            self._shortlist_store.save_pending_shortlist(
                shortlist.model_copy(update={"options": remaining_options})
            )
        else:
            self._shortlist_store.clear_pending_shortlist(shortlist_id)
        if self._archive_writer is not None:
            self._archive_writer.write(
                kind="shortlist-selection",
                payload={
                    "run_id": run.run_id,
                    "shortlist_id": shortlist_id,
                    "option_number": option_number,
                    "final_run": run.model_dump(mode="json"),
                },
            )
        return run

    def _planning_message(self, run: SearchRunSnapshot) -> str:
        target_count = run.request.constraints.target_count
        next_index = len(run.accepted_leads) + 1
        return f"Planning the next step for lead {next_index} of {target_count}."

    def _merge_discovery_results(
        self,
        current: SourcePassResult | None,
        new_result: SourcePassResult,
        state,
    ) -> SourcePassResult:
        if current is None:
            trace = new_result.source_trace
            if trace is not None:
                trace = trace.model_copy(
                    update={
                        "discovery_batches_considered": state.discovery_attempts_for_current_pass,
                    }
                )
            return new_result.model_copy(update={"source_trace": trace})

        merged_documents = merge_documents([*current.documents, *new_result.documents])
        merged_queries = [*current.executed_queries, *new_result.executed_queries]
        merged_research_trace = [*current.research_trace, *new_result.research_trace]
        merged_notes = dedupe_preserve_order([*current.notes, *new_result.notes])
        merged_trace = self._merge_discovery_traces([*state.pending_discovery_traces])
        return current.model_copy(
            update={
                "sourcing_status": new_result.sourcing_status if merged_documents else current.sourcing_status,
                "executed_queries": merged_queries,
                "documents": merged_documents,
                "research_trace": merged_research_trace,
                "notes": merged_notes,
                "source_trace": merged_trace,
            }
        )

    def _merge_discovery_traces(self, traces: list[SourceStageTrace]) -> SourceStageTrace | None:
        if not traces:
            return None
        base = traces[-1]
        batch_traces = [
            {
                "discovery_directory_selected": trace.discovery_directory_selected,
                "discovery_directories_consumed_in_run": trace.discovery_directories_consumed_in_run,
                "discovery_ladder_position": trace.discovery_ladder_position,
                "selected_queries": trace.selected_queries,
                "llm_plan_input": trace.llm_plan_input,
                "llm_raw_plan": trace.llm_raw_plan,
                "sanitized_query_plan": trace.sanitized_query_plan,
                "merged_query_plan": trace.merged_query_plan,
                "query_traces": [item.model_dump(mode="json") for item in trace.query_traces],
                "documents_passed_to_assembler": [item.model_dump(mode="json") for item in trace.documents_passed_to_assembler],
                "excluded_terminal_company_documents": trace.excluded_terminal_company_documents,
                "notes": trace.notes,
            }
            for trace in traces
        ]
        anchor_support: dict[str, dict] = {}
        for trace in traces:
            for candidate in trace.anchor_candidates:
                entry = anchor_support.setdefault(
                    candidate.company_name,
                    {"support_count": 0, "evidence_urls": [], "notes": []},
                )
                entry["support_count"] = max(entry["support_count"], candidate.support_count)
                entry["evidence_urls"] = dedupe_preserve_order([*entry["evidence_urls"], *candidate.evidence_urls])
                entry["notes"] = dedupe_preserve_order([*entry["notes"], *candidate.notes])
        ranked_names = sorted(
            anchor_support.items(),
            key=lambda item: (item[1]["support_count"], len(item[1]["evidence_urls"])),
            reverse=True,
        )
        merged_anchor_candidates = [
            SourceAnchorCandidate(
                company_name=name,
                support_count=data["support_count"],
                evidence_urls=data["evidence_urls"][:4],
                notes=data["notes"][:4],
            )
            for name, data in ranked_names[:5]
        ]

        return base.model_copy(
            update={
                "batch_traces": batch_traces,
                "discovery_batches_considered": len(traces),
                "discovery_directory_selected": base.discovery_directory_selected,
                "discovery_directories_consumed_in_run": dedupe_preserve_order(
                    [item for trace in traces for item in trace.discovery_directories_consumed_in_run]
                ),
                "discovery_ladder_position": base.discovery_ladder_position,
                "selected_query_count": sum(trace.selected_query_count for trace in traces),
                "selected_queries": [query for trace in traces for query in trace.selected_queries],
                "query_traces": [item for trace in traces for item in trace.query_traces],
                "cross_company_rejections": dedupe_preserve_order(
                    [item for trace in traces for item in trace.cross_company_rejections]
                ),
                "anchor_candidates": merged_anchor_candidates,
                "excluded_companies": dedupe_preserve_order(
                    [item for trace in traces for item in trace.excluded_companies]
                ),
                "request_scoped_company_exclusions": dedupe_preserve_order(
                    [item for trace in traces for item in trace.request_scoped_company_exclusions]
                ),
                "documents_passed_to_assembler": [
                    item
                    for trace in traces
                    for item in trace.documents_passed_to_assembler
                ],
                "excluded_terminal_company_documents": dedupe_preserve_order(
                    [item for trace in traces for item in trace.excluded_terminal_company_documents]
                ),
                "notes": dedupe_preserve_order([item for trace in traces for item in trace.notes]),
            }
        )

    def _sourcing_message(self, run: SearchRunSnapshot) -> str:
        next_attempt = min(run.budget.source_attempts_used + 1, run.budget.source_attempt_budget)
        return f"Searching the web for candidate {next_attempt} of {run.budget.source_attempt_budget}."

    def _final_progress_message(self, run: SearchRunSnapshot) -> str:
        if run.status == RunStatus.FAILED:
            return "Search failed before completion."
        if run.accepted_leads:
            return f"Search completed with {len(run.accepted_leads)} accepted leads."
        if run.shortlist_options:
            return f"Search completed with {len(run.shortlist_options)} shortlist options."
        if run.status == RunStatus.NO_RESULT:
            return "Search completed with no suitable results."
        return "Search completed."

    def _update_progress(self, run: SearchRunSnapshot, *, stage: StageName, message: str) -> None:
        now = datetime.now(timezone.utc)
        run.current_stage = stage
        run.progress_message = message
        run.last_heartbeat_at = now
        run.updated_at = now
        run.stage_events.append(
            RunStageEvent(
                timestamp=now,
                stage=stage,
                message=message,
                run_status=run.status,
                source_attempts_used=run.budget.source_attempts_used,
                source_attempt_budget=run.budget.source_attempt_budget,
                enrich_attempts_used=run.budget.enrich_attempts_used,
                enrich_attempt_budget=run.budget.enrich_attempt_budget,
                search_calls_used=run.budget.search_calls_used,
                search_call_budget=run.budget.search_call_budget,
            )
        )
        self._run_store.save_run(run)

    def _archive_final_run(self, run: SearchRunSnapshot) -> None:
        if self._archive_writer is None:
            return
        response = self.build_start_response(run)
        self._archive_writer.write(
            kind="lead-search-run",
            payload={
                "run_id": run.run_id,
                "response": response.model_dump(mode="json"),
                "final_run": run.model_dump(mode="json"),
            },
        )
