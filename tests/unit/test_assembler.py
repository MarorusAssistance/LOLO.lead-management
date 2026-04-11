from urllib.parse import urlparse

from lolo_lead_management.domain.enums import FieldEvidenceStatus, SourcingStatus
from lolo_lead_management.domain.models import ChunkExtractionResolution, CompanyFocusResolution, EvidenceDocument, ExplorationMemoryState, LeadSearchStartRequest, ResearchQuery, ResearchTraceEntry, SearchRunSnapshot, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.rules import enrich_document_metadata
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.state import EngineRuntimeState


def _legacy_resolution_to_chunk_response(response: dict, *, input_payload: dict) -> dict:
    if input_payload.get("mode") in {"discovery_candidate_document_mode", "discovery_candidate_chunk_mode"}:
        if "discovery_candidates" in response:
            return {
                "segment_company_name": response.get("segment_company_name"),
                "discovery_candidates": response.get("discovery_candidates", []),
                "notes": response.get("notes", []),
            }
        return {"segment_company_name": None, "discovery_candidates": [], "notes": response.get("notes", [])}
    if input_payload.get("mode") not in {"focus_locked_chunk_mode", "focus_locked_document_mode"}:
        return response
    if "contact_assertions" in response:
        return response

    subject_company = (
        response.get("segment_company_name")
        or response.get("subject_company_name")
        or response.get("selected_company")
        or input_payload.get("focus_company")
    )
    chunk_text = (
        ((input_payload.get("chunk") or {}).get("text") or input_payload.get("document_text") or "")
    ).lower()
    document_url = (input_payload.get("document") or {}).get("url") or ""
    field_assertions: list[dict] = []
    contact_assertions: list[dict] = []

    def _same_domain(candidate_website: str | None) -> bool:
        if not candidate_website or not document_url:
            return False
        return urlparse(candidate_website).netloc == urlparse(document_url).netloc

    def add_field(field_name: str, value, *, company_name: str | None = subject_company, status: str | None = None, support_type: str | None = None, reasoning_note: str = "") -> None:
        if value is None:
            return
        if field_name == "country" and value in {"spain", "españa", "espana"}:
            value = "es"
        field_assertions.append(
            {
                "field_name": field_name,
                "company_name": company_name,
                "value": value,
                "status": status or FieldEvidenceStatus.SATISFIED.value,
                "support_type": support_type or "explicit",
                "reasoning_note": reasoning_note or "legacy_fixture",
            }
        )

    add_field("company_name", response.get("subject_company_name"))
    website_value = response.get("website") or response.get("candidate_website")
    if website_value and (str(website_value).lower() in chunk_text or _same_domain(str(website_value))):
        add_field("website", website_value)
    add_field("country", response.get("country_code"))
    add_field("employee_estimate", response.get("employee_estimate"))

    for item in response.get("field_assertions", []):
        field_name = item.get("field_name")
        value = item.get("value")
        if field_name in {"company_name", "website", "country", "employee_estimate"}:
            if field_name == "website" and value and not (str(value).lower() in chunk_text or _same_domain(str(value))):
                continue
            add_field(
                field_name,
                value,
                company_name=subject_company,
                status=item.get("status"),
                support_type=item.get("support_type"),
                reasoning_note=item.get("reasoning_note") or "",
            )

    person_name = response.get("person_name")
    role_title = response.get("role_title")
    field_map = {item.get("field_name"): item for item in response.get("field_assertions", [])}
    person_field = field_map.get("person_name", {})
    role_field = field_map.get("role_title", {})
    if person_name is None:
        person_name = person_field.get("value")
    if role_title is None:
        role_title = role_field.get("value")
    person_status = person_field.get("status")
    role_status = role_field.get("status")
    if (
        person_name
        and role_title
        and (
            (
                person_status == FieldEvidenceStatus.SATISFIED.value
                and role_status == FieldEvidenceStatus.SATISFIED.value
            )
            or (not person_field and not role_field)
        )
    ):
        contact_assertions.append(
            {
                "person_name": person_name,
                "role_title": role_title,
                "company_name": subject_company,
                "status": person_status or role_status or FieldEvidenceStatus.SATISFIED.value,
                "support_type": person_field.get("support_type") or role_field.get("support_type") or "explicit",
                "reasoning_note": person_field.get("reasoning_note") or role_field.get("reasoning_note") or "legacy_fixture",
            }
        )

    return {
        "segment_company_name": subject_company,
        "field_assertions": field_assertions,
        "contact_assertions": contact_assertions,
        "fit_signals": response.get("fit_signals", []),
        "contradictions": response.get("contradictions", []),
        "notes": response.get("notes", []),
    }


class FakeAssemblerLlmPort:
    def __init__(self, response: dict) -> None:
        self._response = response

    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, input_payload, schema)
        return _legacy_resolution_to_chunk_response(self._response, input_payload=input_payload)


class SequentialAssemblerLlmPort:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, input_payload, schema)
        self.calls += 1
        return _legacy_resolution_to_chunk_response(self._responses.pop(0), input_payload=input_payload)


class TimeoutAssemblerLlmPort:
    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, input_payload, schema)
        raise TimeoutError("timed out")


