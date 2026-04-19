from __future__ import annotations

from typing import Iterable

from lolo_lead_management.domain.models import EvidenceDocument, NormalizedLeadSearchRequest, ResearchTraceEntry
from lolo_lead_management.ports.reranker import FieldTarget


_FIELD_SEGMENT_PREFERENCES: dict[str, tuple[str, ...]] = {
    "person_name": ("governance", "legal", "contact"),
    "role_title": ("governance", "legal", "contact"),
    "employee_estimate": ("employees", "identity", "legal"),
    "website": ("website", "contact", "identity"),
    "fit_signals": ("fit", "identity", "website"),
    "company_name": ("identity", "legal"),
    "country": ("identity", "legal", "contact"),
    "multi": ("identity", "website", "employees", "governance", "fit", "legal"),
}


def build_rerank_query_text(
    *,
    request: NormalizedLeadSearchRequest,
    focus_company: str | None,
    field_name: FieldTarget,
    query_hint: str | None = None,
) -> str:
    size_bits: list[str] = []
    if request.constraints.min_company_size is not None:
        size_bits.append(f"min {request.constraints.min_company_size}")
    if request.constraints.max_company_size is not None:
        size_bits.append(f"max {request.constraints.max_company_size}")
    size_text = ", ".join(size_bits) if size_bits else "unspecified"
    buyer_targets = ", ".join(request.buyer_targets[:4]) or "unspecified"
    themes = ", ".join(request.search_themes[:4]) or "unspecified"
    focus_text = focus_company or "unknown company"
    objective = _field_objective(field_name)
    hint_text = f" Query hint: {query_hint}." if query_hint else ""
    return (
        f"Focus company: {focus_text}. "
        f"Urgent field: {field_name}. "
        f"Objective: {objective}. "
        f"Buyer targets: {buyer_targets}. "
        f"Requested size band: {size_text}. "
        f"Search themes: {themes}."
        f"{hint_text}"
    )


def build_rerank_candidate_text(
    document: EvidenceDocument,
    *,
    field_name: FieldTarget,
    research_trace: ResearchTraceEntry | None = None,
) -> str:
    parts = [
        f"url: {document.url}",
        f"title: {document.title or ''}",
        f"snippet: {document.snippet or ''}",
        f"source_tier: {document.source_tier}",
        f"company_controlled: {'yes' if document.is_company_controlled_source else 'no'}",
    ]
    if document.company_anchor:
        parts.append(f"company_anchor: {document.company_anchor}")
    if document.selected_for_field:
        parts.append(f"selected_for_field: {document.selected_for_field}")
    if document.why_selected:
        parts.append(f"why_selected: {document.why_selected}")
    if research_trace is not None:
        if research_trace.research_phase:
            parts.append(f"research_phase: {research_trace.research_phase}")
        if research_trace.source_role:
            parts.append(f"source_role: {research_trace.source_role}")
        if research_trace.expected_field:
            parts.append(f"expected_field: {research_trace.expected_field}")
    segments = _relevant_segment_texts(document, field_name=field_name)
    if segments:
        parts.append("segments:")
        parts.extend(segments)
    elif document.raw_content:
        parts.append(f"content: {document.raw_content[:900]}")
    return "\n".join(parts)


def _relevant_segment_texts(document: EvidenceDocument, *, field_name: FieldTarget) -> list[str]:
    preferred = _FIELD_SEGMENT_PREFERENCES.get(field_name, _FIELD_SEGMENT_PREFERENCES["multi"])
    selected: list[str] = []
    if not document.logical_segments:
        return selected
    for segment_type in preferred:
        for segment in document.logical_segments:
            if segment.segment_type != segment_type or segment.noise:
                continue
            heading = " > ".join(segment.heading_path)
            prefix = f"- {segment.segment_type}"
            if heading:
                prefix = f"{prefix} [{heading}]"
            selected.append(f"{prefix}: {segment.text[:500]}")
            if len(selected) >= 3:
                return selected
    return selected


def summarize_field_targets(field_names: Iterable[str]) -> str:
    targets = [field_name for field_name in field_names if field_name]
    return ", ".join(targets) if targets else "multi"


def _field_objective(field_name: FieldTarget) -> str:
    if field_name == "person_name":
        return "Prefer documents that explicitly name a real executive, founder, administrator or decision-maker tied to the focus company."
    if field_name == "role_title":
        return "Prefer documents that explicitly ground the leadership or governance role of a named person tied to the focus company."
    if field_name == "employee_estimate":
        return "Prefer documents that best resolve company-specific employee size or workforce range for the focus company."
    if field_name == "website":
        return "Prefer documents that best resolve the official company website or company-controlled domain."
    if field_name == "fit_signals":
        return "Prefer documents that best support AI, automation, software, GenAI or technical fit signals for the focus company."
    if field_name == "country":
        return "Prefer documents that best ground the operating country of the focus company."
    if field_name == "company_name":
        return "Prefer documents that best isolate the exact company identity of the focus company."
    return "Prefer documents that are most useful to resolve the remaining missing lead fields for the focus company."
