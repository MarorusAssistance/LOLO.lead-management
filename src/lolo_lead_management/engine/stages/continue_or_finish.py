from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.models import ContinueTrace
from lolo_lead_management.domain.enums import RunStatus
from lolo_lead_management.engine.rules import status_after_finish
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.stores import ExplorationMemoryStore, SearchRunStore


class ContinueOrFinishStage:
    def __init__(self, *, run_store: SearchRunStore, memory_store: ExplorationMemoryStore) -> None:
        self._run_store = run_store
        self._memory_store = memory_store
        self.last_trace: ContinueTrace | None = None

    def execute(self, state: EngineRuntimeState) -> None:
        run = state.run
        target_count = run.request.constraints.target_count
        should_finish = False
        reasons: list[str] = []

        if len(run.accepted_leads) >= target_count:
            should_finish = True
            run.completed_reason = "target_count_reached"
            reasons.append("target_count_reached")
        elif not run.budget.can_search():
            should_finish = True
            run.completed_reason = "search_call_budget_exhausted_with_shortlist" if run.shortlist_options else "search_call_budget_exhausted_without_results"
            reasons.append("search_call_budget_exhausted")
        elif state.discovery_ladder_exhausted_in_run and not state.focus_company_locked:
            should_finish = True
            run.completed_reason = "discovery_ladder_exhausted_with_shortlist" if run.shortlist_options else "discovery_ladder_exhausted_without_results"
            reasons.append("discovery_ladder_exhausted")
        elif not run.budget.can_source():
            should_finish = True
            run.completed_reason = "budget_exhausted_with_shortlist" if run.shortlist_options else "budget_exhausted_without_results"
            reasons.append("source_budget_exhausted")

        if should_finish:
            run.status = status_after_finish(
                has_accepted=bool(run.accepted_leads),
                has_errors=bool(run.errors),
                has_shortlist=bool(run.shortlist_options),
            )
            state.memory.consecutive_hard_miss_runs = 0 if run.status != RunStatus.NO_RESULT else state.memory.consecutive_hard_miss_runs + 1
            state.should_continue = False
            reasons.append(f"final_status={run.status.value}")
        else:
            run.status = RunStatus.RUNNING
            state.should_continue = True
            reasons.append("continue_sourcing")

        run.updated_at = datetime.now(timezone.utc)
        self.last_trace = ContinueTrace(
            should_finish=should_finish,
            should_continue=state.should_continue,
            reasons=reasons,
            target_count=target_count,
            accepted_count=len(run.accepted_leads),
            shortlist_count=len(run.shortlist_options),
            source_attempts_used=run.budget.source_attempts_used,
            source_attempt_budget=run.budget.source_attempt_budget,
            enrich_attempts_used=run.budget.enrich_attempts_used,
            enrich_attempt_budget=run.budget.enrich_attempt_budget,
            search_calls_used=run.budget.search_calls_used,
            search_call_budget=run.budget.search_call_budget,
            final_status=run.status,
            completed_reason=run.completed_reason,
        )
        state.current_continue_trace = self.last_trace
        self._memory_store.save_campaign_state(state.memory)
        self._run_store.register_search_run_result(run)
