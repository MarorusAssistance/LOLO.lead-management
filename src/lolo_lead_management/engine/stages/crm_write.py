from __future__ import annotations

from datetime import datetime, timezone

from lolo_lead_management.domain.enums import QualificationOutcome
from lolo_lead_management.domain.models import AcceptedLeadRecord, CompanyObservation, ShortlistOption, ShortlistRecord
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.engine.rules import domain_from_url
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
        official_domain = domain_from_url(dossier.company.website if dossier.company else None)
        if official_domain and dossier.website_resolution and dossier.website_resolution.officiality in {"confirmed", "probable"}:
            state.memory.blocked_official_domains.append(official_domain)
        observation = self._build_company_observation(state, official_domain)
        if observation is not None:
            state.memory.company_observations = self._upsert_company_observation(
                state.memory.company_observations,
                observation,
            )
            if state.run.iterations:
                state.run.iterations[-1].company_observation_written = True

        person_name = self._sanitize_person_name(dossier.person.full_name) if dossier.person else None
        role_title = self._sanitize_role_title(dossier.person.role_title) if dossier.person else None

        if qualification.outcome == QualificationOutcome.ACCEPT and dossier.company and commercial:
            accepted = AcceptedLeadRecord(
                person_name=person_name,
                role_title=role_title,
                lead_source_type=dossier.lead_source_type,
                person_confidence=dossier.person_confidence,
                primary_person_source_url=dossier.primary_person_source_url,
                company_name=dossier.company.name,
                website=dossier.company.website,
                website_resolution=dossier.website_resolution,
                country_code=dossier.company.country_code,
                evidence=dossier.evidence,
                qualification=qualification,
                commercial=commercial,
                research_trace=dossier.research_trace,
                field_evidence=dossier.field_evidence,
                contradictions=dossier.contradictions,
                evidence_quality=dossier.evidence_quality,
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
                    role_title=role_title,
                    lead_source_type=dossier.lead_source_type,
                    person_confidence=dossier.person_confidence,
                    primary_person_source_url=dossier.primary_person_source_url,
                    website=dossier.company.website,
                    website_resolution=dossier.website_resolution,
                    country_code=dossier.company.country_code,
                    summary=qualification.summary,
                    close_match=qualification.close_match,
                    qualification=qualification,
                    commercial=commercial,
                    evidence=dossier.evidence,
                    research_trace=dossier.research_trace,
                    field_evidence=dossier.field_evidence,
                    contradictions=dossier.contradictions,
                    evidence_quality=dossier.evidence_quality,
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
            if dossier.company and qualification.outcome == QualificationOutcome.REJECT:
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
        state.memory.blocked_official_domains = list(dict.fromkeys(state.memory.blocked_official_domains))
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

    def _build_company_observation(self, state: EngineRuntimeState, official_domain: str | None) -> CompanyObservation | None:
        dossier = state.current_dossier
        qualification = state.current_qualification
        focus = state.current_focus_company_resolution
        if dossier is None or qualification is None:
            return None
        company_name = (focus.selected_company if focus and focus.selected_company else dossier.company.name if dossier.company else None)
        if not company_name:
            return None
        anchored_trace = state.current_anchored_source_trace
        size_type = anchored_trace.size_hint_type if anchored_trace else None
        size_value = anchored_trace.size_hint_value if anchored_trace else None
        employee_count_exact = size_value if size_type == "exact" else None
        employee_count_range_max = size_value if size_type == "range" else None
        employee_count_estimate = (
            size_value if size_type == "estimate" else dossier.company.employee_estimate if dossier.company else None
        )
        operational_status = "unknown"
        if anchored_trace and anchored_trace.operational_status_hint == "non_operational":
            operational_status = "non_operational"
        elif anchored_trace and anchored_trace.operational_status_hint:
            operational_status = "active"
        return CompanyObservation(
            company_name=company_name,
            legal_name=focus.legal_name if focus else dossier.company.name if dossier.company else None,
            query_name=focus.query_name if focus else dossier.company.name if dossier.company else None,
            official_domain=official_domain or (anchored_trace.official_domain if anchored_trace else None),
            country_code=dossier.company.country_code if dossier.company else None,
            employee_count_exact=employee_count_exact,
            employee_count_range_max=employee_count_range_max,
            employee_count_estimate=employee_count_estimate,
            location_hint=dossier.company.country_code if dossier.company else None,
            theme_tags=dossier.fit_signals,
            operational_status=operational_status,
            last_outcome=qualification.outcome.value,
            rejection_reasons=qualification.reasons,
            last_seen_at=datetime.now(timezone.utc),
        )

    def _upsert_company_observation(
        self,
        current: list[CompanyObservation],
        observation: CompanyObservation,
    ) -> list[CompanyObservation]:
        updated: list[CompanyObservation] = []
        replaced = False
        for item in current:
            same_company = item.company_name.lower() == observation.company_name.lower()
            same_domain = bool(
                item.official_domain
                and observation.official_domain
                and item.official_domain.lower() == observation.official_domain.lower()
            )
            if same_company or same_domain:
                updated.append(observation)
                replaced = True
            else:
                updated.append(item)
        if not replaced:
            updated.append(observation)
        return updated

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