class ChunkAwareAssemblerLlmPort:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, schema)
        self.calls.append(input_payload["mode"])
        if input_payload["mode"] == "discovery_candidate_document_mode":
            return {
                "segment_company_name": "Acme AI",
                "discovery_candidates": [
                    {
                        "company_name": "Acme AI",
                        "legal_name": "Acme AI SL",
                        "query_name": "Acme AI",
                        "brand_aliases": ["Acme AI"],
                        "country_code": "es",
                        "location_hint": "Madrid",
                        "theme_tags": ["software", "ia"],
                        "candidate_website": "https://acme.ai",
                        "employee_count_hint_value": 25,
                        "employee_count_hint_type": "exact",
                        "operational_status": "active",
                        "support_type": "explicit",
                        "evidence_excerpt": "Company: Acme AI",
                        "evidence_urls": [input_payload["document"]["url"]],
                        "is_real_company_candidate": True,
                        "rejection_reason": None,
                    }
                ],
                "notes": ["document_candidates_ok"],
            }
        if input_payload["mode"] == "discovery_candidate_chunk_mode":
            return {
                "segment_company_name": "Acme AI",
                "discovery_candidates": [
                    {
                        "company_name": "Acme AI",
                        "legal_name": "Acme AI SL",
                        "query_name": "Acme AI",
                        "brand_aliases": ["Acme AI"],
                        "country_code": "es",
                        "location_hint": "Madrid",
                        "theme_tags": ["software", "ia"],
                        "candidate_website": "https://acme.ai",
                        "employee_count_hint_value": 25,
                        "employee_count_hint_type": "exact",
                        "operational_status": "active",
                        "support_type": "explicit",
                        "evidence_excerpt": "Company: Acme AI",
                        "evidence_urls": [input_payload["document"]["url"]],
                        "is_real_company_candidate": True,
                        "rejection_reason": None,
                    }
                ],
                "notes": ["chunk_candidates_ok"],
            }
        if input_payload["mode"] == "focus_locked_chunk_mode":
            return {
                "segment_company_name": "Acme AI",
                "field_assertions": [
                    {
                        "field_name": "company_name",
                        "company_name": "Acme AI",
                        "value": "Acme AI",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "segment company",
                    },
                    {
                        "field_name": "website",
                        "company_name": "Acme AI",
                        "value": "https://acme.ai",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "segment website",
                    },
                    {
                        "field_name": "country",
                        "company_name": "Acme AI",
                        "value": "es",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "segment country",
                    },
                    {
                        "field_name": "employee_estimate",
                        "company_name": "Acme AI",
                        "value": 25,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "segment size",
                        "employee_count_type": "exact",
                    },
                ],
                "contact_assertions": [
                    {
                        "person_name": "Laura Martin",
                        "role_title": "CTO",
                        "company_name": "Acme AI",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "segment contact",
                    }
                ],
                "fit_signals": ["genai"],
                "contradictions": [],
                "notes": ["chunk_ok"],
            }
        if input_payload["mode"] == "focus_locked_document_mode":
            return {
                "segment_company_name": "Acme AI",
                "field_assertions": [
                    {
                        "field_name": "company_name",
                        "company_name": "Acme AI",
                        "value": "Acme AI",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "document company",
                    },
                    {
                        "field_name": "website",
                        "company_name": "Acme AI",
                        "value": "https://acme.ai",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "document website",
                    },
                    {
                        "field_name": "country",
                        "company_name": "Acme AI",
                        "value": "es",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "document country",
                    },
                    {
                        "field_name": "employee_estimate",
                        "company_name": "Acme AI",
                        "value": 25,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "document size",
                        "employee_count_type": "exact",
                    },
                ],
                "contact_assertions": [
                    {
                        "person_name": "Laura Martin",
                        "role_title": "CTO",
                        "company_name": "Acme AI",
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "document contact",
                    }
                ],
                "fit_signals": ["genai"],
                "contradictions": [],
                "notes": ["document_ok"],
            }
        raise AssertionError(f"unexpected assembler mode: {input_payload['mode']}")


def test_assembler_picks_subject_company_not_publisher() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        executed_queries=[
            ResearchQuery(
                query="Spain startup AI software company",
                objective="Find plausible target companies.",
                research_phase="company_discovery",
            )
        ],
        documents=[
            EvidenceDocument(
                url="https://techcrunch.com/2026/03/01/acme-ai-raises-seed/",
                title="Acme AI raises seed round",
                snippet="Acme AI, a Spanish GenAI startup, raised funding.",
                source_type="fixture",
                raw_content="Acme AI is a Spanish startup. Company: Acme AI. Country: Spain. Funding and automation focus.",
            ),
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
        ],
        research_trace=[
            ResearchTraceEntry(
                query_planned="Spain startup AI software company",
                query_executed="Spain startup AI software company",
                research_phase="company_discovery",
                objective="Find plausible target companies.",
                documents_considered=2,
                documents_selected=2,
                selected_urls=[
                    "https://techcrunch.com/2026/03/01/acme-ai-raises-seed/",
                    "https://acme.ai/about",
                ],
            )
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
    )

    dossier = AssembleStage(StageAgentExecutor(None)).execute(state)

    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    assert dossier.company.name != "TechCrunch"
    assert dossier.person is not None
    assert dossier.person.full_name == "Laura Martin"


