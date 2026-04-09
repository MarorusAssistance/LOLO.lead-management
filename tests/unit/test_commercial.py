from lolo_lead_management.domain.enums import QualificationOutcome, SourcingStatus
from lolo_lead_management.domain.models import CompanyCandidate, EvidenceItem, LeadSearchStartRequest, PersonCandidate, QualificationDecision, SourcingDossier
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.draft import DraftStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage


def test_commercial_stage_generates_bundle_without_llm() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
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
    qualification = QualificationDecision(
        outcome=QualificationOutcome.ACCEPT,
        score=90,
        summary="Candidate is a strong exact match.",
        reasons=["test"],
        type="CTO",
        region="es",
        match_type="exact",
    )

    bundle = DraftStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
        qualification_payload=qualification.model_dump(mode="json"),
    )

    assert "Acme AI" in bundle.email_subject
    assert bundle.dm_draft
    assert bundle.connection_note_draft
