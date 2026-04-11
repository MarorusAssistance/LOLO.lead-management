from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    FieldEvidenceStatus,
    MatchType,
    PlannerAction,
    QualificationOutcome,
    RunStatus,
    SearchAction,
    SourceQuality,
    SourcingStatus,
    StageName,
)


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
    wait_for_completion: bool = True


class PersonCandidate(StrictModel):
    full_name: str | None = None
    role_title: str | None = None


class CompanyCandidate(StrictModel):
    name: str
    website: str | None = None
    country_code: str | None = None
    employee_estimate: int | None = None


class EvidenceItem(StrictModel):
    url: str
    title: str
    snippet: str
    source_type: str
    raw_content: str = ""
    domain: str | None = None
    search_score: float | None = Field(default=None, ge=0, le=1)
    query_planned: str | None = None
    query_executed: str | None = None
    research_phase: str | None = None
    objective: str | None = None
    source_quality: SourceQuality = SourceQuality.UNKNOWN
    source_tier: Literal["tier_a", "tier_b", "tier_c", "unknown"] = "unknown"
    company_anchor: str | None = None
    is_company_controlled_source: bool = False
    is_publisher_like: bool = False
    selected_for_field: Literal["company_name", "website", "country", "employee_estimate", "person_name", "role_title", "fit_signals", "multi"] | None = None
    why_selected: str | None = None


class DocumentBlock(StrictModel):
    index: int = Field(ge=1)
    block_type: Literal["heading", "paragraph", "list_item", "table_row", "link_group", "unknown"] = "unknown"
    text: str
    heading_level: int | None = Field(default=None, ge=1, le=6)
    heading_path: list[str] = Field(default_factory=list)


class LogicalSegment(StrictModel):
    segment_id: str
    segment_type: Literal["identity", "contact", "website", "employees", "governance", "fit", "legal", "faq", "noise", "unknown"] = "unknown"
    start_block: int = Field(ge=1)
    end_block: int = Field(ge=1)
    heading_path: list[str] = Field(default_factory=list)
    noise: bool = False
    discard_reason: str | None = None
    text: str


class EvidenceDocument(EvidenceItem):
    raw_html: str | None = None
    content_format: Literal["html", "text", "markdown", "mixed", "unknown"] = "unknown"
    normalized_blocks: list[DocumentBlock] = Field(default_factory=list)
    logical_segments: list[LogicalSegment] = Field(default_factory=list)
    chunker_version: str | None = None
    content_fingerprint: str | None = None
    chunker_adapter: str | None = None
    debug_markdown_artifact_path: str | None = None
    debug_markdown_preview: str | None = None


class PageCapture(StrictModel):
    url: str
    raw_html: str | None = None
    extracted_text: str = ""
    content_format: Literal["html", "text", "unknown"] = "unknown"
    content_type: str | None = None


class ResearchQuery(StrictModel):
    query: str = Field(min_length=3)
    objective: str = Field(min_length=3)
    research_phase: str = Field(min_length=3)
    source_role: Literal["entity_validation", "website_resolution", "employee_count_resolution", "governance_resolution", "signal_detection"] | None = None
    candidate_company_name: str | None = None
    source_tier_target: Literal["tier_a", "tier_b", "tier_c"] | None = None
    expected_field: Literal["company_name", "website", "country", "employee_estimate", "person_name", "role_title", "fit_signals", "multi"] | None = None
    stop_if_resolved: bool = False
    search_depth: str = Field(default="basic", pattern="^(basic|advanced|fast|ultra-fast)$")
    min_score: float = Field(default=0, ge=0, le=1)
    preferred_domains: list[str] = Field(default_factory=list)
    excluded_domains: list[str] = Field(default_factory=list)
    exact_match: bool = False
    country: str | None = None
    expected_source_types: list[str] = Field(default_factory=list)


class ResearchQueryPlan(StrictModel):
    planned_queries: list[ResearchQuery] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)


class ResearchTraceEntry(StrictModel):
    query_planned: str
    query_executed: str
    research_phase: str
    objective: str
    source_role: Literal["entity_validation", "website_resolution", "employee_count_resolution", "governance_resolution", "signal_detection"] | None = None
    candidate_company_name: str | None = None
    source_tier_target: Literal["tier_a", "tier_b", "tier_c"] | None = None
    expected_field: Literal["company_name", "website", "country", "employee_estimate", "person_name", "role_title", "fit_signals", "multi"] | None = None
    documents_considered: int = Field(default=0, ge=0)
    documents_selected: int = Field(default=0, ge=0)
    selected_urls: list[str] = Field(default_factory=list)


