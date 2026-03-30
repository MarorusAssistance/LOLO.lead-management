from __future__ import annotations

from fastapi import APIRouter, Depends

from lolo_lead_management.api.deps import get_container
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.application.use_cases import health
from lolo_lead_management.domain.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(container: ServiceContainer = Depends(get_container)) -> HealthResponse:
    return health(container)
