from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.enums import QualificationOutcome
from lolo_lead_management.domain.models import AcceptedLeadRecord, ShortlistOption, ShortlistRecord
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.crm import CrmWriterPort
from lolo_lead_management.ports.stores import ExplorationMemoryStore, LeadStore, SearchRunStore, ShortlistStore


class CrmWriteStage:
    def __init__(
        self,
        *,
        lead_store: LeadStore,
        run_store: SearchRunStore,
        shortlist_store: ShortlistStore,
        memory_store: ExplorationMemoryStore,
        crm_writer: CrmWriterPort,
        shortlist_size: int,
    ) -> None:
        self._lead_store = lead_store
        self._run_store = run_store
        self._shortlist_store = shortlist_store
        self._memory_store = memory_store
        self._crm_writer = crm_writer
        self._shortlist_size = shortlist_size

    def execute(self, state: EngineRuntimeState) -> None:
        dossier = state.current_dossier
        qualification = state.current_qualification
        commercial = state.current_commercial
        if dossier is None or qualification is None:
            return

        if dossier.query_used:
            state.memory.query_history.append(dossier.query_used)
        state.memory.visited_urls = list({*state.memory.visited_urls, *[item.url for item in dossier.evidence]})

        person_name = self._sanitize_person_name(dossier.person.full_name) if dossier.person else None
        role_title = self._sanitize_role_title(dossier.person.role_title) if dossier.person else None

        if qualification.outcome == QualificationOutcome.ACCEPT and dossier.company and commercial:
            accepted = AcceptedLeadRecord(
                person_name=person_name,
                role_title=role_title,
                company_name=dossier.company.name,
                website=dossier.company.website,
                country_code=dossier.company.country_code,
                evidence=dossier.evidence,
                qualification=qualification,
                commercial=commercial,
            )
            state.run.accepted_leads.append(accepted)
            state.memory.searched_company_names.append(dossier.company.name)
            if accepted.person_name:
                state.memory.registered_lead_names.append(accepted.person_name)
            self._lead_store.register_accepted_lead(state.run.run_id, accepted.model_dump(mode="json"))
            self._crm_writer.upsert_accepted_lead(state.run, accepted)
        elif qualification.outcome == QualificationOutcome.REJECT_CLOSE_MATCH and dossier.company and commercial:
            if len(state.run.shortlist_options) < self._shortlist_size and qualification.close_match is not None:
                option = ShortlistOption(
                    option_number=len(state.run.shortlist_options) + 1,
                    company_name=dossier.company.name,
                    person_name=person_name,
                    summary=qualification.summary,
                    close_match=qualification.close_match,
                    qualification=qualification,
                    commercial=commercial,
                )
                state.run.shortlist_options.append(option)
            state.memory.searched_company_names.append(dossier.company.name)
            self._lead_store.register_rejected_candidate(
                state.run.run_id,
                {
                    "company_name": dossier.company.name,
                    "query_used": dossier.query_used,
                    "qualification": qualification.model_dump(mode="json"),
                },
            )
        else:
            if dossier.company:
                state.memory.searched_company_names.append(dossier.company.name)
            self._lead_store.register_rejected_candidate(
                state.run.run_id,
                {
                    "company_name": dossier.company.name if dossier.company else None,
                    "query_used": dossier.query_used,
                    "qualification": qualification.model_dump(mode="json"),
                },
            )

        state.memory.query_history = list(dict.fromkeys(state.memory.query_history))
        state.memory.visited_urls = list(dict.fromkeys(state.memory.visited_urls))
        state.memory.searched_company_names = list(dict.fromkeys(state.memory.searched_company_names))
        state.memory.registered_lead_names = list(dict.fromkeys(state.memory.registered_lead_names))

        if state.run.shortlist_options:
            shortlist = ShortlistRecord(
                shortlist_id=state.run.shortlist_id or f"short_{state.run.run_id.removeprefix('run_')}",
                run_id=state.run.run_id,
                options=state.run.shortlist_options,
                created_at=datetime.now(timezone.utc),
            )
            state.run.shortlist_id = shortlist.shortlist_id
            self._shortlist_store.save_pending_shortlist(shortlist)
            self._crm_writer.save_shortlist(shortlist)

        self._run_store.register_source_trace(state.run.run_id, dossier)
        self._memory_store.save_campaign_state(state.memory)

    def _sanitize_person_name(self, value: str | None) -> str | None:
        normalized = " ".join((value or "").split()).strip()
        if not normalized or len(normalized) > 80 or len(normalized.split()) > 6 or "industries:" in normalized.lower():
            return None
        return normalized

    def _sanitize_role_title(self, value: str | None) -> str | None:
        normalized = " ".join((value or "").split()).strip()
        if not normalized or len(normalized) > 80:
            return None
        return normalized
