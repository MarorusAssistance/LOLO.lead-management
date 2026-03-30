from __future__ import annotations

from fastapi import APIRouter, Depends

from lolo_lead_management.api.deps import get_container
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.application.use_cases import reset_query_memory
from lolo_lead_management.domain.models import QueryMemoryResetRequest, QueryMemoryResetResponse

router = APIRouter(tags=["memory"])


@router.post("/query-memory/reset", response_model=QueryMemoryResetResponse)
def reset_memory(
    payload: QueryMemoryResetRequest,
    container: ServiceContainer = Depends(get_container),
) -> QueryMemoryResetResponse:
    return reset_query_memory(container, payload)
