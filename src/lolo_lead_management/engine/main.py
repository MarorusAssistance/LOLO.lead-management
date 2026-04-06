from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.enums import PlannerAction, QualificationOutcome, RunStatus, StageName
from lolo_lead_management.domain.models import (
    AcceptedLeadRecord,
    LeadSearchStartRequest,
    LeadSearchStartResponse,
    RunIteration,
    SearchBudget,
    SearchRunSnapshot,
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
                self._update_progress(
                    state.run,
                    stage=StageName.SOURCE,
                    message=self._sourcing_message(state.run),
                )
                source_result = self._source_stage.execute(state)
                query = source_result.executed_queries[0].query if source_result.executed_queries else None
                if query is None:
                    state.run.budget.source_attempts_used = state.run.budget.source_attempt_budget
                else:
                    state.run.budget.source_attempts_used += 1
                state.current_query = query
                state.current_source_result = source_result

                active_stage = StageName.ASSEMBLE
                self._update_progress(
                    state.run,
                    stage=StageName.ASSEMBLE,
                    message="Compacting public web evidence into a structured lead dossier.",
                )
                state.current_dossier = self._assemble_stage.execute(state)

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

                if state.current_qualification.outcome == QualificationOutcome.ENRICH and state.run.budget.can_enrich():
                    active_stage = StageName.ENRICH
                    self._update_progress(
                        state.run,
                        stage=StageName.ENRICH,
                        message="Collecting extra evidence for a promising candidate.",
                    )
                    state.run.budget.enrich_attempts_used += 1
                    state.current_source_result = self._enrich_stage.execute(state)

                    active_stage = StageName.ASSEMBLE
                    self._update_progress(
                        state.run,
                        stage=StageName.ASSEMBLE,
                        message="Merging newly found evidence into the current lead dossier.",
                    )
                    state.current_dossier = self._assemble_stage.execute(state)

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
                        query=query,
                        dossier=state.current_dossier,
                        qualification=state.current_qualification,
                        research_trace=state.current_dossier.research_trace if state.current_dossier else [],
                        documents_considered=state.current_dossier.documents_considered if state.current_dossier else 0,
                        documents_selected=state.current_dossier.documents_selected if state.current_dossier else 0,
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
            company_name=option.company_name,
            website=option.website,
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