def test_assembler_llm_resolution_builds_dossier_without_fallback() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        executed_queries=[
            ResearchQuery(
                query="Spain startup AI software company",
                objective="Find plausible target companies.",
                research_phase="company_discovery",
            )
        ],
        documents=[
            EvidenceDocument(
                url="https://techcrunch.com/2026/03/01/acme-ai-raises-seed/",
                title="Acme AI raises seed round",
                snippet="Acme AI, a Spanish GenAI startup, raised funding.",
                source_type="fixture",
                raw_content="Acme AI is a Spanish startup. Company: Acme AI. Country: Spain. Funding and automation focus.",
            ),
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
        ],
        research_trace=[
            ResearchTraceEntry(
                query_planned="Spain startup AI software company",
                query_executed="Spain startup AI software company",
                research_phase="company_discovery",
                objective="Find plausible target companies.",
                documents_considered=2,
                documents_selected=2,
                selected_urls=[
                    "https://techcrunch.com/2026/03/01/acme-ai-raises-seed/",
                    "https://acme.ai/about",
                ],
            )
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
    )
    llm = FakeAssemblerLlmPort(
        {
            "subject_company_name": "Acme AI",
            "website": "https://acme.ai",
            "country_code": "es",
            "employee_estimate": 25,
            "person_name": "Laura Martin",
            "role_title": "CTO",
            "fit_signals": ["genai", "automation"],
            "selected_evidence_urls": [
                "https://acme.ai/about",
                "https://techcrunch.com/2026/03/01/acme-ai-raises-seed/",
            ],
            "field_assertions": [
                {
                    "field_name": "company_name",
                    "value": "Acme AI",
                    "status": "satisfied",
                    "evidence_urls": ["https://acme.ai/about", "https://techcrunch.com/2026/03/01/acme-ai-raises-seed/"],
                    "contradicting_urls": [],
                    "reasoning_note": "Both documents describe Acme AI as the subject company.",
                },
                {
                    "field_name": "website",
                    "value": "https://acme.ai",
                    "status": "satisfied",
                    "evidence_urls": ["https://acme.ai/about"],
                    "contradicting_urls": [],
                    "reasoning_note": "The official site is directly present in the company-controlled page.",
                },
                {
                    "field_name": "country",
                    "value": "es",
                    "status": "satisfied",
                    "evidence_urls": ["https://acme.ai/about", "https://techcrunch.com/2026/03/01/acme-ai-raises-seed/"],
                    "contradicting_urls": [],
                    "reasoning_note": "Both documents tie the company to Spain.",
                },
                {
                    "field_name": "employee_estimate",
                    "value": 25,
                    "status": "satisfied",
                    "evidence_urls": ["https://acme.ai/about"],
                    "contradicting_urls": [],
                    "reasoning_note": "The about page explicitly states the employee count.",
                },
                {
                    "field_name": "person_name",
                    "value": "Laura Martin",
                    "status": "satisfied",
                    "evidence_urls": ["https://acme.ai/about"],
                    "contradicting_urls": [],
                    "reasoning_note": "The about page names Laura Martin.",
                },
                {
                    "field_name": "role_title",
                    "value": "CTO",
                    "status": "satisfied",
                    "evidence_urls": ["https://acme.ai/about"],
                    "contradicting_urls": [],
                    "reasoning_note": "The about page ties Laura Martin to the CTO role.",
                },
            ],
            "contradictions": [],
            "unresolved_fields": [],
            "notes": ["assembler_resolution_complete"],
        }
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    assert dossier.company.website == "https://acme.ai"
    assert dossier.company.country_code == "es"
    assert dossier.company.employee_estimate == 25
    assert dossier.person is not None
    assert dossier.person.full_name == "Laura Martin"
    assert dossier.person.role_title == "CTO"
    assert "assembled_by_grounded_segment_merge" in dossier.notes
    assert state.current_assembler_trace is not None
    assert state.current_assembler_trace["used_fallback"] is False
    assert state.current_assembler_trace["status"] == "ok"
    assert state.current_assembler_trace["input_documents"]
    assert state.current_assembler_trace["document_steps"]
    assert state.current_assembler_trace["document_steps"][0]["llm_input_payload"] is not None
    assert state.current_assembler_trace["final_dossier_after_overlay"]["company"]["name"] == "Acme AI"


def test_assembler_processes_documents_incrementally() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        executed_queries=[
            ResearchQuery(
                query="Spain startup AI software company",
                objective="Find plausible target companies.",
                research_phase="company_discovery",
            )
        ],
        documents=[
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nGenAI automation engineering",
            ),
            EvidenceDocument(
                url="https://acme.ai/team",
                title="Acme AI team",
                snippet="Laura Martin CTO at Acme AI",
                source_type="fixture",
                raw_content="Company: Acme AI\nPerson: Laura Martin\nRole: CTO\nLeadership team",
            ),
        ],
        research_trace=[
            ResearchTraceEntry(
                query_planned="Spain startup AI software company",
                query_executed="Spain startup AI software company",
                research_phase="company_discovery",
                objective="Find plausible target companies.",
                documents_considered=2,
                documents_selected=2,
                selected_urls=["https://acme.ai/about", "https://acme.ai/team"],
            )
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
    )
    llm = SequentialAssemblerLlmPort(
        [
            {
                "subject_company_name": "Acme AI",
                "website": "https://acme.ai",
                "country_code": "es",
                "employee_estimate": 25,
                "person_name": None,
                "role_title": None,
                "fit_signals": ["genai", "automation"],
                "selected_evidence_urls": ["https://acme.ai/about"],
                "field_assertions": [
                    {
                        "field_name": "company_name",
                        "value": "Acme AI",
                        "status": "satisfied",
                        "evidence_urls": ["https://acme.ai/about"],
                        "contradicting_urls": [],
                        "reasoning_note": "About page identifies the company.",
                    }
                ],
                "contradictions": [],
                "unresolved_fields": ["person_name", "role_title"],
                "notes": ["page_1"],
            },
            {
                "subject_company_name": "Acme AI",
                "website": None,
                "country_code": None,
                "employee_estimate": None,
                "person_name": "Laura Martin",
                "role_title": "CTO",
                "fit_signals": ["genai", "automation"],
                "selected_evidence_urls": ["https://acme.ai/team"],
                "field_assertions": [
                    {
                        "field_name": "person_name",
                        "value": "Laura Martin",
                        "status": "satisfied",
                        "evidence_urls": ["https://acme.ai/team"],
                        "contradicting_urls": [],
                        "reasoning_note": "Team page names Laura Martin.",
                    },
                    {
                        "field_name": "role_title",
                        "value": "CTO",
                        "status": "satisfied",
                        "evidence_urls": ["https://acme.ai/team"],
                        "contradicting_urls": [],
                        "reasoning_note": "Team page ties Laura Martin to the CTO role.",
                    },
                ],
                "contradictions": [],
                "unresolved_fields": [],
                "notes": ["page_2"],
            },
        ]
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert llm.calls == 2
    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    assert dossier.person is not None
    assert dossier.person.full_name == "Laura Martin"
    assert dossier.person.role_title == "CTO"
    assert state.current_assembler_trace is not None
    assert len(state.current_assembler_trace["document_steps"]) == 2


def test_assembler_rejects_directory_as_official_website() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Narrativa",
        documents=[
            EvidenceDocument(
                url="https://startupshub.example.com/narrativa",
                title="Narrativa profile",
                snippet="Narrativa company profile",
                source_type="fixture",
                raw_content="Company: Narrativa\nCountry: Spain\nEmployees: 25\nWebsite: startupshub.example.com/narrativa",
            ),
            EvidenceDocument(
                url="https://www.narrativa.com/about",
                title="Narrativa about",
                snippet="Narrativa official site",
                source_type="fixture",
                raw_content="Company: Narrativa\nCountry: Spain\nAbout Narrativa\n",
                source_tier="tier_a",
                is_company_controlled_source=True,
            ),
        ],
    )
    state = EngineRuntimeState(run=SearchRunSnapshot(request=request), memory=ExplorationMemoryState(), current_source_result=source_result)
    llm = FakeAssemblerLlmPort(
        {
            "subject_company_name": "Narrativa",
            "website": "https://startupshub.example.com/narrativa",
            "country_code": "es",
            "employee_estimate": None,
            "person_name": None,
            "role_title": None,
            "fit_signals": ["genai"],
            "selected_evidence_urls": ["https://startupshub.example.com/narrativa", "https://www.narrativa.com/about"],
            "field_assertions": [
                {
                    "field_name": "company_name",
                    "value": "Narrativa",
                    "status": "satisfied",
                    "evidence_urls": ["https://www.narrativa.com/about"],
                    "contradicting_urls": [],
                    "reasoning_note": "ok",
                },
                {
                    "field_name": "website",
                    "value": "https://startupshub.example.com/narrativa",
                    "status": "satisfied",
                    "evidence_urls": ["https://startupshub.example.com/narrativa"],
                    "contradicting_urls": [],
                    "source_tier": "tier_b",
                    "support_type": "weak_inference",
                    "reasoning_note": "directory profile",
                },
            ],
        }
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.company is not None
    assert dossier.company.website == "https://www.narrativa.com"
    assert dossier.website_resolution is not None
    assert dossier.website_resolution.officiality == "confirmed"
    assert dossier.website_resolution.candidate_website == "https://www.narrativa.com"


def test_assembler_keeps_directory_only_candidate_as_unknown_website() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme Analytics",
        documents=[
            EvidenceDocument(
                url="https://empresite.eleconomista.es/ACME-ANALYTICS.html",
                title="Acme Analytics - Empresite",
                snippet="Web: https://acme-analytics.es",
                source_type="fixture",
                raw_content="Company: Acme Analytics\nWebsite: https://acme-analytics.es\nCountry: Spain\n",
                source_tier="tier_b",
            ),
        ],
    )
    state = EngineRuntimeState(run=SearchRunSnapshot(request=request), memory=ExplorationMemoryState(), current_source_result=source_result)
    llm = FakeAssemblerLlmPort(
        {
            "subject_company_name": "Acme Analytics",
            "candidate_website": "https://acme-analytics.es",
            "website_officiality": "probable",
            "website_confidence": 0.64,
            "website_evidence_urls": ["https://empresite.eleconomista.es/ACME-ANALYTICS.html"],
            "website_signals": ["directory mentions the candidate website"],
            "website_risks": ["single_source_only"],
            "selected_evidence_urls": ["https://empresite.eleconomista.es/ACME-ANALYTICS.html"],
            "field_assertions": [
                {
                    "field_name": "company_name",
                    "value": "Acme Analytics",
                    "status": "satisfied",
                    "evidence_urls": ["https://empresite.eleconomista.es/ACME-ANALYTICS.html"],
                    "contradicting_urls": [],
                    "reasoning_note": "ok",
                },
                {
                    "field_name": "website",
                    "value": "https://acme-analytics.es",
                    "status": "weakly_supported",
                    "evidence_urls": ["https://empresite.eleconomista.es/ACME-ANALYTICS.html"],
                    "contradicting_urls": [],
                    "source_tier": "tier_b",
                    "support_type": "weak_inference",
                    "reasoning_note": "directory only",
                },
            ],
        }
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.company is not None
    assert dossier.company.website is None
    assert dossier.website_resolution is not None
    assert dossier.website_resolution.candidate_website == "https://acme-analytics.es"
    assert dossier.website_resolution.officiality == "unknown"


def test_assembler_rejects_product_copy_as_role_title() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Narrativa",
        documents=[
            EvidenceDocument(
                url="https://www.narrativa.com/",
                title="Narrativa home",
                snippet="In drug development, agentic AI plays a critical role in authoring clinical study reports required by regulatory authorities.",
                source_type="fixture",
                raw_content="Company: Narrativa\nCountry: Spain\nIn drug development, agentic AI plays a critical role in authoring clinical study reports required by regulatory authorities.\n",
            )
        ],
    )
    state = EngineRuntimeState(run=SearchRunSnapshot(request=request), memory=ExplorationMemoryState(), current_source_result=source_result)
    llm = FakeAssemblerLlmPort(
        {
            "subject_company_name": "Narrativa",
            "website": "https://www.narrativa.com",
            "country_code": "es",
            "employee_estimate": None,
            "person_name": "Sindhu Joseph",
            "role_title": "in authoring clinical study reports required by regulatory authorities",
            "fit_signals": ["genai"],
            "selected_evidence_urls": ["https://www.narrativa.com/"],
            "field_assertions": [
                {
                    "field_name": "company_name",
                    "value": "Narrativa",
                    "status": "satisfied",
                    "evidence_urls": ["https://www.narrativa.com/"],
                    "contradicting_urls": [],
                    "reasoning_note": "ok",
                },
                {
                    "field_name": "person_name",
                    "value": "Sindhu Joseph",
                    "status": "weakly_supported",
                    "evidence_urls": ["https://www.narrativa.com/"],
                    "contradicting_urls": [],
                    "source_tier": "tier_a",
                    "support_type": "weak_inference",
                    "reasoning_note": "not explicit",
                },
                {
                    "field_name": "role_title",
                    "value": "in authoring clinical study reports required by regulatory authorities",
                    "status": "weakly_supported",
                    "evidence_urls": ["https://www.narrativa.com/"],
                    "contradicting_urls": [],
                    "source_tier": "tier_a",
                    "support_type": "weak_inference",
                    "reasoning_note": "taken from product copy",
                },
            ],
        }
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.person is None or dossier.person.role_title is None


def test_assembler_rejects_person_not_tied_to_company_evidence() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="BitBrain",
        documents=[
            EvidenceDocument(
                url="https://www.seedtable.com/best-ai-startups-in-spain",
                title="36 Best AI Startups in Spain to Watch in 2026 - Seedtable",
                snippet="BitBrain appears in the ranking. Onna is founded by Ignacio Gaminde.",
                source_type="fixture",
                raw_content="BitBrain is a Spanish neurotechnology startup. Onna was founded by Ignacio Gaminde.",
                source_tier="tier_b",
            ),
            EvidenceDocument(
                url="https://www.bitbrain.com/careers",
                title="Careers | List of Job Opportunities - Bitbrain",
                snippet="About us. Blog. Contact.",
                source_type="fixture",
                raw_content="About us. Blog. Contact. BitBrain careers page.",
                source_tier="tier_a",
                is_company_controlled_source=True,
            ),
        ],
    )
    state = EngineRuntimeState(run=SearchRunSnapshot(request=request), memory=ExplorationMemoryState(), current_source_result=source_result)
    llm = FakeAssemblerLlmPort(
        {
            "subject_company_name": "BitBrain",
            "website": "https://www.bitbrain.com",
            "country_code": "es",
            "employee_estimate": 45,
            "person_name": "Ignacio Gaminde",
            "role_title": "Founder",
            "fit_signals": ["genai"],
            "selected_evidence_urls": [
                "https://www.seedtable.com/best-ai-startups-in-spain",
                "https://www.bitbrain.com/careers",
            ],
            "field_assertions": [
                {
                    "field_name": "company_name",
                    "value": "BitBrain",
                    "status": "satisfied",
                    "evidence_urls": ["https://www.seedtable.com/best-ai-startups-in-spain"],
                    "contradicting_urls": [],
                    "reasoning_note": "ok",
                },
                {
                    "field_name": "website",
                    "value": "https://www.bitbrain.com",
                    "status": "satisfied",
                    "evidence_urls": ["https://www.bitbrain.com/careers"],
                    "contradicting_urls": [],
                    "source_tier": "tier_a",
                    "support_type": "explicit",
                    "reasoning_note": "ok",
                },
                {
                    "field_name": "person_name",
                    "value": "Ignacio Gaminde",
                    "status": "weakly_supported",
                    "evidence_urls": ["https://www.seedtable.com/best-ai-startups-in-spain"],
                    "contradicting_urls": [],
                    "source_tier": "tier_b",
                    "support_type": "explicit",
                    "reasoning_note": "wrong person from another startup in same article",
                },
                {
                    "field_name": "role_title",
                    "value": "Founder",
                    "status": "weakly_supported",
                    "evidence_urls": ["https://www.seedtable.com/best-ai-startups-in-spain"],
                    "contradicting_urls": [],
                    "source_tier": "tier_b",
                    "support_type": "explicit",
                    "reasoning_note": "wrong role from another startup in same article",
                },
            ],
        }
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.company is not None
    assert dossier.company.name == "BitBrain"
    assert dossier.person is None or dossier.person.full_name is None


def test_company_selection_locks_plausible_focus_from_single_discovery_batch() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/origen-inteligencia-artificial-sl",
                    title="ORIGEN INTELIGENCIA ARTIFICIAL SL - Infoempresa",
                    snippet="Empresa de software en Madrid. Entre 10 y 49 empleados.",
                    source_type="fixture",
                    raw_content="Company: Origen Inteligencia Artificial SL\nCountry: Spain\nEmployees: 18\nSoftware company in Madrid.",
                )
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )

    llm = FakeAssemblerLlmPort(
        {
            "discovery_candidates": [
                {
                    "company_name": "ORIGEN INTELIGENCIA ARTIFICIAL SL",
                    "legal_name": "ORIGEN INTELIGENCIA ARTIFICIAL SL",
                    "query_name": "ORIGEN INTELIGENCIA ARTIFICIAL",
                    "brand_aliases": ["Origen Inteligencia Artificial"],
                    "country_code": "es",
                    "location_hint": "Madrid",
                    "theme_tags": ["software", "ia"],
                    "candidate_website": None,
                    "employee_count_hint_value": 18,
                    "employee_count_hint_type": "exact",
                    "operational_status": "active",
                    "support_type": "explicit",
                    "evidence_excerpt": "Company: Origen Inteligencia Artificial SL",
                    "evidence_urls": ["https://www.infoempresa.com/es-es/es/empresa/origen-inteligencia-artificial-sl"],
                    "is_real_company_candidate": True,
                    "rejection_reason": None,
                }
            ]
        }
    )

    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company == "ORIGEN INTELIGENCIA ARTIFICIAL SL"
    assert resolution.selection_mode in {"plausible", "confident"}
    assert AssembleStage(StageAgentExecutor(None)).last_company_selection_trace is None


