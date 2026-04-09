from lolo_lead_management.domain.models import LeadSearchStartRequest
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.normalize import NormalizeStage


def test_normalizer_extracts_core_constraints() -> None:
    stage = NormalizeStage(StageAgentExecutor(None))
    result = stage.execute(
        LeadSearchStartRequest(
            user_text="busca 3 leads que trabajen en espana y esten en empresas de entre 5 y 50 empleados",
            meta={"source": "gateway", "language": "es"},
        )
    )

    assert result.constraints.target_count == 3
    assert result.constraints.preferred_country == "es"
    assert result.constraints.min_company_size == 5
    assert result.constraints.max_company_size == 50
    assert "ceo" in result.buyer_targets
    assert result.user_text.startswith("busca 3 leads")


def test_normalizer_extracts_accented_spanish_min_size_and_country() -> None:
    stage = NormalizeStage(StageAgentExecutor(None))
    result = stage.execute(
        LeadSearchStartRequest(
            user_text="busca 1 founder o CTO de una empresa española de IA o software con más de 50 empleados",
            meta={"source": "gateway", "language": "es"},
        )
    )

    assert result.constraints.target_count == 1
    assert result.constraints.preferred_country == "es"
    assert result.constraints.min_company_size == 50
    assert result.constraints.max_company_size is None
