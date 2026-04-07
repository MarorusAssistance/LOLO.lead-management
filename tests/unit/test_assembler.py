from lolo_lead_management.domain.enums import SourcingStatus
from lolo_lead_management.domain.models import EvidenceDocument, ExplorationMemoryState, LeadSearchStartRequest, ResearchQuery, ResearchTraceEntry, SearchRunSnapshot, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
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


def test_assembler_picks_subject_company_not_publisher() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai")
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
        LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai")
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


def test_assembler_processes_documents_incrementally() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai")
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