def test_company_selection_returns_none_when_llm_extracts_no_real_candidates() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            EvidenceDocument(
                url="https://empresite.eleconomista.es/GO-HOLDINGS.html",
                title="GO HOLDINGS S.L. - Empresite",
                snippet="Ficha de empresa",
                source_type="fixture",
                raw_content="GO HOLDINGS S.L.",
            ),
            EvidenceDocument(
                url="https://empresite.eleconomista.es/SAAS-ADAMANTIUM-FIRST.html",
                title="ADAMANTIUM FIRST S.L. - Empresite",
                snippet="Ficha de empresa",
                source_type="fixture",
                raw_content="ADAMANTIUM FIRST S.L.",
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=2,
    )

    llm = FakeAssemblerLlmPort(
        {
            "discovery_candidates": [
                {
                    "company_name": "Las de IA españolas más innovadoras de 2024",
                    "legal_name": "Las de IA españolas más innovadoras de 2024",
                    "query_name": "Las de IA españolas más innovadoras de 2024",
                    "brand_aliases": [],
                    "country_code": "es",
                    "location_hint": "Madrid",
                    "theme_tags": ["software", "ia"],
                    "candidate_website": "https://example.com/article",
                    "employee_count_hint_value": 20,
                    "employee_count_hint_type": "estimate",
                    "operational_status": "active",
                    "support_type": "weak_inference",
                    "evidence_excerpt": "ranking article",
                    "evidence_urls": ["https://empresite.eleconomista.es/GO-HOLDINGS.html"],
                    "is_real_company_candidate": False,
                    "rejection_reason": "editorial_list_title",
                }
            ]
        }
    )

    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company is None
    assert resolution.selection_mode == "none"


