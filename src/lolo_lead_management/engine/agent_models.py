from __future__ import annotations

from pydantic import Field

from lolo_lead_management.domain.models import StrictModel


class SourceQueryPlan(StrictModel):
    suggested_queries: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
