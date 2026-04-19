from lolo_lead_management.domain.enums import QualificationOutcome, SourcingStatus
from lolo_lead_management.domain.enums import FieldEvidenceStatus, SourceQuality
from lolo_lead_management.domain.models import (
    AssembledFieldEvidence,
    CompanyCandidate,
    EmployeeEvidenceSignal,
    EvidenceItem,
    LeadSearchStartRequest,
    PersonCandidate,
    QualificationDecision,
    ResearchQuery,
    ResearchQueryPlan,
    SourcingDossier,
    WebsiteResolution,
)
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.rules import (
    choose_queries,
    classify_employee_contradiction,
    collect_missing_fields_for_enrichment,
    collect_prioritized_enrichment_needs,
    downgrade_enrich_to_close_match,
)
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.stages.qualify import QualifyStage


class RejectingQualifierLlmPort:
    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, input_payload, schema)
        return {
            "outcome": "REJECT",
            "score": 0,
            "summary": "llm advisory reject",
            "reasons": [],
        }


def test_qualifier_accepts_exact_match() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="CTO"),
        company=CompanyCandidate(name="Acme AI", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=[
            EvidenceItem(
                url="https://acme.ai/about",
                title="about",
                snippet="Laura Martin CTO Acme AI Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
            EvidenceItem(
                url="https://acme.ai/blog",
                title="blog",
                snippet="Acme AI Spain employees 25 automation",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nAutomation and GenAI workflows for IT teams",
            ),
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
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Marta Diaz", role_title="Engineering Manager"),
        company=CompanyCandidate(name="Acme AI", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=[
            EvidenceItem(
                url="https://acme.ai/team",
                title="team",
                snippet="Marta Diaz Engineering Manager Acme AI Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Marta Diaz\nRole: Engineering Manager\nGenAI automation engineering",
            ),
            EvidenceItem(
                url="https://acme.ai/blog",
                title="blog",
                snippet="Acme AI Spain employees 25 automation",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nAutomation and GenAI workflows for engineering teams",
            ),
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
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="CTO"),
        company=CompanyCandidate(name="Acme AI", country_code="fr", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=[
            EvidenceItem(
                url="https://acme.ai/about",
                title="about",
                snippet="Laura Martin CTO Acme AI France",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: France\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
            EvidenceItem(
                url="https://acme.ai/blog",
                title="blog",
                snippet="Acme AI France employees 25 automation",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: France\nEmployees: 25\nAutomation and GenAI workflows for IT teams",
            ),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.REJECT


def test_qualifier_keeps_code_primary_decision_when_llm_review_is_weaker() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://acme.ai/about",
            title="about",
            snippet="Company: Acme AI | Country: Spain | Employees: 25",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nGenAI automation engineering",
            source_tier="tier_a",
            is_company_controlled_source=True,
        )
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name=None, role_title=None),
        company=CompanyCandidate(name="Acme AI", website="https://acme.ai", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://acme.ai", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="role_title", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai, automation", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
        ],
        notes=[],
    )

    stage = QualifyStage(StageAgentExecutor(RejectingQualifierLlmPort()))
    decision = stage.execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ENRICH
    assert stage.last_trace is not None
    assert stage.last_trace.llm_review is not None
    assert "final_verdict=code_primary" in stage.last_trace.notes


def test_qualifier_requires_named_person_for_acceptance_when_requested() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(url="https://acme.ai/about", title="about", snippet="x", source_type="fixture"),
        EvidenceItem(url="https://acme.ai/blog", title="blog", snippet="y", source_type="fixture"),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name=None, role_title="Founder"),
        company=CompanyCandidate(name="Acme AI", website="https://acme.ai", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://acme.ai", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="role_title", value="Founder", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai, automation", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ENRICH


def test_qualifier_downgrades_inferred_role_without_explicit_evidence() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://example.com/profile",
            title="profile",
            snippet="Key person: Luis Claramonte",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nKey person: Luis Claramonte\n",
        ),
        EvidenceItem(url="https://acme.ai/about", title="about", snippet="automation genai", source_type="fixture", raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nGenAI automation\n"),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Luis Claramonte", role_title="Founder"),
        company=CompanyCandidate(name="Acme AI", website="https://acme.ai", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://acme.ai", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[1:], contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value="Luis Claramonte", status=FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_c", support_type="weak_inference", reasoning_note="weak person signal"),
            AssembledFieldEvidence(field_name="role_title", value="Founder", status=FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_c", support_type="weak_inference", reasoning_note="weak role signal"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai, automation", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ENRICH


def test_qualifier_can_accept_with_directory_website_when_other_fields_are_proven() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://directory.example.com/acme-ai",
            title="Acme AI profile",
            snippet="Acme AI company profile",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: Founder\n",
            source_tier="tier_b",
        ),
        EvidenceItem(
            url="https://news.example.com/acme-ai-funding",
            title="Acme AI raises funding",
            snippet="Acme AI funding news",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\n",
            source_tier="tier_c",
        ),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="Founder"),
        company=CompanyCandidate(name="Acme AI", website="https://directory.example.com/acme-ai", country_code="es", employee_estimate=25),
        fit_signals=["genai", "automation"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://directory.example.com/acme-ai", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="weak_inference", reasoning_note="directory profile"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value="Laura Martin", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="role_title", value="Founder", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai, automation", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ACCEPT


def test_qualifier_can_accept_probable_website_when_rest_is_proven() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://x.com/contact",
            title="Contact",
            snippet="Laura Martin Founder",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: Founder\ncontact@x.com",
            source_tier="tier_a",
            is_company_controlled_source=True,
        ),
        EvidenceItem(
            url="https://empresite.eleconomista.es/ACME-AI.html",
            title="Acme AI - Empresite",
            snippet="Web: https://x.com",
            source_type="fixture",
            raw_content="Company: Acme AI\nWebsite: https://x.com\nCountry: Spain\n",
            source_tier="tier_b",
        ),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="Founder"),
        company=CompanyCandidate(name="Acme AI", website="https://x.com", country_code="es", employee_estimate=25),
        website_resolution=WebsiteResolution(
            candidate_website="https://x.com",
            officiality="probable",
            confidence=0.78,
            evidence_urls=[item.url for item in evidence],
            signals=["same-domain company page found", "multiple independent sources reference the same domain"],
            risks=["legal_name_differs_from_brand"],
        ),
        fit_signals=["genai", "automation"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://x.com", status=FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="mixed", support_type="corroborated", reasoning_note="probable"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value="Laura Martin", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="role_title", value="Founder", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai, automation", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ACCEPT


def test_qualifier_accepts_explicit_legal_fallback_when_size_and_person_are_strong() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con software"))
    evidence = [
        EvidenceItem(
            url="https://www.boe.es/borme/dias/2026/01/05/pdfs/BORME-A-2026-1-28.pdf",
            title="BORME Acme AI",
            snippet="Julio Pernia, administrador unico de Acme AI.",
            source_type="fixture",
            raw_content="Empresa: Acme AI. Persona: Julio Pernia. Cargo: Administrador unico. Pais: Spain.",
            source_tier="tier_b",
        ),
        EvidenceItem(
            url="https://www.infoempresa.com/es-es/es/empresa/acme-ai",
            title="Acme AI - Infoempresa",
            snippet="Plantilla: 25 empleados. Software.",
            source_type="fixture",
            raw_content="Empresa: Acme AI. Pais: Spain. Plantilla de 25 empleados. Actividad: software.",
            source_tier="tier_b",
        ),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Julio Pernia", role_title="Administrador unico"),
        company=CompanyCandidate(name="Acme AI", country_code="es", employee_estimate=25),
        lead_source_type="legal_registry",
        person_confidence="strong",
        primary_person_source_url=evidence[0].url,
        fit_signals=["software company"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, source_tier="unknown", support_type="weak_inference", reasoning_note="website unknown"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[1:], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value="Julio Pernia", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="role_title", value="Administrador unico", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="fit_signals", value="software company", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[1:], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ACCEPT


def test_qualifier_can_accept_without_website_when_company_size_and_person_are_proven() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 1 y 50 empleados con software"))
    evidence = [
        EvidenceItem(
            url="https://empresite.eleconomista.es/ACME-AI.html",
            title="ACME AI SL - Empresite",
            snippet="Tiene un total de 3 trabajadores. Zaragoza, Espana.",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nTiene un total de 3 trabajadores.\n",
            source_tier="tier_b",
        ),
        EvidenceItem(
            url="https://www.infoempresa.com/es-es/es/directivo/acme-ai-laura-martin",
            title="Laura Martin - Founder de Acme AI - Infoempresa",
            snippet="Directivo funcional de Acme AI.",
            source_type="fixture",
            raw_content="Company: Acme AI\nPerson: Laura Martin\nRole: Founder\nCountry: Spain\n",
            source_tier="tier_b",
        ),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="Founder"),
        company=CompanyCandidate(name="Acme AI", website=None, country_code="es", employee_estimate=3),
        fit_signals=["software company"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, source_tier="unknown", support_type="weak_inference", reasoning_note="website unresolved"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=3, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="Employee size exact from one explicit public statement."),
            AssembledFieldEvidence(field_name="person_name", value="Laura Martin", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[1:], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="role_title", value="Founder", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[1:], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="fit_signals", value="software company", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ACCEPT


def test_qualifier_can_accept_low_confidence_probable_website_when_other_fields_are_proven() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://empresite.eleconomista.es/ACME-AI.html",
            title="Acme AI - Empresite",
            snippet="Web: https://x.com",
            source_type="fixture",
            raw_content="Company: Acme AI\nWebsite: https://x.com\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: Founder\n",
            source_tier="tier_b",
        ),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="Founder"),
        company=CompanyCandidate(name="Acme AI", website="https://x.com", country_code="es", employee_estimate=25),
        website_resolution=WebsiteResolution(
            candidate_website="https://x.com",
            officiality="probable",
            confidence=0.61,
            evidence_urls=[item.url for item in evidence],
            signals=["directory mentions the candidate website"],
            risks=["single_source_only"],
        ),
        fit_signals=["genai", "automation"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://x.com", status=FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="weak_inference", reasoning_note="probable but weak"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value="Laura Martin", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="role_title", value="Founder", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai, automation", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ACCEPT


def test_collect_missing_fields_for_enrichment_skips_optional_website() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://acme.ai/about",
            title="about",
            snippet="Laura Martin CTO Acme AI Spain",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
        )
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name=None, role_title=None),
        company=CompanyCandidate(name="Acme AI", website=None, country_code="es", employee_estimate=25),
        fit_signals=[],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=25, status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="person_name", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="role_title", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="fit_signals", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
        ],
        notes=[],
    )

    missing = collect_missing_fields_for_enrichment(dossier, request)

    assert "website" in missing
    assert "person_name" in missing
    assert "role_title" in missing
    assert "fit_signals" in missing


def test_collect_prioritized_enrichment_needs_demotes_size_when_in_range_but_weak() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://acme.ai/about",
            title="about",
            snippet="Acme AI Spain",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 9\n",
            source_tier="tier_a",
        )
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name=None, role_title=None),
        company=CompanyCandidate(name="Acme AI", website=None, country_code="es", employee_estimate=9),
        fit_signals=[],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(
                field_name="employee_estimate",
                value=9,
                status=FieldEvidenceStatus.WEAKLY_SUPPORTED,
                supporting_evidence=evidence,
                contradicting_evidence=[],
                employee_signals=[
                    EmployeeEvidenceSignal(kind="exact", min_value=9, max_value=9, company_specific=True, strength="weak", source_url=evidence[0].url, evidence_excerpt="Employees: 9"),
                ],
                source_quality=SourceQuality.MEDIUM,
                reasoning_note="weak size hint",
            ),
            AssembledFieldEvidence(field_name="person_name", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="role_title", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="fit_signals", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
        ],
        notes=[],
    )

    needs = collect_prioritized_enrichment_needs(dossier, request)

    assert [item.field_name for item in needs[:2]] == ["person_name", "role_title"]
    size_need = next(item for item in needs if item.field_name == "employee_estimate")
    assert size_need.priority_class == "medium"
    assert size_need.max_queries_for_this_pass == 1


