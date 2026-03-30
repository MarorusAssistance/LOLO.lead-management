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

from .container import ServiceContainer


def start_lead_search(container: ServiceContainer, payload: LeadSearchStartRequest) -> LeadSearchStartResponse:
    return container.engine.start(payload)


def get_run(container: ServiceContainer, run_id: str) -> SearchRunSnapshot | None:
    return container.engine.get_run(run_id)


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
