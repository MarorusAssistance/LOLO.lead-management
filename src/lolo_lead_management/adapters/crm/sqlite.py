from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.models import AcceptedLeadRecord, SearchRunSnapshot, ShortlistRecord
from lolo_lead_management.infrastructure.sqlite import SqliteDatabase
from lolo_lead_management.ports.crm import CrmWriterPort


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteCrmWriter(CrmWriterPort):
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def upsert_accepted_lead(self, run: SearchRunSnapshot, lead: AcceptedLeadRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO crm_records (run_id, lead_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (run.run_id, lead.lead_id, lead.model_dump_json(), utc_now()),
            )

    def save_shortlist(self, shortlist: ShortlistRecord) -> None:
        _ = shortlist
