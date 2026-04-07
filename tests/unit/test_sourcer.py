from lolo_lead_management.adapters.search.fake import FakeSearchPort
from lolo_lead_management.adapters.stores.sqlite import SqliteExplorationMemoryStore
from lolo_lead_management.domain.models import EvidenceDocument, LeadSearchStartRequest, SearchBudget, SearchRunSnapshot
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.rules import candidate_company_names_from_document, enrich_document_metadata
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.load_state import LoadStateStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.stages.source import SourceStage
from lolo_lead_management.infrastructure.sqlite import SqliteDatabase

from tests.helpers import accepted_candidate_fixture, workspace_tmp_dir


def test_sourcer_collects_documents_and_assembler_resolves_company() -> None:
    tmp_path = workspace_tmp_dir("sourcer")
    search_index, pages = accepted_candidate_fixture()
    search_port = FakeSearchPort(search_index=search_index, pages=pages)
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "sourcer.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    assemble_stage = AssembleStage(StageAgentExecutor(None))

    source_result = source_stage.execute(state)
    state.current_source_result = source_result
    dossier = assemble_stage.execute(state)

    assert source_result.sourcing_status.value == "FOUND"
    assert source_result.executed_queries[0].query == "Spain AI startup directory"
    assert source_result.executed_queries[0].expected_field == "company_name"
    assert source_result.executed_queries[0].source_tier_target == "tier_b"
    assert len(source_result.documents) >= 2
    assert dossier.sourcing_status.value == "FOUND"
    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    assert len(dossier.evidence) >= 2


def test_sourcer_skips_previously_explored_company_when_anchoring() -> None:
    tmp_path = workspace_tmp_dir("sourcer_exclusions")
    search_index = {
        "Spain AI startup directory": [
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
            EvidenceDocument(
                url="https://bravo.dev/team",
                title="Bravo Dev engineering team",
                snippet="Company: Bravo Dev | Person: Marta Diaz | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Bravo Dev\nCountry: Spain\nEmployees: 20\nPerson: Marta Diaz\nRole: CTO\nGenAI automation engineering",
            ),
        ]
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "sourcer.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)
    state.memory.searched_company_names = ["Acme AI"]

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    source_result = source_stage.execute(state)

    assert source_result.sourcing_status.value == "FOUND"
    assert source_result.anchored_company_name == "Bravo Dev"
    assert any(item.url == "https://bravo.dev/team" for item in source_result.documents)


def test_sourcer_does_not_plan_person_lookup_before_anchor() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en españa entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(workspace_tmp_dir("sourcer_plan") / "plan.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)
    source_stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)

    result = source_stage.execute(state)

    assert result.query_plan is not None
    assert all(query.expected_field == "company_name" for query in result.query_plan.planned_queries if query.research_phase == "company_discovery")
    assert all(query.expected_field not in {"person_name", "role_title"} for query in result.query_plan.planned_queries if query.research_phase == "company_discovery")


def test_sourcer_selects_directory_title_company_not_portal_text() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://startupshub.catalonia.com/startup/barcelona/diipai/7148",
            title="diip artificial intelligence sl - Barcelona & Catalonia STARTUP HUB",
            snippet="The Barcelona & Catalonia Startup Hub. It is a Catalan Government project offering updated information.",
            source_type="fixture",
            raw_content="The Barcelona & Catalonia Startup Hub. It is a Catalan Government project offering updated information. Financial data. Investment. Other information.",
        )
    )

    candidates = candidate_company_names_from_document(document)

    assert candidates[0] == "Diip Artificial Intelligence Sl"


def test_non_official_profile_is_not_marked_company_controlled() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.iberinform.es/empresa/10485136/aima-artificial-intelligence",
            title="AIMA ARTIFICIAL INTELLIGENCE SL: CIF, Dirección y Ventas - Iberinform",
            snippet="Aima Artificial Intelligence Sl usa el código 7311.",
            source_type="fixture",
            raw_content="Aima Artificial Intelligence Sl opera en Barcelona. La página web de Aima Artificial Intelligence Sl es null.",
        ),
        anchor_company="Aima Artificial Intelligence Sl",
    )

    assert document.is_company_controlled_source is False
    assert document.source_tier == "tier_b"