def test_company_selection_trace_persists_input_and_resolution() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/origen-inteligencia-artificial-sl",
                    title="ORIGEN INTELIGENCIA ARTIFICIAL SL - Infoempresa",
                    snippet="Empresa de software en Madrid. Entre 10 y 49 empleados.",
                    source_type="fixture",
                    raw_content="Company: Origen Inteligencia Artificial SL\nCountry: Spain\nEmployees: 18\nSoftware company in Madrid.",
                )
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )

    llm = FakeAssemblerLlmPort(
        {
            "discovery_candidates": [
                {
                    "company_name": "ORIGEN INTELIGENCIA ARTIFICIAL SL",
                    "legal_name": "ORIGEN INTELIGENCIA ARTIFICIAL SL",
                    "query_name": "ORIGEN INTELIGENCIA ARTIFICIAL",
                    "brand_aliases": ["Origen Inteligencia Artificial"],
                    "country_code": "es",
                    "location_hint": "Madrid",
                    "theme_tags": ["software", "ia"],
                    "candidate_website": None,
                    "employee_count_hint_value": 18,
                    "employee_count_hint_type": "exact",
                    "operational_status": "active",
                    "support_type": "explicit",
                    "evidence_excerpt": "Company: Origen Inteligencia Artificial SL",
                    "evidence_urls": ["https://www.infoempresa.com/es-es/es/empresa/origen-inteligencia-artificial-sl"],
                    "is_real_company_candidate": True,
                    "rejection_reason": None,
                }
            ]
        }
    )
    stage = AssembleStage(StageAgentExecutor(llm))
    resolution = stage.select_focus_company(state)

    assert resolution.selected_company == "ORIGEN INTELIGENCIA ARTIFICIAL SL"
    assert stage.last_company_selection_trace is not None
    assert stage.last_company_selection_trace["input_documents"]
    assert stage.last_company_selection_trace["candidate_extraction_inputs"]
    assert stage.last_company_selection_trace["candidate_document_steps"]
    assert stage.last_company_selection_trace["aggregated_candidate_ledger"]
    assert stage.last_company_selection_trace["resolved_focus_company"] == "ORIGEN INTELIGENCIA ARTIFICIAL SL"


