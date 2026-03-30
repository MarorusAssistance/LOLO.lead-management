from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from lolo_lead_management.api.deps import get_container
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.application.use_cases import get_run, start_lead_search
from lolo_lead_management.domain.models import LeadSearchStartRequest, LeadSearchStartResponse, SearchRunSnapshot

router = APIRouter(tags=["runs"])


@router.post("/lead-search/start", response_model=LeadSearchStartResponse, status_code=status.HTTP_200_OK)
def start_run(
    payload: LeadSearchStartRequest,
    container: ServiceContainer = Depends(get_container),
) -> LeadSearchStartResponse:
    return start_lead_search(container, payload)


@router.get("/runs/{run_id}", response_model=SearchRunSnapshot)
def get_run_snapshot(run_id: str, container: ServiceContainer = Depends(get_container)) -> SearchRunSnapshot:
    snapshot = get_run(container, run_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return snapshot
