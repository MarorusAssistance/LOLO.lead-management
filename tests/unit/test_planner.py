from lolo_lead_management.domain.enums import PlannerAction
from lolo_lead_management.domain.models import SearchBudget
from lolo_lead_management.engine.rules import decide_planner_action


def test_planner_finishes_when_target_reached() -> None:
    decision = decide_planner_action(
        accepted_count=3,
        target_count=3,
        budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1),
        shortlist_count=0,
    )
    assert decision.action == PlannerAction.FINISH_ACCEPTED


def test_planner_returns_shortlist_when_budget_exhausted() -> None:
    budget = SearchBudget(source_attempt_budget=1, enrich_attempt_budget=1, source_attempts_used=1)
    decision = decide_planner_action(
        accepted_count=0,
        target_count=1,
        budget=budget,
        shortlist_count=2,
    )
    assert decision.action == PlannerAction.FINISH_SHORTLIST