def test_choose_queries_respects_enrichment_field_caps() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    needs = collect_prioritized_enrichment_needs(
        SourcingDossier(
            sourcing_status=SourcingStatus.FOUND,
            company=CompanyCandidate(name="Tauniqo Ai S.L", country_code="es", employee_estimate=9),
            person=PersonCandidate(full_name=None, role_title=None),
            fit_signals=[],
            evidence=[],
            field_evidence=[
                AssembledFieldEvidence(field_name="company_name", value="Tauniqo Ai S.L", status=FieldEvidenceStatus.SATISFIED, reasoning_note="ok"),
                AssembledFieldEvidence(field_name="website", value=None, status=FieldEvidenceStatus.UNKNOWN, reasoning_note="missing"),
                AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, reasoning_note="ok"),
                AssembledFieldEvidence(
                    field_name="employee_estimate",
                    value=9,
                    status=FieldEvidenceStatus.WEAKLY_SUPPORTED,
                    employee_signals=[EmployeeEvidenceSignal(kind="exact", min_value=9, max_value=9, company_specific=True, strength="weak")],
                    reasoning_note="weak size hint",
                ),
                AssembledFieldEvidence(field_name="person_name", value=None, status=FieldEvidenceStatus.UNKNOWN, reasoning_note="missing"),
                AssembledFieldEvidence(field_name="role_title", value=None, status=FieldEvidenceStatus.UNKNOWN, reasoning_note="missing"),
                AssembledFieldEvidence(field_name="fit_signals", value=None, status=FieldEvidenceStatus.UNKNOWN, reasoning_note="missing"),
            ],
        ),
        request,
    )
    plan = ResearchQueryPlan(
        planned_queries=[
            ResearchQuery(query='infoempresa "Tauniqo Ai S.L" empleados plantilla', objective="size", research_phase="evidence_closing", source_role="employee_count_resolution", candidate_company_name="Tauniqo Ai S.L", expected_field="employee_estimate"),
            ResearchQuery(query='einforma "Tauniqo Ai S.L" empleados', objective="size corroboration", research_phase="evidence_closing", source_role="employee_count_resolution", candidate_company_name="Tauniqo Ai S.L", expected_field="employee_estimate"),
            ResearchQuery(query='"Tauniqo Ai S.L" administradores cargos directivos', objective="person", research_phase="evidence_closing", source_role="governance_resolution", candidate_company_name="Tauniqo Ai S.L", expected_field="person_name"),
            ResearchQuery(query='"Tauniqo Ai S.L" CTO', objective="role", research_phase="evidence_closing", source_role="governance_resolution", candidate_company_name="Tauniqo Ai S.L", expected_field="role_title"),
        ]
    )
    selected, diagnostics = choose_queries(
        plan,
        [],
        limit=3,
        prioritized_needs=needs,
        return_diagnostics=True,
    )

    selected_fields = [item.expected_field for item in selected]
    assert selected_fields.count("employee_estimate") == 1
    assert "person_name" in selected_fields
    assert "role_title" in selected_fields
    assert diagnostics["selected_field_coverage"]["employee_estimate"] == 1


