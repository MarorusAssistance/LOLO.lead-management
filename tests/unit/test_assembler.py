from lolo_lead_management.domain.enums import SourcingStatus
from lolo_lead_management.domain.models import ChunkExtractionResolution, CompanyFocusResolution, EvidenceDocument, ExplorationMemoryState, LeadSearchStartRequest, ResearchQuery, ResearchTraceEntry, SearchRunSnapshot, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.rules import enrich_document_metadata
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.state import EngineRuntimeState


class FakeAssemblerLlmPort:
    def __init__(self, response: dict) -> None:
        self._response = response

    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, input_payload, schema)
        return self._response


class SequentialAssemblerLlmPort:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, input_payload, schema)
        self.calls += 1
        return self._responses.pop(0)


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
        if input_payload["mode"] == "focus_locked_chunk_mode":
            return {
                "candidate_website": "https://acme.ai",
                "website_signals": ["chunk saw explicit website"],
                "country_code": "es",
                "location_hint": "Madrid",
                "employee_count_hint_value": 25,
                "employee_count_hint_type": "exact",
                "person_clues": ["Laura Martin"],
                "role_clues": ["CTO"],
                "fit_signals": ["genai"],
                "contradictions": [],
                "notes": ["chunk_ok"],
            }
        return {
            "subject_company_name": "Acme AI",
            "candidate_website": "https://acme.ai",
            "website_officiality": "probable",
            "website_confidence": 0.72,
            "website_evidence_urls": ["https://acme.ai/about"],
            "website_signals": ["chunk saw explicit website"],
            "website_risks": [],
            "country_code": "es",
            "employee_estimate": 25,
            "fit_signals": ["genai"],
            "selected_evidence_urls": ["https://acme.ai/about"],
            "field_assertions": [
                {
                    "field_name": "company_name",
                    "value": "Acme AI",
                    "status": "satisfied",
                    "evidence_urls": ["https://acme.ai/about"],
                    "contradicting_urls": [],
                    "reasoning_note": "ok",
                }
            ],
            "contradictions": [],
            "unresolved_fields": ["person_name", "role_title"],
            "notes": ["final_ok"],
        }


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
    assert "assembled_by=llm" in dossier.notes
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

    resolution = AssembleStage(StageAgentExecutor(None)).select_focus_company(state)

    assert resolution.selected_company == "ORIGEN INTELIGENCIA ARTIFICIAL SL"
    assert resolution.selection_mode in {"plausible", "confident"}
    assert AssembleStage(StageAgentExecutor(None)).last_company_selection_trace is None


def test_company_selection_uses_fallback_after_second_discovery_batch() -> None:
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

    resolution = AssembleStage(StageAgentExecutor(None)).select_focus_company(state)

    assert resolution.selected_company is not None
    assert resolution.selection_mode in {"fallback", "plausible"}


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

    stage = AssembleStage(StageAgentExecutor(None))
    resolution = stage.select_focus_company(state)

    assert resolution.selected_company == "ORIGEN INTELIGENCIA ARTIFICIAL SL"
    assert stage.last_company_selection_trace is not None
    assert stage.last_company_selection_trace["input_documents"]
    assert stage.last_company_selection_trace["llm_input_payload"] is not None
    assert stage.last_company_selection_trace["resolved_focus_company"] == "ORIGEN INTELIGENCIA ARTIFICIAL SL"


def test_company_selection_trusts_llm_focus_without_margin_threshold() -> None:
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
    llm = FakeAssemblerLlmPort(
        {
            "selected_company": "Software Avanzado De Archivos Y Servicios Spain Sl",
            "legal_name": "Software Avanzado De Archivos Y Servicios Spain Sl",
            "query_name": "Software Avanzado De Archivos Y Servicios",
            "brand_aliases": ["Software Avanzado De Archivos Y Servicios"],
            "evidence_urls": ["https://empresite.eleconomista.es/SOFTWARE-AVANZADO-ARCHIVOS-SERVICIOS-SPAIN.html"],
            "selection_mode": "plausible",
            "confidence": 0.46,
            "selection_reasons": ["country_or_location_matches", "theme_match"],
            "hard_rejections": [],
            "rejected_candidates": [],
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
                    "employee_count_hint_value": None,
                    "employee_count_hint_type": "unknown",
                    "operational_status": "active",
                    "evidence_urls": ["https://empresite.eleconomista.es/SOFTWARE-AVANZADO-ARCHIVOS-SERVICIOS-SPAIN.html"],
                    "selection_score": 0.46,
                    "selection_reasons": ["country_or_location_matches", "theme_match"],
                    "hard_rejections": [],
                }
            ],
            "notes": [],
        }
    )

    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company == "Software Avanzado De Archivos Y Servicios Spain Sl"
    assert "focus_trusted_from_llm" in resolution.notes
    assert "focus_fields_preserved_from_llm" in resolution.notes


def test_company_selection_rejects_llm_focus_on_hard_contradiction() -> None:
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
            "selected_company": "ACME US INC",
            "legal_name": "ACME US INC",
            "query_name": "ACME US",
            "brand_aliases": ["Acme"],
            "evidence_urls": ["https://example.com/acme-us"],
            "selection_mode": "plausible",
            "confidence": 0.7,
            "selection_reasons": ["theme_match"],
            "hard_rejections": [],
            "rejected_candidates": [],
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
                    "evidence_urls": ["https://example.com/acme-us"],
                    "selection_score": 0.7,
                    "selection_reasons": ["theme_match"],
                    "hard_rejections": [],
                }
            ],
            "notes": [],
        }
    )

    resolution = AssembleStage(StageAgentExecutor(llm)).select_focus_company(state)

    assert resolution.selected_company is None
    assert "focus_rejected_by_hard_contradiction" in resolution.notes


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
    assert all(len(item["text"]) <= 3000 for item in chunks)
    assert all(item["total"] == len(chunks) for item in chunks)


def test_focus_locked_mode_uses_chunk_trace_and_summary() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    long_content = ("Company: Acme AI\nCountry: Spain\nWebsite: https://acme.ai\nEmployees: 25\n" * 120)
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
    assert "focus_locked_mode" in llm.calls
