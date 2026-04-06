from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from lolo_lead_management.api.deps import get_container
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.application.use_cases import (
    build_start_response,
    execute_lead_search,
    get_run,
    initialize_lead_search,
    start_lead_search,
)
from lolo_lead_management.domain.models import LeadSearchStartRequest, LeadSearchStartResponse, SearchRunSnapshot

router = APIRouter(tags=["runs"])


@router.post("/lead-search/start", response_model=LeadSearchStartResponse, status_code=status.HTTP_200_OK)
def start_run(
    payload: LeadSearchStartRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    container: ServiceContainer = Depends(get_container),
) -> LeadSearchStartResponse:
    if not payload.wait_for_completion:
        run = initialize_lead_search(container, payload)
        background_tasks.add_task(execute_lead_search, container, run.run_id, raise_on_error=False)
        response.status_code = status.HTTP_202_ACCEPTED
        return build_start_response(container, run)
    return start_lead_search(container, payload)


@router.get("/runs/{run_id}", response_model=SearchRunSnapshot)
def get_run_snapshot(run_id: str, container: ServiceContainer = Depends(get_container)) -> SearchRunSnapshot:
    snapshot = get_run(container, run_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return snapshot
