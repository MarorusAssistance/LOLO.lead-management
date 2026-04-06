from __future__ import annotations

from enum import Enum


class SearchAction(str, Enum):
    LEAD_SEARCH_START = "lead_search.start"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    NO_RESULT = "no_result"
    FAILED = "failed"


class SourcingStatus(str, Enum):
    FOUND = "FOUND"
    NO_CANDIDATE = "NO_CANDIDATE"
    ERROR = "ERROR"


class QualificationOutcome(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REJECT_CLOSE_MATCH = "REJECT_CLOSE_MATCH"
    ENRICH = "ENRICH"


class MatchType(str, Enum):
    EXACT = "exact"
    CLOSE = "close_match"


class PlannerAction(str, Enum):
    SOURCE = "SOURCE"
    ENRICH = "ENRICH"
    FINISH_ACCEPTED = "FINISH_ACCEPTED"
    FINISH_SHORTLIST = "FINISH_SHORTLIST"
    FINISH_NO_RESULT = "FINISH_NO_RESULT"


class StageName(str, Enum):
    NORMALIZE = "normalize"
    LOAD_STATE = "load_state"
    PLAN = "plan"
    SOURCE = "source"
    ASSEMBLE = "assemble"
    QUALIFY = "qualify"
    ENRICH = "enrich"
    REQUALIFY = "requalify"
    DRAFT = "draft"
    CRM_WRITE = "crm_write"
    CONTINUE_OR_FINISH = "continue_or_finish"


class FieldEvidenceStatus(str, Enum):
    SATISFIED = "satisfied"
    WEAKLY_SUPPORTED = "weakly_supported"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


class SourceQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"
