from lolo_lead_management.domain.models import LeadSearchStartRequest
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.normalize import NormalizeStage


def test_normalizer_extracts_core_constraints() -> None:
    stage = NormalizeStage(StageAgentExecutor(None))
    result = stage.execute(
        LeadSearchStartRequest(
            user_text="busca 3 leads que trabajen en españa y esten en empresas de entre 5 y 50 empleados",
            meta={"source": "gateway", "language": "es"},
        )
    )

    assert result.constraints.target_count == 3
    assert result.constraints.preferred_country == "es"
    assert result.constraints.min_company_size == 5
    assert result.constraints.max_company_size == 50
    assert "ceo" in result.buyer_targets
    assert result.user_text.startswith("busca 3 leads")