def test_classify_employee_contradiction_marks_in_range_non_overlap_as_moderate() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    field = AssembledFieldEvidence(
        field_name="employee_estimate",
        value=9,
        status=FieldEvidenceStatus.CONTRADICTED,
        employee_signals=[
            EmployeeEvidenceSignal(kind="exact", min_value=9, max_value=9, company_specific=True, strength="medium", source_url="https://a.test"),
            EmployeeEvidenceSignal(kind="range", min_value=25, max_value=50, company_specific=True, strength="weak", source_url="https://b.test"),
        ],
        reasoning_note="contradiction",
    )

    contradiction_class, contradiction_reason = classify_employee_contradiction(field, request)

    assert contradiction_class == "moderate"
    assert contradiction_reason is not None


def test_qualifier_keeps_size_contradiction_in_enrich_when_all_signals_stay_in_band() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://a.test/acme",
            title="Acme directory",
            snippet="Employees 9",
            source_type="fixture",
            raw_content="Employees: 9",
            source_tier="tier_b",
        ),
        EvidenceItem(
            url="https://b.test/acme",
            title="Acme profile",
            snippet="Employees 25-50",
            source_type="fixture",
            raw_content="Plantilla: Entre 25 y 50 empleados",
            source_tier="tier_b",
        ),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name=None, role_title=None),
        company=CompanyCandidate(name="Acme AI", country_code="es", employee_estimate=9),
        fit_signals=["genai"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://acme.ai", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, reasoning_note="ok"),
            AssembledFieldEvidence(
                field_name="employee_estimate",
                value=9,
                status=FieldEvidenceStatus.CONTRADICTED,
                supporting_evidence=evidence[:1],
                contradicting_evidence=evidence[1:],
                employee_signals=[
                    EmployeeEvidenceSignal(kind="exact", min_value=9, max_value=9, company_specific=True, strength="medium", source_url=evidence[0].url),
                    EmployeeEvidenceSignal(kind="range", min_value=25, max_value=50, company_specific=True, strength="weak", source_url=evidence[1].url),
                ],
                source_quality=SourceQuality.MEDIUM,
                reasoning_note="conflicting size sources",
            ),
            AssembledFieldEvidence(field_name="person_name", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="role_title", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )

    assert decision.outcome == QualificationOutcome.ENRICH
    assert decision.qualification_rubric is not None
    assert decision.qualification_rubric.employee_contradiction_class == "moderate"


def test_downgrade_enrich_to_close_match_after_budget_exhaustion() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    evidence = [
        EvidenceItem(
            url="https://acme.ai/about",
            title="Acme AI about",
            snippet="Acme AI Spain",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\n",
            source_tier="tier_a",
        ),
        EvidenceItem(
            url="https://acme.ai/contact",
            title="Acme AI contact",
            snippet="Acme AI contact",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\n",
            source_tier="tier_a",
        ),
        EvidenceItem(
            url="https://directory.example.com/acme-ai",
            title="Acme AI profile",
            snippet="Laura Martin Founder",
            source_type="fixture",
            raw_content="Company: Acme AI\nCountry: Spain\nPerson: Laura Martin\nRole: Founder\n",
            source_tier="tier_b",
        ),
    ]
    dossier = SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        person=PersonCandidate(full_name="Laura Martin", role_title="Founder"),
        company=CompanyCandidate(name="Acme AI", website="https://acme.ai", country_code="es", employee_estimate=None),
        fit_signals=["genai", "automation"],
        evidence=evidence,
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Acme AI", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:1], contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="website", value="https://acme.ai", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[:2], contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="tier_a", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="country", value="es", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.HIGH, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="employee_estimate", value=None, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, source_tier="unknown", support_type="weak_inference", reasoning_note="missing"),
            AssembledFieldEvidence(field_name="person_name", value="Laura Martin", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[2:], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="explicit", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="role_title", value="Founder", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence[2:], contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="tier_b", support_type="corroborated", reasoning_note="ok"),
            AssembledFieldEvidence(field_name="fit_signals", value="genai, automation", status=FieldEvidenceStatus.SATISFIED, supporting_evidence=evidence, contradicting_evidence=[], source_quality=SourceQuality.MEDIUM, source_tier="mixed", support_type="corroborated", reasoning_note="ok"),
        ],
        notes=[],
    )

    decision = QualifyStage(StageAgentExecutor(None)).execute(
        request_payload=request.model_dump(mode="json"),
        dossier_payload=dossier.model_dump(mode="json"),
    )
    downgraded = downgrade_enrich_to_close_match(decision, dossier, request)

    assert decision.outcome == QualificationOutcome.ENRICH
    assert downgraded.outcome == QualificationOutcome.REJECT_CLOSE_MATCH
