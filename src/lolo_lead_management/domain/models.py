from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import MatchType, PlannerAction, QualificationOutcome, RunStatus, SearchAction, SourcingStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LeadSearchConstraints(StrictModel):
    target_count: int = Field(default=3, ge=1, le=20)
    preferred_country: str | None = Field(default=None, min_length=2, max_length=2)
    preferred_regions: list[str] = Field(default_factory=list)
    min_company_size: int | None = Field(default=None, ge=1, le=100000)
    max_company_size: int | None = Field(default=None, ge=1, le=100000)
    prefer_named_person: bool = True
    hard_constraints: list[str] = Field(default_factory=list)
    relaxable_constraints: list[str] = Field(default_factory=list)

    @field_validator("preferred_country")
    @classmethod
    def normalize_country(cls, value: str | None) -> str | None:
        return value.lower() if value else value


class NormalizedLeadSearchRequest(StrictModel):
    action: SearchAction = SearchAction.LEAD_SEARCH_START
    request_id: str = Field(default_factory=lambda: f"req_{uuid4().hex[:12]}")
    user_text: str = Field(min_length=1)
    constraints: LeadSearchConstraints = Field(default_factory=LeadSearchConstraints)
    buyer_targets: list[str] = Field(default_factory=list)
    search_themes: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class LeadSearchStartRequest(StrictModel):
    user_text: str = Field(min_length=1)
    request_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(StrictModel):
    url: str
    title: str
    snippet: str
    source_type: str


class PersonCandidate(StrictModel):
    full_name: str | None = None
    role_title: str | None = None


class CompanyCandidate(StrictModel):
    name: str
    website: str | None = None
    country_code: str | None = None
    employee_estimate: int | None = None


class SourcingDossier(StrictModel):
    sourcing_status: SourcingStatus
    query_used: str | None = None
    person: PersonCandidate | None = None
    company: CompanyCandidate | None = None
    fit_signals: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CloseMatch(StrictModel):
    summary: str
    missed_filters: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class QualificationDecision(StrictModel):
    outcome: QualificationOutcome
    match_type: MatchType | None = None
    score: int = Field(default=0, ge=0, le=100)
    summary: str
    reasons: list[str] = Field(default_factory=list)
    type: str | None = None
    region: str | None = None
    close_match: CloseMatch | None = None


class CommercialBundle(StrictModel):
    source_notes: str
    hooks: list[str] = Field(default_factory=list)
    fit_summary: str
    connection_note_draft: str
    dm_draft: str
    email_subject: str
    email_body: str


class AcceptedLeadRecord(StrictModel):
    lead_id: str = Field(default_factory=lambda: f"lead_{uuid4().hex[:12]}")
    person_name: str | None = None
    role_title: str | None = None
    company_name: str
    website: str | None = None
    country_code: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    qualification: QualificationDecision
    commercial: CommercialBundle


class ShortlistOption(StrictModel):
    option_number: int = Field(ge=1)
    company_name: str
    person_name: str | None = None
    summary: str
    close_match: CloseMatch
    qualification: QualificationDecision
    commercial: CommercialBundle


class ShortlistRecord(StrictModel):
    shortlist_id: str = Field(default_factory=lambda: f"short_{uuid4().hex[:12]}")
    run_id: str
    options: list[ShortlistOption] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ExplorationMemoryState(StrictModel):
    scope: Literal["global"] = "global"
    query_history: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    searched_company_names: list[str] = Field(default_factory=list)
    registered_lead_names: list[str] = Field(default_factory=list)
    consecutive_hard_miss_runs: int = Field(default=0, ge=0)


class SearchBudget(StrictModel):
    source_attempt_budget: int = Field(default=6, ge=1)
    enrich_attempt_budget: int = Field(default=1, ge=0)
    source_attempts_used: int = Field(default=0, ge=0)
    enrich_attempts_used: int = Field(default=0, ge=0)
    search_calls_used: int = Field(default=0, ge=0)

    def can_source(self) -> bool:
        return self.source_attempts_used < self.source_attempt_budget

    def can_enrich(self) -> bool:
        return self.enrich_attempts_used < self.enrich_attempt_budget


class RunIteration(StrictModel):
    index: int = Field(ge=1)
    planner_action: PlannerAction
    query: str | None = None
    dossier: SourcingDossier | None = None
    qualification: QualificationDecision | None = None


class SearchRunSnapshot(StrictModel):
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    request: NormalizedLeadSearchRequest
    status: RunStatus = RunStatus.RUNNING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    accepted_leads: list[AcceptedLeadRecord] = Field(default_factory=list)
    shortlist_id: str | None = None
    shortlist_options: list[ShortlistOption] = Field(default_factory=list)
    iterations: list[RunIteration] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    budget: SearchBudget = Field(default_factory=SearchBudget)
    applied_relaxation_stage: int = 0
    completed_reason: str | None = None


class StageDecision(StrictModel):
    action: PlannerAction
    relaxation_stage: int = Field(default=0, ge=0, le=2)
    reason: str


class LeadSearchStartResponse(StrictModel):
    run_id: str
    status: RunStatus
    normalized_request: NormalizedLeadSearchRequest
    accepted_leads: list[AcceptedLeadRecord] = Field(default_factory=list)
    shortlist_id: str | None = None
    shortlist_options: list[ShortlistOption] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    budget_summary: SearchBudget
    applied_relaxation_stage: int
    completed_reason: str | None = None


class QueryMemoryResetRequest(StrictModel):
    reset: list[str] = Field(default_factory=list)
    include_registered_lead_names: bool = False


class QueryMemoryResetResponse(StrictModel):
    scope: str
    reset_fields: list[str]


class ShortlistSelectRequest(StrictModel):
    option_number: int = Field(ge=1)


class HealthResponse(StrictModel):
    status: str
    environment: str
    database_path: str
    tavily_configured: bool
    search_provider: str
    lm_studio_configured: bool
    llm_enabled: bool
    search_enabled: bool