class AssembledFieldEvidence(StrictModel):
    field_name: str
    value: str | int | None = None
    status: FieldEvidenceStatus
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceItem] = Field(default_factory=list)
    source_quality: SourceQuality = SourceQuality.UNKNOWN
    source_tier: Literal["tier_a", "tier_b", "tier_c", "mixed", "unknown"] = "unknown"
    support_type: Literal["explicit", "corroborated", "weak_inference"] = "explicit"
    reasoning_note: str


class WebsiteResolution(StrictModel):
    candidate_website: str | None = None
    officiality: Literal["confirmed", "probable", "unknown"] = "unknown"
    confidence: float = Field(default=0, ge=0, le=1)
    evidence_urls: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class WebsiteCandidateHint(StrictModel):
    candidate_website: str
    evidence_urls: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    score: float = Field(default=0, ge=0)


class RejectedCompanyCandidate(StrictModel):
    company_name: str
    reason: str
    evidence_urls: list[str] = Field(default_factory=list)


class DiscoveryCompanyCandidate(StrictModel):
    company_name: str
    legal_name: str | None = None
    query_name: str | None = None
    brand_aliases: list[str] = Field(default_factory=list)
    country_code: str | None = None
    location_hint: str | None = None
    theme_tags: list[str] = Field(default_factory=list)
    candidate_website: str | None = None
    employee_count_hint_value: int | None = Field(default=None, ge=0)
    employee_count_hint_type: Literal["exact", "range", "estimate", "unknown"] = "unknown"
    operational_status: Literal["active", "non_operational", "unknown"] = "unknown"
    evidence_urls: list[str] = Field(default_factory=list)
    support_type: Literal["explicit", "corroborated", "weak_inference"] = "explicit"
    evidence_excerpt: str = ""
    is_real_company_candidate: bool = True
    rejection_reason: str | None = None
    selection_score: float = Field(default=0, ge=0)
    selection_reasons: list[str] = Field(default_factory=list)
    hard_rejections: list[str] = Field(default_factory=list)


