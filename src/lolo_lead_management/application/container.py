from __future__ import annotations

from dataclasses import dataclass

from lolo_lead_management.adapters.crm.sqlite import SqliteCrmWriter
from lolo_lead_management.adapters.llm.lm_studio import LmStudioLlmPort
from lolo_lead_management.adapters.search.fake import FakeSearchPort
from lolo_lead_management.adapters.search.tavily import TavilySearchPort
from lolo_lead_management.adapters.stores.sqlite import (
    SqliteExplorationMemoryStore,
    SqliteLeadStore,
    SqliteSearchRunStore,
    SqliteShortlistStore,
)
from lolo_lead_management.config.settings import Settings
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.main import LeadManagementEngine
from lolo_lead_management.engine.stages.assemble import AssembleStage
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
from lolo_lead_management.ports.llm import LlmPort
from lolo_lead_management.ports.search import SearchPort


@dataclass
class ServiceContainer:
    settings: Settings
    database: SqliteDatabase
    engine: LeadManagementEngine
    llm_port: LlmPort | None
    search_port: SearchPort
    lead_store: SqliteLeadStore
    run_store: SqliteSearchRunStore
    shortlist_store: SqliteShortlistStore
    memory_store: SqliteExplorationMemoryStore
    crm_writer: SqliteCrmWriter
    archive_writer: ExecutionArchiveWriter


def build_container(settings: Settings) -> ServiceContainer:
    database = SqliteDatabase(settings.database_path)
    lead_store = SqliteLeadStore(database)
    run_store = SqliteSearchRunStore(database)
    shortlist_store = SqliteShortlistStore(database)
    memory_store = SqliteExplorationMemoryStore(database)
    crm_writer = SqliteCrmWriter(database)
    archive_writer = ExecutionArchiveWriter(settings.execution_results_dir)

    llm_port = None
    if settings.llm_enabled:
        llm_port = LmStudioLlmPort(base_url=settings.lm_studio_base_url, model=settings.lm_studio_model)

    if settings.search_enabled and settings.tavily_api_key:
        search_port = TavilySearchPort(api_key=settings.tavily_api_key, base_url=settings.tavily_base_url)
    else:
        search_port = FakeSearchPort()

    agent_executor = StageAgentExecutor(llm_port)
    engine = LeadManagementEngine(
        normalize_stage=NormalizeStage(agent_executor),
        load_state_stage=LoadStateStage(memory_store),
        plan_stage=PlanStage(agent_executor),
        source_stage=SourceStage(search_port=search_port, agent_executor=agent_executor, max_results=settings.search_max_results),
        assemble_stage=AssembleStage(agent_executor),
        qualify_stage=QualifyStage(agent_executor),
        enrich_stage=EnrichStage(search_port=search_port, agent_executor=agent_executor, max_results=settings.search_max_results),
        draft_stage=DraftStage(agent_executor),
        crm_write_stage=CrmWriteStage(
            lead_store=lead_store,
            run_store=run_store,
            shortlist_store=shortlist_store,
            memory_store=memory_store,
            crm_writer=crm_writer,
            shortlist_size=settings.shortlist_size,
        ),
        continue_stage=ContinueOrFinishStage(run_store=run_store, memory_store=memory_store),
        run_store=run_store,
        shortlist_store=shortlist_store,
        source_attempt_budget=settings.source_attempt_budget,
        enrich_attempt_budget=settings.enrich_attempt_budget,
        archive_writer=archive_writer,
    )
    return ServiceContainer(
        settings=settings,
        database=database,
        engine=engine,
        llm_port=llm_port,
        search_port=search_port,
        lead_store=lead_store,
        run_store=run_store,
        shortlist_store=shortlist_store,
        memory_store=memory_store,
        crm_writer=crm_writer,
        archive_writer=archive_writer,
    )