def test_company_selection_rejects_corrupted_fragment_candidate_from_llm() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            EvidenceDocument(
                url="https://example.com/editorial",
                title="Las startups de inteligencia artificial en Espana",
                snippet="Articulo editorial",
                source_type="fixture",
                raw_content="Las startups de inteligencia artificial en Espana...",
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )
    llm = FakeAssemblerLlmPort(
        {
            "discovery_candidates": [
                {
                    "company_name": "s de inteligencia artificial en España",
                    "legal_name": "s de inteligencia artificial en Espa\u00f1a",
                    "query_name": "s de inteligencia artificial en Espa\u00f1a",
                    "brand_aliases": [],
                    "country_code": "es",
                    "location_hint": "Madrid",
                    "theme_tags": ["software", "ia"],
                    "candidate_website": None,
                    "employee_count_hint_value": 20,
                    "employee_count_hint_type": "estimate",
                    "operational_status": "active",
                    "support_type": "weak_inference",
                    "evidence_excerpt": "editorial heading fragment",
                    "evidence_urls": ["https://example.com/editorial"],
                    "is_real_company_candidate": False,
                    "rejection_reason": "corrupted_heading_fragment",
                }
            ]
        }
    )

    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company is None
    assert resolution.selection_mode == "none"


def test_company_selection_uses_whole_document_mode_when_discovery_document_fits_context() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 empresa espanola de software con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nWebsite: https://acme.ai\nEmployees: 25\n",
                source_tier="tier_a",
                is_company_controlled_source=True,
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )

    llm = ChunkAwareAssemblerLlmPort()
    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company == "Acme AI SL"
    assert "discovery_candidate_document_mode" in llm.calls


def test_company_selection_uses_chunk_mode_when_discovery_document_exceeds_threshold() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 empresa espanola de software con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain",
                source_type="fixture",
                raw_content=("Company: Acme AI\nCountry: Spain\nWebsite: https://acme.ai\nEmployees: 25\n" * 700),
                source_tier="tier_a",
                is_company_controlled_source=True,
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )

    llm = ChunkAwareAssemblerLlmPort()
    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company == "Acme AI SL"
    assert "discovery_candidate_chunk_mode" in llm.calls


def test_focus_source_result_filters_non_matching_documents() -> None:
    stage = AssembleStage(StageAgentExecutor(None))
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.datoscif.es/empresa/agentes-de-ia-sl",
                    title="AGENTES DE IA SL - Informe de empresa | DatosCif",
                    snippet="Razón Social AGENTES DE IA SL",
                    source_type="fixture",
                    raw_content="Razón Social AGENTES DE IA SL\nProvincia Córdoba\nAdministrador Único Romeo Molina Alfredo",
                ),
                anchor_company="AGENTES DE IA SL",
            ),
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.datoscif.es/empresa/aris-software-spain-sl",
                    title="ARIS SOFTWARE SPAIN SL - Informe de empresa | DatosCif",
                    snippet="Razón Social ARIS SOFTWARE SPAIN SL",
                    source_type="fixture",
                    raw_content="Razón Social ARIS SOFTWARE SPAIN SL\nProvincia Madrid",
                ),
                anchor_company="ARIS SOFTWARE SPAIN SL",
            ),
        ],
    )

    focused = stage._focus_source_result(source_result, "AGENTES DE IA SL")

    assert [item.url for item in focused.documents] == ["https://www.datoscif.es/empresa/agentes-de-ia-sl"]
    assert "focus_locked_documents=1" in focused.notes


def test_company_selection_chooses_highest_scored_llm_candidate() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://empresite.eleconomista.es/SOFTWARE-AVANZADO-ARCHIVOS-SERVICIOS-SPAIN.html",
                    title="SOFTWARE AVANZADO DE ARCHIVOS Y SERVICIOS SPAIN SL - Empresite",
                    snippet="Empresa de software en Madrid.",
                    source_type="fixture",
                    raw_content="SOFTWARE AVANZADO DE ARCHIVOS Y SERVICIOS SPAIN SL. Empresa de software en Madrid.",
                )
            ),
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://empresite.eleconomista.es/SAAS-LEVEL-UP-2019.html",
                    title="SAAS LEVEL UP 2019 SOCIEDAD LIMITADA - Empresite",
                    snippet="Empresa SaaS en Espana.",
                    source_type="fixture",
                    raw_content="SAAS LEVEL UP 2019 SOCIEDAD LIMITADA. Empresa SaaS en Espana.",
                )
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )
    llm = SequentialAssemblerLlmPort(
        [
            {
                "discovery_candidates": [
                    {
                        "company_name": "Software Avanzado De Archivos Y Servicios Spain Sl",
                        "legal_name": "Software Avanzado De Archivos Y Servicios Spain Sl",
                        "query_name": "Software Avanzado De Archivos Y Servicios",
                        "brand_aliases": ["Software Avanzado De Archivos Y Servicios"],
                        "country_code": "es",
                        "location_hint": "Madrid",
                        "theme_tags": ["software"],
                        "candidate_website": None,
                        "employee_count_hint_value": 22,
                        "employee_count_hint_type": "exact",
                        "operational_status": "active",
                        "support_type": "explicit",
                        "evidence_excerpt": "Software Avanzado De Archivos Y Servicios Spain Sl",
                        "evidence_urls": ["https://empresite.eleconomista.es/SOFTWARE-AVANZADO-ARCHIVOS-SERVICIOS-SPAIN.html"],
                        "is_real_company_candidate": True,
                        "rejection_reason": None,
                    }
                ]
            },
            {
                "discovery_candidates": [
                    {
                        "company_name": "SAAS LEVEL UP 2019 SOCIEDAD LIMITADA",
                        "legal_name": "SAAS LEVEL UP 2019 SOCIEDAD LIMITADA",
                        "query_name": "SAAS LEVEL UP 2019",
                        "brand_aliases": ["SAAS LEVEL UP 2019"],
                        "country_code": "es",
                        "location_hint": "Madrid",
                        "theme_tags": ["software"],
                        "candidate_website": None,
                        "employee_count_hint_value": None,
                        "employee_count_hint_type": "unknown",
                        "operational_status": "active",
                        "support_type": "explicit",
                        "evidence_excerpt": "SAAS LEVEL UP 2019 SOCIEDAD LIMITADA",
                        "evidence_urls": ["https://empresite.eleconomista.es/SAAS-LEVEL-UP-2019.html"],
                        "is_real_company_candidate": True,
                        "rejection_reason": None,
                    }
                ]
            },
        ]
    )

    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company == "Software Avanzado De Archivos Y Servicios Spain Sl"
    assert "selected_by=confident" in resolution.notes


