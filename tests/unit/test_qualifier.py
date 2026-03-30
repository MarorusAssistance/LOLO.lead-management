from lolo_lead_management.domain.enums import QualificationOutcome, SourcingStatus
from lolo_lead_management.domain.models import CompanyCandidate, EvidenceItem, LeadSearchStartRequest, PersonCandidate, SourcingDossier
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.stages.qualify import QualifyStage


def test_qualifier_accepts_exact_match() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai"))
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="CTO"),
        company=CompanyCandidate(name="Acme AI", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=[
            EvidenceItem(url="https://acme.ai/about", title="about", snippet="x", source_type="fixture"),
            EvidenceItem(url="https://acme.ai/blog", title="blog", snippet="y", source_type="fixture"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ACCEPT


def test_qualifier_marks_close_match() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai"))
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Marta Diaz", role_title="Engineering Manager"),
        company=CompanyCandidate(name="Acme AI", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=[
            EvidenceItem(url="https://acme.ai/about", title="about", snippet="x", source_type="fixture"),
            EvidenceItem(url="https://acme.ai/blog", title="blog", snippet="y", source_type="fixture"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.REJECT_CLOSE_MATCH


def test_qualifier_rejects_hard_country_miss() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai"))
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="CTO"),
        company=CompanyCandidate(name="Acme AI", country_code="fr", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=[
            EvidenceItem(url="https://acme.ai/about", title="about", snippet="x", source_type="fixture"),
            EvidenceItem(url="https://acme.ai/blog", title="blog", snippet="y", source_type="fixture"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.REJECT
