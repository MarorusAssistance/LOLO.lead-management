from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.enums import PlannerAction, QualificationOutcome, RunStatus
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
        normalized = self._normalize_stage.execute(payload)
        run = SearchRunSnapshot(
            request=normalized,
            budget=SearchBudget(
                source_attempt_budget=self._source_attempt_budget,
                enrich_attempt_budget=self._enrich_attempt_budget,
            ),
        )
        self._run_store.save_run(run)
        state = self._load_state_stage.execute(run)

        while state.should_continue:
            state.run.updated_at = datetime.now(timezone.utc)
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

            query, dossier = self._source_stage.execute(state)
            if query is None:
                state.run.budget.source_attempts_used = state.run.budget.source_attempt_budget
            else:
                state.run.budget.source_attempts_used += 1
            state.current_query = query
            state.current_dossier = dossier
            state.current_qualification = self._qualify_stage.execute(
                request_payload=state.run.request.model_dump(mode="json"),
                dossier_payload=dossier.model_dump(mode="json"),
            )

            if state.current_qualification.outcome == QualificationOutcome.ENRICH and state.run.budget.can_enrich():
                state.run.budget.enrich_attempts_used += 1
                state.current_dossier = self._enrich_stage.execute(state)
                state.current_qualification = self._qualify_stage.execute(
                    request_payload=state.run.request.model_dump(mode="json"),
                    dossier_payload=state.current_dossier.model_dump(mode="json"),
                )

            if state.current_qualification.outcome in {QualificationOutcome.ACCEPT, QualificationOutcome.REJECT_CLOSE_MATCH}:
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
                )
            )
            self._crm_write_stage.execute(state)
            self._continue_stage.execute(state)

        state.run.updated_at = datetime.now(timezone.utc)
        self._run_store.save_run(state.run)
        response = LeadSearchStartResponse(
            run_id=state.run.run_id,
            status=state.run.status,
            normalized_request=state.run.request,
            accepted_leads=state.run.accepted_leads,
            shortlist_id=state.run.shortlist_id,
            shortlist_options=state.run.shortlist_options,
            errors=state.run.errors,
            budget_summary=state.run.budget,
            applied_relaxation_stage=state.run.applied_relaxation_stage,
            completed_reason=state.run.completed_reason,
        )
        if self._archive_writer is not None:
            self._archive_writer.write(
                kind="lead-search-run",
                payload={
                    "run_id": state.run.run_id,
                    "request": payload.model_dump(mode="json"),
                    "response": response.model_dump(mode="json"),
                    "final_run": state.run.model_dump(mode="json"),
                },
            )
        return response

    def get_run(self, run_id: str) -> SearchRunSnapshot | None:
        return self._run_store.get_run(run_id)

    def select_shortlist_option(self, shortlist_id: str, option_number: int) -> SearchRunSnapshot | None:
        shortlist = self._shortlist_store.get_pending_shortlist(shortlist_id)
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
            role_title=option.qualification.type,
            company_name=option.company_name,
            qualification=option.qualification,
            commercial=option.commercial,
        )
        run.accepted_leads.append(accepted_record)
        run.shortlist_options = [item for item in run.shortlist_options if item.option_number != option_number]
        run.status = RunStatus.COMPLETED
        run.updated_at = datetime.now(timezone.utc)
        self._shortlist_store.clear_pending_shortlist(shortlist_id)
        self._run_store.save_run(run)
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