def test_company_selection_rejects_country_mismatched_candidate_from_llm_ledger() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://example.com/acme-us",
                    title="ACME US INC",
                    snippet="Software company in New York.",
                    source_type="fixture",
                    raw_content="Company: ACME US INC\nCountry: United States\nEmployees: 12\nSoftware company.",
                )
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )
    llm = FakeAssemblerLlmPort(
        {
            "discovery_candidates": [
                {
                    "company_name": "ACME US INC",
                    "legal_name": "ACME US INC",
                    "query_name": "ACME US",
                    "brand_aliases": ["Acme"],
                    "country_code": "us",
                    "location_hint": "New York",
                    "theme_tags": ["software"],
                    "candidate_website": None,
                    "employee_count_hint_value": 12,
                    "employee_count_hint_type": "exact",
                    "operational_status": "active",
                    "support_type": "explicit",
                    "evidence_excerpt": "Company: ACME US INC",
                    "evidence_urls": ["https://example.com/acme-us"],
                    "is_real_company_candidate": True,
                    "rejection_reason": None,
                }
            ]
        }
    )

    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company is None
    assert resolution.selection_mode == "none"


def test_assembler_trace_records_llm_timeout_and_fallback() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nGenAI automation engineering",
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        current_focus_company_resolution=CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI"),
    )

    dossier = AssembleStage(StageAgentExecutor(TimeoutAssemblerLlmPort())).execute(state)

    assert dossier.company is not None
    assert state.current_assembler_trace is not None
    assert state.current_assembler_trace["used_fallback"] is True
    assert state.current_assembler_trace["llm_error"] is not None
    assert state.current_assembler_trace["document_steps"][0]["llm_error"] == "timed out"


def test_fallback_assembly_keeps_explicit_employee_estimate_without_official_website() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead de una empresa espanola del sector IT con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Tecnologia Teseo España Sl",
        documents=[
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://empresite.eleconomista.es/TECNOLOGIA-TESEO-ESPANA.html",
                    title="TECNOLOGIA TESEO ESPAÑA SL - Empresite",
                    snippet="Empresa en Elche. Tiene un total de 3 trabajadores.",
                    source_type="fixture",
                    raw_content=(
                        "TECNOLOGIA TESEO ESPAÑA SL. "
                        "Empresa en Elche, Alicante, España. "
                        "¿Cuántos trabajadores tiene Tecnologia Teseo España Sl? "
                        "Tiene un total de 3 trabajadores en su plantilla."
                    ),
                    source_tier="tier_b",
                ),
                anchor_company="Tecnologia Teseo España Sl",
            ),
        ],
        research_trace=[
            ResearchTraceEntry(
                query_planned='empresite "Tecnologia Teseo" empleados plantilla',
                query_executed='empresite "Tecnologia Teseo" empleados plantilla',
                research_phase="company_anchoring",
                objective="Find explicit employee count evidence.",
                documents_considered=1,
                documents_selected=1,
                selected_urls=["https://empresite.eleconomista.es/TECNOLOGIA-TESEO-ESPANA.html"],
                expected_field="employee_estimate",
                source_role="employee_count_resolution",
            )
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        current_focus_company_resolution=CompanyFocusResolution(
            selected_company="Tecnologia Teseo España Sl",
            legal_name="Tecnologia Teseo España Sl",
            query_name="Tecnologia Teseo",
        ),
        focus_company_locked=True,
    )

    dossier = AssembleStage(StageAgentExecutor(TimeoutAssemblerLlmPort())).execute(state)

    assert dossier.company is not None
    assert dossier.company.name == "Tecnologia Teseo España Sl"
    assert dossier.company.employee_estimate == 3
    assert all("motor-taller" not in item.url for item in dossier.evidence)


def test_company_selection_payload_is_compact() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 empresa espanola de software con menos de 50 empleados")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            EvidenceDocument(
                url="https://empresite.eleconomista.es/ACME-AI.html",
                title="ACME AI SL - Empresite",
                snippet="Empresa de software en Madrid",
                source_type="fixture",
                raw_content="A" * 3000,
                source_tier="tier_b",
            )
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        discovery_attempts_for_current_pass=1,
    )

    stage = AssembleStage(StageAgentExecutor(None))
    payload = stage._company_selection_payload(state, source_result, [], [])

    assert "source_result" not in payload
    assert payload["request_summary"]["preferred_country"] == "es"
    assert payload["documents"][0]["raw_content_preview"] == "A" * 1200
    assert "raw_content" not in payload["documents"][0]


def test_chunk_document_splits_large_content_with_limits() -> None:
    stage = AssembleStage(StageAgentExecutor(None))
    document = EvidenceDocument(
        url="https://acme.ai/about",
        title="Acme AI about",
        snippet="About Acme AI",
        source_type="fixture",
        raw_content=("Paragraph one. " * 220) + "\n\n" + ("Paragraph two. " * 220),
    )

    chunks = stage._chunk_document(document)

    assert chunks
    assert len(chunks) <= 4
    assert all(len(item["text"]) <= 4000 for item in chunks)
    assert all(item["total"] == len(chunks) for item in chunks)


