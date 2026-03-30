from __future__ import annotations

import json
from datetime import datetime, timezone

from lolo_lead_management.domain.models import ExplorationMemoryState, SearchRunSnapshot, ShortlistRecord, SourcingDossier
from lolo_lead_management.infrastructure.sqlite import SqliteDatabase
from lolo_lead_management.ports.stores import ExplorationMemoryStore, LeadStore, SearchRunStore, ShortlistStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteLeadStore(LeadStore):
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def register_accepted_lead(self, run_id: str, payload: dict) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO accepted_leads (run_id, lead_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, payload["lead_id"], json.dumps(payload), utc_now()),
            )

    def register_rejected_candidate(self, run_id: str, payload: dict) -> None:
        candidate_key = payload.get("company_name") or payload.get("query_used") or "candidate"
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO rejected_candidates (run_id, candidate_key, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, candidate_key, json.dumps(payload), utc_now()),
            )


class SqliteSearchRunStore(SearchRunStore):
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def get_run(self, run_id: str) -> SearchRunSnapshot | None:
        with self._database.connect() as connection:
            row = connection.execute("SELECT payload_json FROM search_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return SearchRunSnapshot.model_validate_json(row["payload_json"])

    def save_run(self, run: SearchRunSnapshot) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO search_runs (run_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at
                """,
                (run.run_id, run.model_dump_json(), run.created_at.isoformat(), run.updated_at.isoformat()),
            )

    def register_source_trace(self, run_id: str, trace: SourcingDossier) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO source_traces (run_id, payload_json, created_at) VALUES (?, ?, ?)",
                (run_id, trace.model_dump_json(), utc_now()),
            )

    def register_search_run_result(self, run: SearchRunSnapshot) -> None:
        self.save_run(run)


class SqliteShortlistStore(ShortlistStore):
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def save_pending_shortlist(self, shortlist: ShortlistRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO shortlists (shortlist_id, run_id, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(shortlist_id) DO UPDATE SET payload_json = excluded.payload_json, status = excluded.status
                """,
                (shortlist.shortlist_id, shortlist.run_id, shortlist.model_dump_json(), "pending", shortlist.created_at.isoformat()),
            )

    def get_pending_shortlist(self, shortlist_id: str) -> ShortlistRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM shortlists WHERE shortlist_id = ? AND status = 'pending'",
                (shortlist_id,),
            ).fetchone()
        if row is None:
            return None
        return ShortlistRecord.model_validate_json(row["payload_json"])

    def clear_pending_shortlist(self, shortlist_id: str) -> None:
        with self._database.connect() as connection:
            connection.execute("UPDATE shortlists SET status = 'cleared' WHERE shortlist_id = ?", (shortlist_id,))


class SqliteExplorationMemoryStore(ExplorationMemoryStore):
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def get_campaign_state(self) -> ExplorationMemoryState:
        with self._database.connect() as connection:
            row = connection.execute("SELECT payload_json FROM exploration_memory WHERE scope = 'global'").fetchone()
        if row is None:
            state = ExplorationMemoryState()
            self.save_campaign_state(state)
            return state
        return ExplorationMemoryState.model_validate_json(row["payload_json"])

    def save_campaign_state(self, state: ExplorationMemoryState) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO exploration_memory (scope, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at
                """,
                (state.scope, state.model_dump_json(), utc_now()),
            )

    def reset_query_memory(self, reset_fields: list[str], *, include_registered_lead_names: bool) -> None:
        state = self.get_campaign_state()
        if "queryHistory" in reset_fields:
            state.query_history = []
        if "visitedUrls" in reset_fields:
            state.visited_urls = []
        if "searchedCompanyNames" in reset_fields:
            state.searched_company_names = []
        if "consecutiveHardMissRuns" in reset_fields:
            state.consecutive_hard_miss_runs = 0
        if include_registered_lead_names:
            state.registered_lead_names = []
        self.save_campaign_state(state)
