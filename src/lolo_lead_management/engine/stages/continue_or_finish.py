from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.enums import RunStatus
from lolo_lead_management.engine.rules import status_after_finish
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.stores import ExplorationMemoryStore, SearchRunStore


class ContinueOrFinishStage:
    def __init__(self, *, run_store: SearchRunStore, memory_store: ExplorationMemoryStore) -> None:
        self._run_store = run_store
        self._memory_store = memory_store

    def execute(self, state: EngineRuntimeState) -> None:
        run = state.run
        target_count = run.request.constraints.target_count
        should_finish = False

        if len(run.accepted_leads) >= target_count:
            should_finish = True
            run.completed_reason = "target_count_reached"
        elif not run.budget.can_source():
            should_finish = True
            run.completed_reason = "budget_exhausted_with_shortlist" if run.shortlist_options else "budget_exhausted_without_results"

        if should_finish:
            run.status = status_after_finish(
                has_accepted=bool(run.accepted_leads),
                has_errors=bool(run.errors),
                has_shortlist=bool(run.shortlist_options),
            )
            state.memory.consecutive_hard_miss_runs = 0 if run.status != RunStatus.NO_RESULT else state.memory.consecutive_hard_miss_runs + 1
            state.should_continue = False
        else:
            run.status = RunStatus.RUNNING
            state.should_continue = True

        run.updated_at = datetime.now(timezone.utc)
        self._memory_store.save_campaign_state(state.memory)
        self._run_store.register_search_run_result(run)
