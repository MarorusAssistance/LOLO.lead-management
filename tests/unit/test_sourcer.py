from lolo_lead_management.adapters.search.fake import FakeSearchPort
from lolo_lead_management.adapters.stores.sqlite import SqliteExplorationMemoryStore
from lolo_lead_management.domain.models import LeadSearchStartRequest, SearchBudget, SearchRunSnapshot
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
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
    assert source_result.executed_queries[0].query == "Spain startup AI software company"
    assert len(source_result.documents) >= 2
    assert dossier.sourcing_status.value == "FOUND"
    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    assert len(dossier.evidence) >= 2
