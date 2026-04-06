from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from lolo_lead_management.api.deps import get_container
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.application.use_cases import get_shortlist, get_shortlist_option, select_shortlist_option
from lolo_lead_management.domain.models import SearchRunSnapshot, ShortlistOption, ShortlistRecord, ShortlistSelectRequest

router = APIRouter(tags=["shortlists"])


@router.get("/shortlists/{shortlist_id}", response_model=ShortlistRecord)
def shortlist_detail(
    shortlist_id: str,
    container: ServiceContainer = Depends(get_container),
) -> ShortlistRecord:
    shortlist = get_shortlist(container, shortlist_id)
    if shortlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shortlist not found")
    return shortlist


@router.get("/shortlists/{shortlist_id}/options/{option_number}", response_model=ShortlistOption)
def shortlist_option_detail(
    shortlist_id: str,
    option_number: int,
    container: ServiceContainer = Depends(get_container),
) -> ShortlistOption:
    option = get_shortlist_option(container, shortlist_id, option_number)
    if option is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shortlist option not found")
    return option


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
