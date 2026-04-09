from __future__ import annotations

from lolo_lead_management.domain.models import (
    AcceptedLeadRecord,
    HealthResponse,
    LeadSearchStartRequest,
    LeadSearchStartResponse,
    QueryMemoryResetRequest,
    QueryMemoryResetResponse,
    SearchRunSnapshot,
)
from lolo_lead_management.engine.rules import domain_from_url

from .container import ServiceContainer


def start_lead_search(container: ServiceContainer, payload: LeadSearchStartRequest) -> LeadSearchStartResponse:
    return container.engine.start(payload)


def initialize_lead_search(container: ServiceContainer, payload: LeadSearchStartRequest) -> SearchRunSnapshot:
    return container.engine.initialize_run(payload)


def execute_lead_search(container: ServiceContainer, run_id: str, *, raise_on_error: bool = False) -> SearchRunSnapshot:
    return container.engine.run_to_completion(run_id, raise_on_error=raise_on_error)


def build_start_response(container: ServiceContainer, run: SearchRunSnapshot) -> LeadSearchStartResponse:
    return container.engine.build_start_response(run)


def get_run(container: ServiceContainer, run_id: str) -> SearchRunSnapshot | None:
    return container.engine.get_run(run_id)


def get_shortlist(container: ServiceContainer, shortlist_id: str):
    return container.engine.get_shortlist(shortlist_id)


def get_shortlist_option(container: ServiceContainer, shortlist_id: str, option_number: int):
    return container.engine.get_shortlist_option(shortlist_id, option_number)


def select_shortlist_option(container: ServiceContainer, shortlist_id: str, option_number: int) -> SearchRunSnapshot | None:
    run = container.engine.select_shortlist_option(shortlist_id, option_number)
    if run is None:
        return None

    selected = run.accepted_leads[-1]
    if isinstance(selected, AcceptedLeadRecord):
        container.lead_store.register_accepted_lead(run.run_id, selected.model_dump(mode="json"))
        container.crm_writer.upsert_accepted_lead(run, selected)
        memory = container.memory_store.get_campaign_state()
        memory.searched_company_names = list(dict.fromkeys(memory.searched_company_names + [selected.company_name]))
        official_domain = domain_from_url(selected.website)
        if official_domain and selected.website_resolution and selected.website_resolution.officiality in {"confirmed", "probable"}:
            memory.blocked_official_domains = list(dict.fromkeys(memory.blocked_official_domains + [official_domain]))
        if selected.person_name:
            memory.registered_lead_names = list(dict.fromkeys(memory.registered_lead_names + [selected.person_name]))
        container.memory_store.save_campaign_state(memory)
    return run


def reset_query_memory(container: ServiceContainer, payload: QueryMemoryResetRequest) -> QueryMemoryResetResponse:
    container.memory_store.reset_query_memory(
        payload.reset,
        include_registered_lead_names=payload.include_registered_lead_names,
    )
    return QueryMemoryResetResponse(scope="global", reset_fields=payload.reset)


def health(container: ServiceContainer) -> HealthResponse:
    settings = container.settings
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        database_path=settings.database_path,
        tavily_configured=bool(settings.tavily_api_key),
        search_provider="tavily",
        lm_studio_configured=bool(settings.lm_studio_base_url and settings.lm_studio_model),
        llm_enabled=settings.llm_enabled,
        search_enabled=settings.search_enabled,
    )
