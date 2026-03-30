from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from lolo_lead_management.api.deps import get_container
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.application.use_cases import select_shortlist_option
from lolo_lead_management.domain.models import SearchRunSnapshot, ShortlistSelectRequest

router = APIRouter(tags=["shortlists"])


@router.post("/shortlists/{shortlist_id}/select", response_model=SearchRunSnapshot)
def shortlist_select(
    shortlist_id: str,
    payload: ShortlistSelectRequest,
    container: ServiceContainer = Depends(get_container),
) -> SearchRunSnapshot:
    run = select_shortlist_option(container, shortlist_id, payload.option_number)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shortlist or option not found")
    return run