class DiscoveryCandidateExtractionResolution(StrictModel):
    segment_company_name: str | None = None
    discovery_candidates: list[DiscoveryCompanyCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CompanyObservation(StrictModel):
    company_name: str
    legal_name: str | None = None
    query_name: str | None = None
    official_domain: str | None = None
    country_code: str | None = None
    employee_count_exact: int | None = Field(default=None, ge=0)
    employee_count_range_max: int | None = Field(default=None, ge=0)
    employee_count_estimate: int | None = Field(default=None, ge=0)
    location_hint: str | None = None
    theme_tags: list[str] = Field(default_factory=list)
    operational_status: Literal["active", "non_operational", "unknown"] = "unknown"
    last_outcome: str | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    last_seen_at: datetime = Field(default_factory=utc_now)


class CompanyFocusResolution(StrictModel):
    selected_company: str | None = None
    legal_name: str | None = None
    query_name: str | None = None
    brand_aliases: list[str] = Field(default_factory=list)
    selection_mode: Literal["confident", "plausible", "fallback", "none"] = "none"
    confidence: float = Field(default=0, ge=0, le=1)
    evidence_urls: list[str] = Field(default_factory=list)
    selection_reasons: list[str] = Field(default_factory=list)
    hard_rejections: list[str] = Field(default_factory=list)
    rejected_candidates: list[RejectedCompanyCandidate] = Field(default_factory=list)
    discovery_candidates: list[DiscoveryCompanyCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ChunkFieldAssertion(StrictModel):
    field_name: Literal["company_name", "website", "country", "employee_estimate"]
    company_name: str | None = None
    value: str | int | None = None
    status: FieldEvidenceStatus
    support_type: Literal["explicit", "corroborated", "weak_inference"] = "explicit"
    reasoning_note: str = ""
    segment_index: int = Field(default=0, ge=0)
    source_url: str = ""
    evidence_excerpt: str = ""
    employee_count_type: Literal["exact", "range", "estimate", "unknown"] = "unknown"


class ChunkContactAssertion(StrictModel):
    person_name: str | None = None
    role_title: str | None = None
    company_name: str | None = None
    status: FieldEvidenceStatus
    support_type: Literal["explicit", "corroborated", "weak_inference"] = "explicit"
    reasoning_note: str = ""
    segment_index: int = Field(default=0, ge=0)
    source_url: str = ""
    evidence_excerpt: str = ""


class ChunkExtractionResolution(StrictModel):
    segment_company_name: str | None = None
    field_assertions: list[ChunkFieldAssertion] = Field(default_factory=list)
    contact_assertions: list[ChunkContactAssertion] = Field(default_factory=list)
    fit_signals: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AssemblyFieldAssertion(StrictModel):
    field_name: Literal["company_name", "website", "country", "employee_estimate", "person_name", "role_title"]
    value: str | int | None = None
    status: FieldEvidenceStatus
    evidence_urls: list[str] = Field(default_factory=list)
    contradicting_urls: list[str] = Field(default_factory=list)
    source_tier: Literal["tier_a", "tier_b", "tier_c", "mixed", "unknown"] = "unknown"
    support_type: Literal["explicit", "corroborated", "weak_inference"] = "explicit"
    reasoning_note: str = ""


class AssemblyResolution(StrictModel):
    subject_company_name: str | None = None
    website: str | None = None
    candidate_website: str | None = None
    website_officiality: Literal["confirmed", "probable", "unknown"] | None = None
    website_confidence: float | None = Field(default=None, ge=0, le=1)
    website_evidence_urls: list[str] = Field(default_factory=list)
    website_signals: list[str] = Field(default_factory=list)
    website_risks: list[str] = Field(default_factory=list)
    country_code: str | None = None
    employee_estimate: int | None = None
    person_name: str | None = None
    role_title: str | None = None
    fit_signals: list[str] = Field(default_factory=list)
    selected_evidence_urls: list[str] = Field(default_factory=list)
    field_assertions: list[AssemblyFieldAssertion] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class QualificationRubricField(StrictModel):
    field_name: str
    status: FieldEvidenceStatus
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceItem] = Field(default_factory=list)
    source_quality: SourceQuality = SourceQuality.UNKNOWN
    source_tier: Literal["tier_a", "tier_b", "tier_c", "mixed", "unknown"] = "unknown"
    support_type: Literal["explicit", "corroborated", "weak_inference"] = "explicit"
    reasoning_note: str


class QualificationRubric(StrictModel):
    fields: list[QualificationRubricField] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    meddicc_signals: list[str] = Field(default_factory=list)
    overall_confidence: int = Field(default=0, ge=0, le=100)


class AssembledLeadDossier(StrictModel):
    sourcing_status: SourcingStatus
    query_used: str | None = None
    person: PersonCandidate | None = None
    company: CompanyCandidate | None = None
    lead_source_type: Literal["functional_exec", "company_team_page", "speaker_or_event", "interview_or_press", "mercantile_directory", "legal_registry", "unknown"] | None = None
    person_confidence: Literal["strong", "corroborated", "weak", "unknown"] | None = None
    primary_person_source_url: str | None = None
    fit_signals: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    anchored_company_name: str | None = None
    website_resolution: WebsiteResolution | None = None
    research_trace: list[ResearchTraceEntry] = Field(default_factory=list)
    field_evidence: list[AssembledFieldEvidence] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    evidence_quality: SourceQuality = SourceQuality.UNKNOWN
    documents_considered: int = Field(default=0, ge=0)
    documents_selected: int = Field(default=0, ge=0)


class SourcingDossier(AssembledLeadDossier):
    pass


class SourcePassResult(StrictModel):
    sourcing_status: SourcingStatus
    query_plan: ResearchQueryPlan | None = None
    executed_queries: list[ResearchQuery] = Field(default_factory=list)
    documents: list[EvidenceDocument | EvidenceItem] = Field(default_factory=list)
    website_candidates: list[WebsiteCandidateHint] = Field(default_factory=list)
    anchored_company_name: str | None = None
    research_trace: list[ResearchTraceEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source_trace: "SourceStageTrace | None" = None


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
    qualification_rubric: QualificationRubric | None = None


class SearchResultTrace(StrictModel):
    url: str
    domain: str | None = None
    title: str | None = None
    source_type: str | None = None
    search_score: float | None = Field(default=None, ge=0, le=1)
    kept: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SourceTraceDocumentSnapshot(StrictModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    raw_content: str | None = None
    has_raw_html: bool = False
    content_format: Literal["html", "text", "markdown", "mixed", "unknown"] = "unknown"
    source_type: str | None = None
    domain: str | None = None
    source_tier: Literal["tier_a", "tier_b", "tier_c", "unknown"] = "unknown"
    source_quality: SourceQuality = SourceQuality.UNKNOWN
    company_anchor: str | None = None
    is_company_controlled_source: bool = False
    chunker_adapter: str | None = None
    chunker_version: str | None = None
    normalized_block_count: int = Field(default=0, ge=0)
    logical_segment_count: int = Field(default=0, ge=0)
    debug_markdown_artifact_path: str | None = None
    debug_markdown_preview: str | None = None
    raw_content_len_before: int | None = Field(default=None, ge=0)
    raw_content_len_after: int | None = Field(default=None, ge=0)
    enrichment_strategy_used: Literal["search_raw", "extract_pages", "fetch_page", "extract_then_fetch", "none"] | None = None
    extract_attempted: bool = False
    fetch_attempted: bool = False


class SourceQueryTrace(StrictModel):
    query: str
    objective: str
    research_phase: str
    source_role: Literal["entity_validation", "website_resolution", "employee_count_resolution", "governance_resolution", "signal_detection"] | None = None
    candidate_company_name: str | None = None
    source_tier_target: Literal["tier_a", "tier_b", "tier_c"] | None = None
    expected_field: Literal["company_name", "website", "country", "employee_estimate", "person_name", "role_title", "fit_signals", "multi"] | None = None
    preferred_domains: list[str] = Field(default_factory=list)
    excluded_domains: list[str] = Field(default_factory=list)
    max_results: int = Field(default=0, ge=0)
    raw_result_count: int = Field(default=0, ge=0)
    filtered_result_count: int = Field(default=0, ge=0)
    enriched_result_count: int = Field(default=0, ge=0)
    selected_result_count: int = Field(default=0, ge=0)
    selected_urls: list[str] = Field(default_factory=list)
    fetched_urls: list[str] = Field(default_factory=list)
    empty_fetch_urls: list[str] = Field(default_factory=list)
    results: list[SearchResultTrace] = Field(default_factory=list)
    raw_results_before_filter: list[SourceTraceDocumentSnapshot] = Field(default_factory=list)
    documents_after_enrichment: list[SourceTraceDocumentSnapshot] = Field(default_factory=list)
    documents_selected_for_pass: list[SourceTraceDocumentSnapshot] = Field(default_factory=list)
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class SourceAnchorCandidate(StrictModel):
    company_name: str
    support_count: int = Field(default=0, ge=0)
    evidence_urls: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SourceDocumentSelectionTrace(StrictModel):
    url: str
    domain: str | None = None
    selected_for_field: Literal["company_name", "website", "country", "employee_estimate", "person_name", "role_title", "fit_signals", "multi"] | None = None
    why_selected: str | None = None
    source_tier: Literal["tier_a", "tier_b", "tier_c", "unknown"] = "unknown"
    is_company_controlled_source: bool = False
    research_phase: str | None = None
    source_role: Literal["entity_validation", "website_resolution", "employee_count_resolution", "governance_resolution", "signal_detection"] | None = None
    expected_field: Literal["company_name", "website", "country", "employee_estimate", "person_name", "role_title", "fit_signals", "multi"] | None = None


class ChunkerDocumentTrace(StrictModel):
    url: str
    domain: str | None = None
    adapter: str | None = None
    had_raw_html: bool = False
    normalized_block_count: int = Field(default=0, ge=0)
    logical_segment_count: int = Field(default=0, ge=0)
    debug_markdown_artifact_path: str | None = None
    debug_markdown_preview: str | None = None
    notes: list[str] = Field(default_factory=list)


class ChunkerStageTrace(StrictModel):
    processed_documents: list[ChunkerDocumentTrace] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SourceStageTrace(StrictModel):
    mode: Literal["source", "enrich"] = "source"
    pass_kind: Literal["discovery_batch", "focus_locked_retrieval"] | None = None
    batch_traces: list[dict[str, Any]] = Field(default_factory=list)
    discovery_batches_considered: int = Field(default=1, ge=0)
    discovery_directory_selected: str | None = None
    discovery_directories_consumed_in_run: list[str] = Field(default_factory=list)
    discovery_ladder_position: int | None = Field(default=None, ge=1)
    llm_plan_status: Literal["ok", "llm_error", "llm_disabled", "fallback_only"] = "fallback_only"
    llm_plan_error: str | None = None
    llm_plan_input: dict[str, Any] | None = None
    llm_raw_plan: dict[str, Any] | None = None
    sanitized_query_plan: dict[str, Any] | None = None
    merged_query_plan: dict[str, Any] | None = None
    fallback_query_count: int = Field(default=0, ge=0)
    llm_query_count: int = Field(default=0, ge=0)
    merged_query_count: int = Field(default=0, ge=0)
    selected_query_count: int = Field(default=0, ge=0)
    query_history: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    request_scoped_company_exclusions: list[str] = Field(default_factory=list)
    selected_queries: list[str] = Field(default_factory=list)
    query_traces: list[SourceQueryTrace] = Field(default_factory=list)
    cross_company_rejections: list[str] = Field(default_factory=list)
    anchor_candidates: list[SourceAnchorCandidate] = Field(default_factory=list)
    anchor_raw_name: str | None = None
    anchored_company: str | None = None
    anchor_query_name: str | None = None
    anchor_brand_aliases: list[str] = Field(default_factory=list)
    anchor_confidence: str | None = None
    operational_status_hint: str | None = None
    size_hint_value: int | None = Field(default=None, ge=0)
    size_hint_type: Literal["exact", "range", "estimate", "unknown"] | None = None
    candidate_branch_stop_reason: str | None = None
    domain_validation_strategy: Literal["name_based", "domain_based"] | None = None
    focused_document_urls: list[str] = Field(default_factory=list)
    extract_candidate_urls: list[str] = Field(default_factory=list)
    extracted_urls: list[str] = Field(default_factory=list)
    extract_error: str | None = None
    official_domain: str | None = None
    website_candidates: list[WebsiteCandidateHint] = Field(default_factory=list)
    selected_documents: list[SourceDocumentSelectionTrace] = Field(default_factory=list)
    documents_passed_to_assembler: list[SourceTraceDocumentSnapshot] = Field(default_factory=list)
    excluded_terminal_company_documents: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class QualificationTrace(StrictModel):
    deterministic_decision: QualificationDecision
    llm_review: QualificationDecision | None = None
    llm_raw_output: dict[str, Any] | None = None
    llm_error: str | None = None
    merged_decision: QualificationDecision
    notes: list[str] = Field(default_factory=list)


class ContinueTrace(StrictModel):
    should_finish: bool = False
    should_continue: bool = False
    reasons: list[str] = Field(default_factory=list)
    target_count: int = Field(default=0, ge=0)
    accepted_count: int = Field(default=0, ge=0)
    shortlist_count: int = Field(default=0, ge=0)
    source_attempts_used: int = Field(default=0, ge=0)
    source_attempt_budget: int = Field(default=0, ge=0)
    enrich_attempts_used: int = Field(default=0, ge=0)
    enrich_attempt_budget: int = Field(default=0, ge=0)
    search_calls_used: int = Field(default=0, ge=0)
    search_call_budget: int = Field(default=0, ge=0)
    final_status: RunStatus
    completed_reason: str | None = None


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
    lead_source_type: Literal["functional_exec", "company_team_page", "speaker_or_event", "interview_or_press", "mercantile_directory", "legal_registry", "unknown"] | None = None
    person_confidence: Literal["strong", "corroborated", "weak", "unknown"] | None = None
    primary_person_source_url: str | None = None
    company_name: str
    website: str | None = None
    website_resolution: WebsiteResolution | None = None
    country_code: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    qualification: QualificationDecision
    commercial: CommercialBundle
    research_trace: list[ResearchTraceEntry] = Field(default_factory=list)
    field_evidence: list[AssembledFieldEvidence] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    evidence_quality: SourceQuality = SourceQuality.UNKNOWN


class ShortlistOption(StrictModel):
    option_number: int = Field(ge=1)
    company_name: str
    person_name: str | None = None
    role_title: str | None = None
    lead_source_type: Literal["functional_exec", "company_team_page", "speaker_or_event", "interview_or_press", "mercantile_directory", "legal_registry", "unknown"] | None = None
    person_confidence: Literal["strong", "corroborated", "weak", "unknown"] | None = None
    primary_person_source_url: str | None = None
    website: str | None = None
    website_resolution: WebsiteResolution | None = None
    country_code: str | None = None
    summary: str
    close_match: CloseMatch
    qualification: QualificationDecision
    commercial: CommercialBundle
    evidence: list[EvidenceItem] = Field(default_factory=list)
    research_trace: list[ResearchTraceEntry] = Field(default_factory=list)
    field_evidence: list[AssembledFieldEvidence] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    evidence_quality: SourceQuality = SourceQuality.UNKNOWN


class ShortlistRecord(StrictModel):
    shortlist_id: str = Field(default_factory=lambda: f"short_{uuid4().hex[:12]}")
    run_id: str
    options: list[ShortlistOption] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ExplorationMemoryState(StrictModel):
    scope: Literal["global"] = "global"
    query_history: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    blocked_official_domains: list[str] = Field(default_factory=list)
    searched_company_names: list[str] = Field(default_factory=list)
    company_observations: list[CompanyObservation] = Field(default_factory=list)
    registered_lead_names: list[str] = Field(default_factory=list)
    consecutive_hard_miss_runs: int = Field(default=0, ge=0)


class SearchBudget(StrictModel):
    source_attempt_budget: int = Field(default=6, ge=1)
    enrich_attempt_budget: int = Field(default=1, ge=0)
    search_call_budget: int = Field(default=10, ge=1)
    source_attempts_used: int = Field(default=0, ge=0)
    enrich_attempts_used: int = Field(default=0, ge=0)
    search_calls_used: int = Field(default=0, ge=0)

    def can_source(self) -> bool:
        return self.source_attempts_used < self.source_attempt_budget

    def can_enrich(self) -> bool:
        return self.enrich_attempts_used < self.enrich_attempt_budget

    def can_search(self) -> bool:
        return self.search_calls_used < self.search_call_budget


class RunIteration(StrictModel):
    index: int = Field(ge=1)
    planner_action: PlannerAction
    planner_reason: str | None = None
    planner_relaxation_stage: int | None = Field(default=None, ge=0, le=2)
    query: str | None = None
    dossier: AssembledLeadDossier | None = None
    qualification: QualificationDecision | None = None
    research_trace: list[ResearchTraceEntry] = Field(default_factory=list)
    documents_considered: int = Field(default=0, ge=0)
    documents_selected: int = Field(default=0, ge=0)
    focus_company_resolution: CompanyFocusResolution | None = None
    source_trace: SourceStageTrace | None = None
    anchored_source_trace: SourceStageTrace | None = None
    enrich_trace: SourceStageTrace | None = None
    assembler_trace: dict[str, Any] = Field(default_factory=dict)
    qualification_trace: QualificationTrace | None = None
    continue_trace: ContinueTrace | None = None
    company_observation_written: bool = False


class RunStageEvent(StrictModel):
    timestamp: datetime = Field(default_factory=utc_now)
    stage: StageName
    message: str
    run_status: RunStatus
    source_attempts_used: int = Field(default=0, ge=0)
    source_attempt_budget: int = Field(default=0, ge=0)
    enrich_attempts_used: int = Field(default=0, ge=0)
    enrich_attempt_budget: int = Field(default=0, ge=0)
    search_calls_used: int = Field(default=0, ge=0)
    search_call_budget: int = Field(default=0, ge=0)


class SearchRunSnapshot(StrictModel):
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    request: NormalizedLeadSearchRequest
    status: RunStatus = RunStatus.RUNNING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    current_stage: StageName | None = None
    progress_message: str | None = None
    last_heartbeat_at: datetime | None = None
    accepted_leads: list[AcceptedLeadRecord] = Field(default_factory=list)
    shortlist_id: str | None = None
    shortlist_options: list[ShortlistOption] = Field(default_factory=list)
    iterations: list[RunIteration] = Field(default_factory=list)
    stage_events: list[RunStageEvent] = Field(default_factory=list)
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
    current_stage: StageName | None = None
    progress_message: str | None = None
    last_heartbeat_at: datetime | None = None
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
