from __future__ import annotations

from lolo_lead_management.domain.models import StageDecision
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.rules import decide_planner_action
from lolo_lead_management.engine.state import EngineRuntimeState


class PlanStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor
        _ = agent_executor

    def execute(self, state: EngineRuntimeState) -> StageDecision:
        return decide_planner_action(
            accepted_count=len(state.run.accepted_leads),
            target_count=state.run.request.constraints.target_count,
            budget=state.run.budget,
            shortlist_count=len(state.run.shortlist_options),
        )
