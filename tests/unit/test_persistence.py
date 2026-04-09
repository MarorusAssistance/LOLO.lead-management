import sqlite3

from lolo_lead_management.domain.models import CompanyObservation, SearchRunSnapshot
from lolo_lead_management.infrastructure.sqlite import SqliteDatabase
from lolo_lead_management.adapters.stores.sqlite import SqliteExplorationMemoryStore, SqliteSearchRunStore
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.domain.models import LeadSearchStartRequest
from tests.helpers import workspace_tmp_dir


def test_sqlite_run_store_roundtrip() -> None:
    tmp_path = workspace_tmp_dir("persistence-run")
    database = SqliteDatabase(str(tmp_path / "persistence.sqlite3"))
    run_store = SqliteSearchRunStore(database)
    request = NormalizeStage(StageAgentExecutor(None)).execute(LeadSearchStartRequest(user_text="busca 1 lead en espana"))
    run = SearchRunSnapshot(request=request)
    run_store.save_run(run)

    loaded = run_store.get_run(run.run_id)

    assert loaded is not None
    assert loaded.run_id == run.run_id


def test_memory_reset_keeps_registered_leads_by_default() -> None:
    tmp_path = workspace_tmp_dir("persistence-memory")
    database = SqliteDatabase(str(tmp_path / "memory.sqlite3"))
    store = SqliteExplorationMemoryStore(database)
    state = store.get_campaign_state()
    state.query_history = ["a"]
    state.registered_lead_names = ["Laura Martin"]
    store.save_campaign_state(state)
    store.reset_query_memory(["queryHistory"], include_registered_lead_names=False)

    updated = store.get_campaign_state()
    assert updated.query_history == []
    assert updated.registered_lead_names == ["Laura Martin"]

    with sqlite3.connect(database.path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM exploration_memory").fetchone()[0]
    assert count == 1


def test_company_observations_roundtrip_in_memory_store() -> None:
    tmp_path = workspace_tmp_dir("persistence-company-observations")
    database = SqliteDatabase(str(tmp_path / "memory.sqlite3"))
    store = SqliteExplorationMemoryStore(database)
    state = store.get_campaign_state()
    state.company_observations = [
        CompanyObservation(
            company_name="Megasoft Spain SL",
            legal_name="Megasoft Spain SL",
            query_name="Megasoft",
            official_domain="megasoft.com",
            country_code="es",
            employee_count_exact=2000,
            theme_tags=["software company"],
            operational_status="active",
            last_outcome="reject_no_match",
            rejection_reasons=["size_mismatch"],
        )
    ]
    store.save_campaign_state(state)

    loaded = store.get_campaign_state()

    assert len(loaded.company_observations) == 1
    assert loaded.company_observations[0].company_name == "Megasoft Spain SL"
    assert loaded.company_observations[0].employee_count_exact == 2000


def test_memory_store_prunes_enrich_companies_from_searched_company_names() -> None:
    tmp_path = workspace_tmp_dir("persistence-memory-prune-enrich")
    database = SqliteDatabase(str(tmp_path / "memory.sqlite3"))
    store = SqliteExplorationMemoryStore(database)
    state = store.get_campaign_state()
    state.searched_company_names = ["Acme AI", "Contestio Spain Sl."]
    state.company_observations = [
        CompanyObservation(
            company_name="Acme AI",
            legal_name="Acme AI SL",
            query_name="Acme",
            official_domain=None,
            country_code="es",
            operational_status="active",
            last_outcome="ENRICH",
            rejection_reasons=["missing_person"],
        ),
        CompanyObservation(
            company_name="Contestio Spain Sl.",
            legal_name="Contestio Spain Sl.",
            query_name="Contestio",
            official_domain=None,
            country_code="es",
            operational_status="active",
            last_outcome="REJECT_CLOSE_MATCH",
            rejection_reasons=["size_mismatch"],
        ),
    ]
    store.save_campaign_state(state)

    loaded = store.get_campaign_state()

    assert loaded.searched_company_names == ["Contestio Spain Sl."]