def test_assembler_uses_whole_document_mode_when_document_fits_context() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    content = "Company: Acme AI\nCountry: Spain\nWebsite: https://acme.ai\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering\n"
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain",
                source_type="fixture",
                raw_content=content,
                source_tier="tier_a",
                is_company_controlled_source=True,
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        current_focus_company_resolution=CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI"),
    )

    llm = ChunkAwareAssemblerLlmPort()
    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    assert dossier.person is not None
    assert dossier.person.full_name == "Laura Martin"
    assert "focus_locked_document_mode" in llm.calls
    assert state.current_assembler_trace is not None
    step = state.current_assembler_trace["document_steps"][0]
    assert step["mode"] == "focus_locked_document_mode"
    assert step["estimated_input_tokens"] > 0
    assert step["parse_success"] is True
    assert step["llm_latency_ms"] >= 0


def test_focus_locked_mode_uses_chunk_trace_and_summary() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    long_content = ("Company: Acme AI\nCountry: Spain\nWebsite: https://acme.ai\nEmployees: 25\n" * 700)
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain",
                source_type="fixture",
                raw_content=long_content,
                source_tier="tier_a",
                is_company_controlled_source=True,
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        current_focus_company_resolution=CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI"),
    )

    llm = ChunkAwareAssemblerLlmPort()
    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.company is not None
    assert state.current_assembler_trace is not None
    assert state.current_assembler_trace["chunk_inputs"]
    assert state.current_assembler_trace["chunk_sanitized_outputs"]
    assert state.current_assembler_trace["chunk_merge_summary"]
    assert "focus_locked_chunk_mode" in llm.calls
    step = state.current_assembler_trace["document_steps"][0]
    assert step["mode"] == "focus_locked_chunk_mode"
    assert step["estimated_input_tokens"] > 10000
    assert step["parse_success"] is True


def test_assembler_ignores_other_company_mentions_when_subject_is_supported() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(
                url="https://directory.example.com/acme-ai",
                title="Acme AI ficha",
                snippet="Acme AI and other companies",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nOther company: Bravo Labs\n",
            ),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        current_focus_company_resolution=CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI"),
    )
    llm = FakeAssemblerLlmPort(
        {
            "segment_company_name": "Acme AI",
            "field_assertions": [
                {
                    "field_name": "company_name",
                    "company_name": "Acme AI",
                    "value": "Acme AI",
                    "status": "satisfied",
                    "support_type": "explicit",
                    "reasoning_note": "Acme AI is the ficha subject",
                },
                {
                    "field_name": "company_name",
                    "company_name": "Bravo Labs",
                    "value": "Bravo Labs",
                    "status": "satisfied",
                    "support_type": "explicit",
                    "reasoning_note": "mentioned as related company only",
                },
                {
                    "field_name": "country",
                    "company_name": "Acme AI",
                    "value": "es",
                    "status": "satisfied",
                    "support_type": "explicit",
                    "reasoning_note": "Acme country",
                },
            ],
            "contact_assertions": [],
            "fit_signals": ["genai"],
            "contradictions": [],
            "notes": ["document_ok"],
        }
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    company_field = next(item for item in dossier.field_evidence if item.field_name == "company_name")
    assert company_field.status == FieldEvidenceStatus.SATISFIED
    assert company_field.contradicting_evidence == []


def test_assembler_marks_country_contradicted_only_for_incompatible_focus_values() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(url="https://source-a.test/acme", title="a", snippet="a", source_type="fixture", raw_content="A"),
            EvidenceDocument(url="https://source-b.test/acme", title="b", snippet="b", source_type="fixture", raw_content="B"),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        current_focus_company_resolution=CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI"),
    )
    llm = SequentialAssemblerLlmPort(
        [
            {
                "segment_company_name": "Acme AI",
                "field_assertions": [
                    {"field_name": "company_name", "company_name": "Acme AI", "value": "Acme AI", "status": "satisfied", "support_type": "explicit"},
                    {"field_name": "country", "company_name": "Acme AI", "value": "es", "status": "satisfied", "support_type": "explicit"},
                ],
                "contact_assertions": [],
                "fit_signals": [],
                "contradictions": [],
                "notes": [],
            },
            {
                "segment_company_name": "Acme AI",
                "field_assertions": [
                    {"field_name": "company_name", "company_name": "Acme AI", "value": "Acme AI", "status": "satisfied", "support_type": "explicit"},
                    {"field_name": "country", "company_name": "Acme AI", "value": "fr", "status": "satisfied", "support_type": "explicit"},
                ],
                "contact_assertions": [],
                "fit_signals": [],
                "contradictions": [],
                "notes": [],
            },
        ]
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    country_field = next(item for item in dossier.field_evidence if item.field_name == "country")
    assert country_field.status == FieldEvidenceStatus.CONTRADICTED


def test_assembler_prefers_exact_employee_count_over_estimate_without_contradiction() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(url="https://source-a.test/acme", title="a", snippet="a", source_type="fixture", raw_content="A"),
            EvidenceDocument(url="https://source-b.test/acme", title="b", snippet="b", source_type="fixture", raw_content="B"),
        ],
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=request),
        memory=ExplorationMemoryState(),
        current_source_result=source_result,
        current_focus_company_resolution=CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI"),
    )
    llm = SequentialAssemblerLlmPort(
        [
            {
                "segment_company_name": "Acme AI",
                "field_assertions": [
                    {"field_name": "company_name", "company_name": "Acme AI", "value": "Acme AI", "status": "satisfied", "support_type": "explicit"},
                    {"field_name": "employee_estimate", "company_name": "Acme AI", "value": 25, "status": "satisfied", "support_type": "explicit", "employee_count_type": "exact"},
                ],
                "contact_assertions": [],
                "fit_signals": [],
                "contradictions": [],
                "notes": [],
            },
            {
                "segment_company_name": "Acme AI",
                "field_assertions": [
                    {"field_name": "company_name", "company_name": "Acme AI", "value": "Acme AI", "status": "satisfied", "support_type": "explicit"},
                    {"field_name": "employee_estimate", "company_name": "Acme AI", "value": 22, "status": "weakly_supported", "support_type": "weak_inference", "employee_count_type": "estimate"},
                ],
                "contact_assertions": [],
                "fit_signals": [],
                "contradictions": [],
                "notes": [],
            },
        ]
    )

    dossier = AssembleStage(StageAgentExecutor(llm)).execute(state)

    employee_field = next(item for item in dossier.field_evidence if item.field_name == "employee_estimate")
    assert dossier.company is not None
    assert dossier.company.employee_estimate == 25
    assert employee_field.status == FieldEvidenceStatus.SATISFIED
