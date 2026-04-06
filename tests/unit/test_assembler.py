from lolo_lead_management.domain.enums import SourcingStatus
from lolo_lead_management.domain.models import EvidenceDocument, ExplorationMemoryState, LeadSearchStartRequest, ResearchQuery, ResearchTraceEntry, SearchRunSnapshot, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.state import EngineRuntimeState


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
