from __future__ import annotations

import shutil
from pathlib import Path

from lolo_lead_management.adapters.crm.sqlite import SqliteCrmWriter
from lolo_lead_management.adapters.search.fake import FakeSearchPort
from lolo_lead_management.adapters.stores.sqlite import (
    SqliteExplorationMemoryStore,
    SqliteLeadStore,
    SqliteSearchRunStore,
    SqliteShortlistStore,
)
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.config.settings import Settings
from lolo_lead_management.domain.models import EvidenceItem
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.main import LeadManagementEngine
from lolo_lead_management.engine.stages.continue_or_finish import ContinueOrFinishStage
from lolo_lead_management.engine.stages.crm_write import CrmWriteStage
from lolo_lead_management.engine.stages.draft import DraftStage
from lolo_lead_management.engine.stages.enrich import EnrichStage
from lolo_lead_management.engine.stages.load_state import LoadStateStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.stages.plan import PlanStage
from lolo_lead_management.engine.stages.qualify import QualifyStage
from lolo_lead_management.engine.stages.source import SourceStage
from lolo_lead_management.infrastructure.run_archive import ExecutionArchiveWriter
from lolo_lead_management.infrastructure.sqlite import SqliteDatabase


def workspace_tmp_dir(name: str) -> Path:
    base = Path("test-output") / name
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_test_container(
    tmp_path: Path,
    *,
    search_index: dict[str, list[EvidenceItem]] | None = None,
    pages: dict[str, str] | None = None,
) -> ServiceContainer:
    settings = Settings(database_path=str(tmp_path / "lead_management.sqlite3"))
    database = SqliteDatabase(settings.database_path)
    lead_store = SqliteLeadStore(database)
    run_store = SqliteSearchRunStore(database)
    shortlist_store = SqliteShortlistStore(database)
    memory_store = SqliteExplorationMemoryStore(database)
    crm_writer = SqliteCrmWriter(database)
    archive_writer = ExecutionArchiveWriter(str(tmp_path / "execution-results"))
    search_port = FakeSearchPort(search_index=search_index, pages=pages)
    agent_executor = StageAgentExecutor(None)
    engine = LeadManagementEngine(
        normalize_stage=NormalizeStage(agent_executor),
        load_state_stage=LoadStateStage(memory_store),
        plan_stage=PlanStage(agent_executor),
        source_stage=SourceStage(search_port=search_port, agent_executor=agent_executor, max_results=5),
        qualify_stage=QualifyStage(agent_executor),
        enrich_stage=EnrichStage(search_port=search_port, agent_executor=agent_executor, max_results=5),
        draft_stage=DraftStage(agent_executor),
        crm_write_stage=CrmWriteStage(
            lead_store=lead_store,
            run_store=run_store,
            shortlist_store=shortlist_store,
            memory_store=memory_store,
            crm_writer=crm_writer,
            shortlist_size=5,
        ),
        continue_stage=ContinueOrFinishStage(run_store=run_store, memory_store=memory_store),
        run_store=run_store,
        shortlist_store=shortlist_store,
        source_attempt_budget=6,
        enrich_attempt_budget=1,
        archive_writer=archive_writer,
    )
    return ServiceContainer(
        settings=settings,
        database=database,
        engine=engine,
        llm_port=None,
        search_port=search_port,
        lead_store=lead_store,
        run_store=run_store,
        shortlist_store=shortlist_store,
        memory_store=memory_store,
        crm_writer=crm_writer,
        archive_writer=archive_writer,
    )


def accepted_candidate_fixture() -> tuple[dict[str, list[EvidenceItem]], dict[str, str]]:
    query = "site:eu-startups.com/directory/ Spain AI software"
    search_index = {
        query: [
            EvidenceItem(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
            ),
            EvidenceItem(
                url="https://acme.ai/blog/agentic-workflows",
                title="Acme AI on agentic workflows",
                snippet="Company: Acme AI | Country: Spain | Employees: 25 | GenAI automation",
                source_type="fixture",
            ),
        ]
    }
    pages = {
        "https://acme.ai/about": "Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
        "https://acme.ai/blog/agentic-workflows": "Company: Acme AI\nCountry: Spain\nEmployees: 25\nAutomation and GenAI workflows for IT teams",
    }
    return search_index, pages


def close_match_candidate_fixture() -> tuple[dict[str, list[EvidenceItem]], dict[str, str]]:
    query = "site:eu-startups.com/directory/ Spain AI software"
    search_index = {
        query: [
            EvidenceItem(
                url="https://bravo.dev/team",
                title="Bravo Dev engineering team",
                snippet="Company: Bravo Dev | Person: Marta Diaz | Role: Engineering Manager | Country: Spain",
                source_type="fixture",
            ),
            EvidenceItem(
                url="https://bravo.dev/blog/genai",
                title="Bravo Dev exploring GenAI automation",
                snippet="Company: Bravo Dev | Country: Spain | Employees: 30 | GenAI automation",
                source_type="fixture",
            ),
        ]
    }
    pages = {
        "https://bravo.dev/team": "Company: Bravo Dev\nCountry: Spain\nEmployees: 30\nPerson: Marta Diaz\nRole: Engineering Manager\nGenAI automation engineering",
        "https://bravo.dev/blog/genai": "Company: Bravo Dev\nCountry: Spain\nEmployees: 30\nAutomation and GenAI workflows for engineering teams",
    }
    return search_index, pages
