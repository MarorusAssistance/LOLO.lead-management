from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from urllib.parse import urlparse

from lolo_lead_management.domain.enums import FieldEvidenceStatus, SourceQuality, SourcingStatus, StageName
from lolo_lead_management.domain.models import (
    AssembledFieldEvidence,
    AssembledLeadDossier,
    AssemblyFieldAssertion,
    AssemblyResolution,
    ChunkContactAssertion,
    ChunkExtractionResolution,
    ChunkFieldAssertion,
    CompanyCandidate,
    CompanyFocusResolution,
    DiscoveryCandidateExtractionResolution,
    DiscoveryCompanyCandidate,
    EvidenceDocument,
    PersonCandidate,
    RejectedCompanyCandidate,
    SourcePassResult,
    WebsiteResolution,
)
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    build_fallback_assembled_dossier,
    canonicalize_country_code,
    canonicalize_search_themes,
    canonicalize_website,
    clean_company_name,
    clean_person_name,
    clean_role_title,
    company_name_matches_anchor,
    company_name_matches_anchor_strict,
    dedupe_preserve_order,
    derive_anchor_legal_name,
    derive_anchor_query_name,
    derive_brand_aliases,
    document_can_seed_website_candidate,
    document_matches_anchor_strong,
    domain_from_url,
    domain_is_directory,
    domain_is_publisher_like,
    extracted_official_website_from_document,
    extract_employee_size_hint,
    is_plausible_company_name,
    merge_documents,
    normalize_text,
    parse_candidate_from_text,
    request_scoped_company_exclusions,
    resolve_person_signal,
    resolve_website_resolution,
    text_has_spanish_non_operational_signal,
)
from lolo_lead_management.engine.state import EngineRuntimeState

SPANISH_CITY_HINTS = {
    "madrid",
    "barcelona",
    "valencia",
    "sevilla",
    "malaga",
    "bilbao",
    "granada",
    "zaragoza",
    "alicante",
    "murcia",
}

WHOLE_DOCUMENT_TOKEN_THRESHOLD = 10_000


class AssembleStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor
        self.last_company_selection_trace: dict | None = None

    def execute(self, state: EngineRuntimeState) -> AssembledLeadDossier:
        source_result = state.current_source_result
        if source_result is None:
            dossier = AssembledLeadDossier(sourcing_status=SourcingStatus.NO_CANDIDATE, notes=["no_source_result"])
            state.current_assembler_trace = {
                "status": "no_source_result",
                "used_fallback": True,
                "input_documents": [],
                "document_steps": [],
                "final_dossier_after_overlay": dossier.model_dump(mode="json"),
            }
            return dossier

        focus_company = None
        if state.current_focus_company_resolution and state.current_focus_company_resolution.selected_company:
            focus_company = state.current_focus_company_resolution.selected_company
        focused_source = self._focus_source_result(source_result, focus_company)
        documents = self._prioritize_documents(focused_source.documents, focus_company)[:8]
        compact_source = focused_source.model_copy(update={"documents": documents})
        extraction_inputs: list[dict] = []
        extraction_raw_outputs: list[dict] = []
        extraction_sanitized_outputs: list[dict] = []
        segment_field_resolutions: list[dict] = []
        document_steps: list[dict] = []
        merged_segments: list[ChunkExtractionResolution] = []
        for document in documents:
            step = self._extract_document_assertions(
                state,
                document,
                focus_company=focus_company,
                extraction_inputs=extraction_inputs,
                extraction_raw_outputs=extraction_raw_outputs,
                extraction_sanitized_outputs=extraction_sanitized_outputs,
                segment_field_resolutions=segment_field_resolutions,
            )
            document_steps.append(step)
            merged_segments.extend(step["segment_resolutions"])

        resolution = self._build_grounded_resolution(
            state,
            source_result=compact_source,
            prior_dossier=state.current_dossier,
            focus_company=focus_company,
            segment_resolutions=merged_segments,
        )
        if resolution is not None:
            assembled = self._resolution_to_dossier(
                resolution,
                source_result=compact_source,
                prior_dossier=state.current_dossier,
            )
            used_fallback = False
            status = "ok"
            llm_error = None
        else:
            assembled = build_fallback_assembled_dossier(
                request=state.run.request,
                source_result=compact_source,
                prior_dossier=state.current_dossier,
            )
            used_fallback = True
            llm_error = next((item.get("llm_error") for item in document_steps if item.get("llm_error")), None)
            status = llm_error or "fallback"

        state.current_assembler_trace = {
            "status": status,
            "used_fallback": used_fallback,
            "focus_company": focus_company,
            "input_documents": [self._document_snapshot(item) for item in documents],
            "llm_input_payload": None,
            "llm_error": llm_error,
            "llm_raw_output": None,
            "evidence_classification": self._evidence_classification(resolution, documents),
            "field_confidence": self._field_confidence(resolution),
            "cross_company_reasons": resolution.contradictions if resolution is not None else [],
            "document_steps": [self._serialize_document_step(item) for item in document_steps],
            "chunk_inputs": [item for item in extraction_inputs if item.get("mode") == "focus_locked_chunk_mode"],
            "chunk_raw_outputs": [item for item in extraction_raw_outputs if item.get("mode") == "focus_locked_chunk_mode"],
            "chunk_sanitized_outputs": [item for item in extraction_sanitized_outputs if item.get("mode") == "focus_locked_chunk_mode"],
            "extraction_inputs": extraction_inputs,
            "extraction_raw_outputs": extraction_raw_outputs,
            "extraction_sanitized_outputs": extraction_sanitized_outputs,
            "chunk_merge_summary": segment_field_resolutions,
            "segment_field_resolutions": segment_field_resolutions,
            "merged_field_resolution": resolution.model_dump(mode="json") if resolution is not None else None,
            "selected_subject_company": resolution.subject_company_name if resolution is not None else None,
            "selected_contact_pair": (
                {
                    "person_name": resolution.person_name,
                    "role_title": resolution.role_title,
                }
                if resolution is not None and (resolution.person_name or resolution.role_title)
                else None
            ),
            "final_dossier_after_overlay": assembled.model_dump(mode="json"),
        }
        return assembled

    def select_focus_company(self, state: EngineRuntimeState) -> CompanyFocusResolution:
        source_result = state.current_source_result
        attempts = max(1, state.discovery_attempts_for_current_pass)
        if source_result is None or not source_result.documents:
            resolution = CompanyFocusResolution(notes=["no_discovery_documents"])
            self.last_company_selection_trace = {
                "mode": "company_selection_mode",
                "status": "no_documents",
                "input_documents": [],
                "excluded_companies": [],
                "llm_input_payload": None,
                "llm_raw_output": None,
                "sanitized_discovery_candidates": [],
                "focus_selection_mode": "none",
                "discovery_batches_considered": attempts,
                "focus_selection_basis": [],
                "resolved_focus_company": None,
                "focus_resolution": resolution.model_dump(mode="json"),
            }
            return resolution

        excluded_companies = self._excluded_company_names(state)
        documents = self._prioritize_documents(source_result.documents, None)[:8]
        candidate_extraction_inputs: list[dict] = []
        candidate_extraction_raw_outputs: list[dict] = []
        candidate_extraction_sanitized_outputs: list[dict] = []
        candidate_document_steps: list[dict] = []
        aggregated_candidate_ledger: list[DiscoveryCompanyCandidate] = []
        for document in documents:
            step = self._extract_discovery_candidates_from_document(
                state,
                document,
                candidate_extraction_inputs=candidate_extraction_inputs,
                candidate_extraction_raw_outputs=candidate_extraction_raw_outputs,
                candidate_extraction_sanitized_outputs=candidate_extraction_sanitized_outputs,
            )
            candidate_document_steps.append(step)
            aggregated_candidate_ledger.extend(step["candidates"])

        scored_candidates = self._score_discovery_candidates(
            aggregated_candidate_ledger,
            preferred_country=state.run.request.constraints.preferred_country,
            min_size=state.run.request.constraints.min_company_size,
            max_size=state.run.request.constraints.max_company_size,
            request_text=state.run.request.user_text,
        )
        resolution = self._sanitize_focus_resolution(
            documents=documents,
            attempts=attempts,
            excluded_companies=excluded_companies,
            ledger_candidates=scored_candidates,
        )
        self.last_company_selection_trace = {
            "mode": "company_selection_mode",
            "status": "ok" if aggregated_candidate_ledger else "no_candidates",
            "error": None,
            "input_documents": [self._document_snapshot(item) for item in documents],
            "excluded_companies": excluded_companies,
            "llm_input_payload": None,
            "llm_raw_output": None,
            "sanitized_discovery_candidates": [item.model_dump(mode="json") for item in aggregated_candidate_ledger],
            "candidate_extraction_inputs": candidate_extraction_inputs,
            "candidate_extraction_raw_outputs": candidate_extraction_raw_outputs,
            "candidate_extraction_sanitized_outputs": candidate_extraction_sanitized_outputs,
            "candidate_document_steps": [self._serialize_candidate_document_step(item) for item in candidate_document_steps],
            "aggregated_candidate_ledger": [item.model_dump(mode="json") for item in aggregated_candidate_ledger],
            "candidate_scores": [item.model_dump(mode="json") for item in scored_candidates],
            "discovery_batches_considered": attempts,
            "focus_selection_basis": [item.model_dump(mode="json") for item in resolution.discovery_candidates],
            "focus_selection_mode": resolution.selection_mode,
            "selection_reasons": resolution.selection_reasons,
            "hard_rejections": resolution.hard_rejections,
            "resolved_focus_company": resolution.selected_company,
            "focus_resolution": resolution.model_dump(mode="json"),
        }
        return resolution

    def _assembly_payload(
        self,
        state: EngineRuntimeState,
        source_result: SourcePassResult,
        prior_dossier: AssembledLeadDossier | None,
        focus_company: str | None,
        *,
        chunk_summary: dict | None = None,
    ) -> dict:
        return {
            "mode": "focus_locked_mode",
            "request_summary": self._request_summary(state),
            "focus_company": focus_company,
            "prior_dossier": self._prior_dossier_summary(prior_dossier),
            "documents": [self._compact_document_payload(item, raw_limit=120) for item in source_result.documents[:3]],
            "website_candidates": [item.model_dump(mode="json") for item in source_result.website_candidates[:2]],
            "chunk_merge_summary": chunk_summary,
            "excluded_companies": [],
        }

    def _company_selection_payload(
        self,
        state: EngineRuntimeState,
        source_result: SourcePassResult,
        fallback_candidates: list[DiscoveryCompanyCandidate],
        excluded_companies: list[str],
    ) -> dict:
        return {
            "mode": "company_selection_mode",
            "request_summary": self._request_summary(state),
            "discovery_attempts_for_current_pass": state.discovery_attempts_for_current_pass,
            "excluded_companies": [],
            "documents": [self._compact_document_payload(item, raw_limit=1200) for item in source_result.documents[:3]],
            "fallback_candidates": [self._compact_fallback_candidate_payload(item) for item in fallback_candidates[:2]],
        }

    def _request_summary(self, state: EngineRuntimeState) -> dict:
        request = state.run.request
        return {
            "user_text": request.user_text[:220],
            "preferred_country": request.constraints.preferred_country,
            "preferred_regions": request.constraints.preferred_regions[:3],
            "min_company_size": request.constraints.min_company_size,
            "max_company_size": request.constraints.max_company_size,
            "buyer_targets": request.buyer_targets[:4],
            "search_themes": request.search_themes[:4],
        }

    def _prior_dossier_summary(self, dossier: AssembledLeadDossier | None) -> dict | None:
        if dossier is None:
            return None
        return {
            "company_name": dossier.company.name if dossier.company else None,
            "website": dossier.company.website if dossier.company else None,
            "country_code": dossier.company.country_code if dossier.company else None,
            "employee_estimate": dossier.company.employee_estimate if dossier.company else None,
            "person_name": dossier.person.full_name if dossier.person else None,
            "role_title": dossier.person.role_title if dossier.person else None,
            "fit_signals": dossier.fit_signals,
            "notes": dossier.notes[-3:],
        }

    def _compact_document_payload(self, document: EvidenceDocument, *, raw_limit: int = 180) -> dict:
        return {
            "url": document.url,
            "title": (document.title or "")[:110],
            "snippet": (document.snippet or "")[:140],
            "raw_content_preview": (document.raw_content or "")[:raw_limit],
            "chunker_adapter": document.chunker_adapter,
            "logical_segment_count": len(document.logical_segments),
            "debug_markdown_artifact_path": document.debug_markdown_artifact_path,
            "source_tier": document.source_tier,
            "source_quality": document.source_quality.value if hasattr(document.source_quality, "value") else str(document.source_quality),
            "company_anchor": document.company_anchor,
            "is_company_controlled_source": document.is_company_controlled_source,
            "source_type": document.source_type,
        }

    def _compact_fallback_candidate_payload(self, candidate: DiscoveryCompanyCandidate) -> dict:
        return {
            "company_name": candidate.company_name,
            "legal_name": candidate.legal_name,
            "query_name": candidate.query_name,
            "country_code": candidate.country_code,
            "location_hint": candidate.location_hint,
            "theme_tags": candidate.theme_tags[:3],
            "candidate_website": candidate.candidate_website,
            "employee_count_hint_value": candidate.employee_count_hint_value,
            "employee_count_hint_type": candidate.employee_count_hint_type,
            "evidence_urls": candidate.evidence_urls[:2],
            "selection_score": candidate.selection_score,
            "selection_reasons": candidate.selection_reasons[:2],
            "hard_rejections": candidate.hard_rejections[:2],
        }

    def _llm_first_focus_resolution(
        self,
        generated: CompanyFocusResolution | None,
        *,
        documents: list[EvidenceDocument],
        fallback_candidates: list[DiscoveryCompanyCandidate],
        allowed_urls: set[str],
    ) -> CompanyFocusResolution:
        if generated is not None and generated.selected_company:
            selected_company = clean_company_name(generated.selected_company) or generated.selected_company
            legal_name = clean_company_name(generated.legal_name) or selected_company
            query_name = clean_company_name(generated.query_name) or selected_company
            evidence_urls = [url for url in generated.evidence_urls if url in allowed_urls]
            if not evidence_urls and documents:
                evidence_urls = [documents[0].url]
            return generated.model_copy(
                update={
                    "selected_company": selected_company,
                    "legal_name": legal_name,
                    "query_name": query_name,
                    "brand_aliases": dedupe_preserve_order(
                        [clean_company_name(alias) for alias in generated.brand_aliases if clean_company_name(alias)]
                    ),
                    "evidence_urls": evidence_urls,
                    "selection_mode": generated.selection_mode if generated.selection_mode != "none" else "plausible",
                    "notes": dedupe_preserve_order([*generated.notes, "focus_selected_by_llm_first"]),
                }
            )
        if fallback_candidates:
            top = fallback_candidates[0]
            return CompanyFocusResolution(
                selected_company=top.legal_name or top.company_name,
                legal_name=top.legal_name or top.company_name,
                query_name=top.query_name or top.company_name,
                brand_aliases=top.brand_aliases,
                selection_mode="fallback",
                confidence=max(0.2, min(top.selection_score, 0.8)),
                evidence_urls=top.evidence_urls[:4],
                selection_reasons=top.selection_reasons,
                hard_rejections=top.hard_rejections,
                discovery_candidates=fallback_candidates,
                notes=["focus_selected_by_minimal_fallback"],
            )
        return CompanyFocusResolution(selection_mode="none", discovery_candidates=[], notes=["no_company_selected"])

    def _build_grounded_resolution(
        self,
        state: EngineRuntimeState,
        *,
        source_result: SourcePassResult,
        prior_dossier: AssembledLeadDossier | None,
        focus_company: str | None,
        segment_resolutions: list[ChunkExtractionResolution],
    ) -> AssemblyResolution | None:
        if not segment_resolutions:
            return None
        if not any(item.field_assertions or item.contact_assertions for item in segment_resolutions):
            return None

        allowed_docs = {
            item.url: item
            for item in merge_documents([*(prior_dossier.evidence if prior_dossier else []), *source_result.documents])
        }
        subject_company = self._select_subject_company(
            focus_company=focus_company,
            anchored_company=source_result.anchored_company_name,
            segment_resolutions=segment_resolutions,
            allowed_docs=allowed_docs,
        )
        if not subject_company:
            return None

        subject_docs = [
            item for item in allowed_docs.values() if document_matches_anchor_strong(item, subject_company)
        ] or list(allowed_docs.values())
        company_assertions = self._subject_field_assertions(segment_resolutions, subject_company, "company_name")
        country_assertions = self._subject_field_assertions(segment_resolutions, subject_company, "country")
        employee_assertions = self._subject_field_assertions(segment_resolutions, subject_company, "employee_estimate")
        website_assertions = self._subject_field_assertions(segment_resolutions, subject_company, "website")
        contact_assertions = self._subject_contact_assertions(segment_resolutions, subject_company)

        company_urls = dedupe_preserve_order(
            [item.source_url for item in company_assertions if item.source_url in allowed_docs]
            or [item.url for item in subject_docs[:3]]
        )
        country_value, country_status, country_support, country_contradict = self._resolve_country(
            country_assertions,
            allowed_docs,
        )
        employee_value, employee_status, employee_support, employee_contradict, employee_note = self._resolve_employee(
            employee_assertions,
            allowed_docs,
        )
        website_resolution, website_support, website_contradict = self._resolve_subject_website(
            subject_company=subject_company,
            website_assertions=website_assertions,
            subject_docs=subject_docs,
            source_result=source_result,
            allowed_docs=allowed_docs,
        )
        contact = self._resolve_contact_pair(
            state,
            subject_company=subject_company,
            contact_assertions=contact_assertions,
            allowed_docs=allowed_docs,
        )
        fit_signals = self._merge_fit_signals(
            state,
            subject_company=subject_company,
            segment_resolutions=segment_resolutions,
            allowed_docs=allowed_docs,
        )

        selected_urls = dedupe_preserve_order(
            [
                *company_urls,
                *website_support,
                *country_support,
                *employee_support,
                *contact["support_urls"],
            ]
        )
        cross_company_notes = self._cross_company_notes(segment_resolutions, subject_company)
        field_assertions = [
            self._final_assertion(
                field_name="company_name",
                value=subject_company,
                supporting_urls=company_urls,
                contradicting_urls=[],
                status=FieldEvidenceStatus.SATISFIED,
                support_type="corroborated" if len(company_urls) >= 2 else "explicit",
                allowed_docs=allowed_docs,
                reasoning_note="Subject company selected from grounded segment assertions and supporting documents.",
            ),
            self._build_website_field_assertion(website_resolution, website_support, website_contradict, allowed_docs),
            self._final_assertion(
                field_name="country",
                value=country_value,
                supporting_urls=country_support,
                contradicting_urls=country_contradict,
                status=country_status,
                support_type="corroborated" if len(country_support) >= 2 else "explicit",
                allowed_docs=allowed_docs,
                reasoning_note="Country kept only from explicit grounded segment assertions.",
            ),
            self._final_assertion(
                field_name="employee_estimate",
                value=employee_value,
                supporting_urls=employee_support,
                contradicting_urls=employee_contradict,
                status=employee_status,
                support_type="corroborated" if len(employee_support) >= 2 else "explicit",
                allowed_docs=allowed_docs,
                reasoning_note=employee_note,
            ),
            self._final_assertion(
                field_name="person_name",
                value=contact["person_name"],
                supporting_urls=contact["support_urls"],
                contradicting_urls=[],
                status=contact["status"],
                support_type=contact["support_type"],
                allowed_docs=allowed_docs,
                reasoning_note=contact["reasoning_note"],
            ),
            self._final_assertion(
                field_name="role_title",
                value=contact["role_title"],
                supporting_urls=contact["support_urls"],
                contradicting_urls=[],
                status=contact["status"],
                support_type=contact["support_type"],
                allowed_docs=allowed_docs,
                reasoning_note=contact["reasoning_note"],
            ),
        ]

        return AssemblyResolution(
            subject_company_name=subject_company,
            website=website_resolution.candidate_website if website_resolution.officiality in {"confirmed", "probable"} else None,
            candidate_website=website_resolution.candidate_website,
            website_officiality=website_resolution.officiality,
            website_confidence=website_resolution.confidence,
            website_evidence_urls=website_resolution.evidence_urls,
            website_signals=website_resolution.signals,
            website_risks=website_resolution.risks,
            country_code=country_value,
            employee_estimate=employee_value,
            person_name=contact["person_name"],
            role_title=contact["role_title"],
            fit_signals=fit_signals,
            selected_evidence_urls=selected_urls,
            field_assertions=field_assertions,
            contradictions=dedupe_preserve_order([*cross_company_notes, *([employee_note] if employee_status == FieldEvidenceStatus.CONTRADICTED else [])]),
            unresolved_fields=[item.field_name for item in field_assertions if item.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}],
            confidence_notes=dedupe_preserve_order(
                [
                    f"grounded_segments={len(segment_resolutions)}",
                    f"subject_company={subject_company}",
                    *([f"website_officiality={website_resolution.officiality}"] if website_resolution.candidate_website else []),
                ]
            ),
            notes=dedupe_preserve_order([*[item for resolution in segment_resolutions for item in resolution.notes], "assembled_by_grounded_segment_merge"]),
        )

    def _resolution_to_dossier(
        self,
        generated: AssemblyResolution,
        *,
        source_result: SourcePassResult,
        prior_dossier: AssembledLeadDossier | None,
    ) -> AssembledLeadDossier:
        allowed_docs = {
            item.url: item
            for item in merge_documents([*(prior_dossier.evidence if prior_dossier else []), *source_result.documents])
        }
        assertion_map = {item.field_name: item for item in generated.field_assertions}
        selected_urls = dedupe_preserve_order(
            [
                *generated.selected_evidence_urls,
                *[url for item in generated.field_assertions for url in item.evidence_urls],
                *[url for item in generated.field_assertions for url in item.contradicting_urls],
            ]
        )
        evidence = [allowed_docs[url] for url in selected_urls if url in allowed_docs] or source_result.documents[:6]
        company_name = clean_company_name(generated.subject_company_name) or source_result.anchored_company_name
        if not company_name:
            return AssembledLeadDossier(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_used=source_result.executed_queries[0].query if source_result.executed_queries else None,
                notes=dedupe_preserve_order([*source_result.notes, "segment_subject_company_missing"]),
                evidence=source_result.documents[:6],
                research_trace=source_result.research_trace,
                documents_considered=sum(item.documents_considered for item in source_result.research_trace),
                documents_selected=len(source_result.documents[:6]),
            )
        website = canonicalize_website(generated.website)
        candidate_website = canonicalize_website(generated.candidate_website or generated.website)
        country_code = generated.country_code
        employee_estimate = generated.employee_estimate
        person_name = generated.person_name
        role_title = generated.role_title
        fit_signals = dedupe_preserve_order(generated.fit_signals)

        company = CompanyCandidate(name=company_name, website=website, country_code=country_code, employee_estimate=employee_estimate)
        person = PersonCandidate(full_name=person_name, role_title=role_title) if person_name or role_title else None
        website_resolution = WebsiteResolution(
            candidate_website=candidate_website,
            officiality=generated.website_officiality or "unknown",
            confidence=generated.website_confidence or 0,
            evidence_urls=[url for url in generated.website_evidence_urls if url in allowed_docs],
            signals=dedupe_preserve_order(generated.website_signals),
            risks=dedupe_preserve_order(generated.website_risks),
        )
        field_evidence = [
            self._field_evidence_from_final_assertion("company_name", company.name, assertion_map.get("company_name"), allowed_docs),
            self._field_evidence_from_final_assertion("website", company.website, assertion_map.get("website"), allowed_docs),
            self._field_evidence_from_final_assertion("country", company.country_code, assertion_map.get("country"), allowed_docs),
            self._field_evidence_from_final_assertion("employee_estimate", company.employee_estimate, assertion_map.get("employee_estimate"), allowed_docs),
            self._field_evidence_from_final_assertion("person_name", person.full_name if person else None, assertion_map.get("person_name"), allowed_docs),
            self._field_evidence_from_final_assertion("role_title", person.role_title if person else None, assertion_map.get("role_title"), allowed_docs),
        ]
        if fit_signals:
            field_evidence.append(
                AssembledFieldEvidence(
                    field_name="fit_signals",
                    value=", ".join(fit_signals),
                    status=FieldEvidenceStatus.SATISFIED,
                    supporting_evidence=evidence[:3],
                    contradicting_evidence=[],
                    source_quality=self._source_quality_from_docs(evidence[:3]),
                    source_tier=self._source_tier_from_docs(evidence[:3]),
                    support_type="corroborated" if len(evidence[:3]) >= 2 else "explicit",
                    reasoning_note="Fit signals preserved directly from grounded segment evidence.",
                )
            )
        return AssembledLeadDossier(
            sourcing_status=SourcingStatus.FOUND,
            query_used=source_result.executed_queries[0].query if source_result.executed_queries else None,
            person=person,
            company=company,
            lead_source_type=prior_dossier.lead_source_type if prior_dossier else None,
            person_confidence=prior_dossier.person_confidence if prior_dossier else None,
            primary_person_source_url=prior_dossier.primary_person_source_url if prior_dossier else None,
            fit_signals=fit_signals,
            evidence=evidence,
            notes=dedupe_preserve_order([*source_result.notes, *generated.confidence_notes, *generated.notes]),
            anchored_company_name=company_name,
            website_resolution=website_resolution,
            research_trace=source_result.research_trace,
            field_evidence=field_evidence,
            contradictions=dedupe_preserve_order(generated.contradictions),
            evidence_quality=self._source_quality_from_docs(evidence),
            documents_considered=sum(item.documents_considered for item in source_result.research_trace),
            documents_selected=len(evidence),
        )

    def _field_evidence_from_final_assertion(
        self,
        field_name: str,
        value,
        assertion,
        allowed_docs: dict[str, EvidenceDocument],
    ) -> AssembledFieldEvidence:
        supporting = [allowed_docs[url] for url in (assertion.evidence_urls if assertion is not None else []) if url in allowed_docs]
        contradicting = [allowed_docs[url] for url in (assertion.contradicting_urls if assertion is not None else []) if url in allowed_docs]
        if assertion is not None:
            return AssembledFieldEvidence(
                field_name=field_name,
                value=value,
                status=assertion.status,
                supporting_evidence=supporting,
                contradicting_evidence=contradicting,
                source_quality=self._source_quality_from_docs(supporting or contradicting),
                source_tier=assertion.source_tier,
                support_type=assertion.support_type,
                reasoning_note=assertion.reasoning_note or f"{field_name} preserved from grounded segment assertion.",
            )
        return AssembledFieldEvidence(
            field_name=field_name,
            value=value,
            status=FieldEvidenceStatus.UNKNOWN if value is None else FieldEvidenceStatus.WEAKLY_SUPPORTED,
            supporting_evidence=supporting[:2],
            contradicting_evidence=[],
            source_quality=self._source_quality_from_docs(supporting[:2]),
            source_tier=self._source_tier_from_docs(supporting[:2]),
            support_type="weak_inference" if value is not None else "explicit",
            reasoning_note=f"{field_name} has no explicit grounded segment assertion.",
        )

    def _source_quality_from_docs(self, documents: list[EvidenceDocument]) -> SourceQuality:
        priority = {
            SourceQuality.HIGH: 3,
            SourceQuality.MEDIUM: 2,
            SourceQuality.LOW: 1,
            SourceQuality.UNKNOWN: 0,
        }
        best = SourceQuality.UNKNOWN
        for item in documents:
            quality = item.source_quality if isinstance(item.source_quality, SourceQuality) else SourceQuality.UNKNOWN
            if priority[quality] > priority[best]:
                best = quality
        return best

    def _source_tier_from_docs(self, documents: list[EvidenceDocument]) -> str:
        tiers = {item.source_tier for item in documents if item.source_tier}
        if not tiers:
            return "unknown"
        if len(tiers) == 1:
            return next(iter(tiers))
        return "mixed"

    def _evidence_classification(self, generated: AssemblyResolution | None, documents: list[EvidenceDocument]) -> list[dict]:
        if generated is None:
            return []
        assertion_map = {item.field_name: item for item in generated.field_assertions}
        return [
            {
                "field_name": field_name,
                "status": assertion.status,
                "support_type": assertion.support_type,
                "evidence_urls": assertion.evidence_urls,
                "contradicting_urls": assertion.contradicting_urls,
            }
            for field_name, assertion in assertion_map.items()
        ]

    def _field_confidence(self, generated: AssemblyResolution | None) -> dict[str, str]:
        if generated is None:
            return {}
        return {
            item.field_name: (
                "strong"
                if item.status == FieldEvidenceStatus.SATISFIED and item.support_type == "corroborated"
                else "corroborated"
                if item.status == FieldEvidenceStatus.SATISFIED
                else "weak"
                if item.status == FieldEvidenceStatus.WEAKLY_SUPPORTED
                else "unknown"
            )
            for item in generated.field_assertions
        }

    def _final_assertion(
        self,
        *,
        field_name: str,
        value,
        supporting_urls: list[str],
        contradicting_urls: list[str],
        status: FieldEvidenceStatus,
        support_type: str,
        allowed_docs: dict[str, EvidenceDocument],
        reasoning_note: str,
    ) -> AssemblyFieldAssertion:
        supporting_docs = [allowed_docs[url] for url in supporting_urls if url in allowed_docs]
        contradicting_docs = [allowed_docs[url] for url in contradicting_urls if url in allowed_docs]
        return AssemblyFieldAssertion(
            field_name=field_name,
            value=value,
            status=status,
            evidence_urls=dedupe_preserve_order([item.url for item in supporting_docs]),
            contradicting_urls=dedupe_preserve_order([item.url for item in contradicting_docs]),
            source_tier=self._source_tier_from_docs(supporting_docs or contradicting_docs),
            support_type=support_type,
            reasoning_note=reasoning_note,
        )

    def _build_website_field_assertion(
        self,
        website_resolution: WebsiteResolution,
        support_urls: list[str],
        contradicting_urls: list[str],
        allowed_docs: dict[str, EvidenceDocument],
    ):
        if contradicting_urls and website_resolution.candidate_website is not None:
            status = FieldEvidenceStatus.CONTRADICTED
        elif website_resolution.officiality == "confirmed":
            status = FieldEvidenceStatus.SATISFIED
        elif website_resolution.officiality == "probable":
            status = FieldEvidenceStatus.WEAKLY_SUPPORTED
        else:
            status = FieldEvidenceStatus.UNKNOWN
        support_type = "corroborated" if len(support_urls) >= 2 else "explicit"
        return self._final_assertion(
            field_name="website",
            value=website_resolution.candidate_website if website_resolution.officiality in {"confirmed", "probable"} else None,
            supporting_urls=support_urls,
            contradicting_urls=contradicting_urls,
            status=status,
            support_type=support_type,
            allowed_docs=allowed_docs,
            reasoning_note=(
                f"Website resolved from grounded segment evidence with officiality={website_resolution.officiality} "
                f"and confidence={website_resolution.confidence:.2f}."
            ),
        )

    def _select_subject_company(
        self,
        *,
        focus_company: str | None,
        anchored_company: str | None,
        segment_resolutions: list[ChunkExtractionResolution],
        allowed_docs: dict[str, EvidenceDocument],
    ) -> str | None:
        candidates: dict[str, dict[str, object]] = {}
        for resolution in segment_resolutions:
            for assertion in resolution.field_assertions:
                company_name = self._company_name_for_assertion(assertion)
                if not company_name:
                    continue
                bucket = candidates.setdefault(company_name, {"score": 0, "urls": []})
                bucket["score"] = int(bucket["score"]) + self._assertion_weight(assertion, allowed_docs)
                bucket["urls"] = dedupe_preserve_order([*bucket["urls"], assertion.source_url])  # type: ignore[index]
            for assertion in resolution.contact_assertions:
                company_name = clean_company_name(assertion.company_name)
                if not company_name:
                    continue
                bucket = candidates.setdefault(company_name, {"score": 0, "urls": []})
                bucket["score"] = int(bucket["score"]) + 5 + self._support_weight(assertion.support_type)
                bucket["urls"] = dedupe_preserve_order([*bucket["urls"], assertion.source_url])  # type: ignore[index]

        preferred = clean_company_name(focus_company) or clean_company_name(anchored_company)
        if preferred:
            for company_name, bucket in candidates.items():
                if company_name_matches_anchor(company_name, preferred):
                    bucket["score"] = int(bucket["score"]) + 8
        if preferred and not candidates:
            return preferred if is_plausible_company_name(preferred) else None
        if not candidates:
            return None

        ordered = sorted(
            candidates.items(),
            key=lambda item: (int(item[1]["score"]), len(item[1]["urls"])),  # type: ignore[index]
            reverse=True,
        )
        chosen = ordered[0][0]
        return chosen if is_plausible_company_name(chosen) else preferred

    def _subject_field_assertions(
        self,
        segment_resolutions: list[ChunkExtractionResolution],
        subject_company: str,
        field_name: str,
    ) -> list[ChunkFieldAssertion]:
        return [
            item
            for resolution in segment_resolutions
            for item in resolution.field_assertions
            if item.field_name == field_name and self._assertion_matches_subject(item.company_name, subject_company)
        ]

    def _subject_contact_assertions(
        self,
        segment_resolutions: list[ChunkExtractionResolution],
        subject_company: str,
    ) -> list[ChunkContactAssertion]:
        return [
            item
            for resolution in segment_resolutions
            for item in resolution.contact_assertions
            if self._assertion_matches_subject(item.company_name, subject_company)
        ]

    def _non_subject_company_assertions(
        self,
        segment_resolutions: list[ChunkExtractionResolution],
        subject_company: str,
    ) -> list[ChunkFieldAssertion]:
        return [
            item
            for resolution in segment_resolutions
            for item in resolution.field_assertions
            if item.field_name == "company_name"
            and item.company_name
            and not self._assertion_matches_subject(item.company_name, subject_company)
        ]

    def _cross_company_notes(
        self,
        segment_resolutions: list[ChunkExtractionResolution],
        subject_company: str,
    ) -> list[str]:
        _ = subject_company
        return dedupe_preserve_order(
            [
                item
                for resolution in segment_resolutions
                for item in resolution.contradictions
            ]
        )

    def _resolve_country(
        self,
        assertions: list[ChunkFieldAssertion],
        allowed_docs: dict[str, EvidenceDocument],
    ) -> tuple[str | None, FieldEvidenceStatus, list[str], list[str]]:
        values: dict[str, list[str]] = defaultdict(list)
        for assertion in assertions:
            value = canonicalize_country_code(str(assertion.value)) if assertion.value is not None else None
            if value:
                values[value].append(assertion.source_url)
        if not values:
            return None, FieldEvidenceStatus.UNKNOWN, [], []
        if len(values) > 1:
            support = dedupe_preserve_order([url for urls in values.values() for url in urls if url in allowed_docs])
            return None, FieldEvidenceStatus.CONTRADICTED, [], support
        value, urls = next(iter(values.items()))
        support = dedupe_preserve_order([url for url in urls if url in allowed_docs])
        return value, FieldEvidenceStatus.SATISFIED if support else FieldEvidenceStatus.UNKNOWN, support, []

    def _resolve_employee(
        self,
        assertions: list[ChunkFieldAssertion],
        allowed_docs: dict[str, EvidenceDocument],
    ) -> tuple[int | None, FieldEvidenceStatus, list[str], list[str], str]:
        priority = {"exact": 3, "range": 2, "estimate": 1, "unknown": 0}
        candidates: dict[tuple[int, str], list[str]] = defaultdict(list)
        for assertion in assertions:
            if not isinstance(assertion.value, int):
                continue
            candidates[(assertion.value, assertion.employee_count_type)].append(assertion.source_url)
        if not candidates:
            return None, FieldEvidenceStatus.UNKNOWN, [], [], "Employee estimate is still missing from grounded segment assertions."

        exact_values = {value for (value, count_type) in candidates if count_type == "exact"}
        if len(exact_values) > 1:
            contradicting = dedupe_preserve_order(
                [
                    url
                    for (value, count_type), urls in candidates.items()
                    if count_type == "exact"
                    for url in urls
                    if url in allowed_docs
                ]
            )
            chosen_value = max(exact_values)
            return (
                chosen_value,
                FieldEvidenceStatus.CONTRADICTED,
                [],
                contradicting,
                "Employee estimate has incompatible exact values for the selected company.",
            )

        range_values = [value for (value, count_type) in candidates if count_type == "range"]
        if not exact_values and len(range_values) >= 2 and max(range_values) > max(1, min(range_values) * 2):
            contradicting = dedupe_preserve_order(
                [
                    url
                    for (value, count_type), urls in candidates.items()
                    if count_type == "range"
                    for url in urls
                    if url in allowed_docs
                ]
            )
            return (
                min(range_values),
                FieldEvidenceStatus.CONTRADICTED,
                [],
                contradicting,
                "Employee estimate ranges conflict across grounded assertions for the selected company.",
            )

        estimate_values = [value for (value, count_type) in candidates if count_type == "estimate"]
        if not exact_values and not range_values and len(estimate_values) >= 2:
            highest = max(estimate_values)
            lowest = min(estimate_values)
            if highest > max(lowest * 2, lowest + 25):
                contradicting = dedupe_preserve_order(
                    [
                        url
                        for (value, count_type), urls in candidates.items()
                        if count_type == "estimate"
                        for url in urls
                        if url in allowed_docs
                    ]
                )
                return (
                    lowest,
                    FieldEvidenceStatus.CONTRADICTED,
                    [],
                    contradicting,
                    "Employee estimate hints vary too much to reconcile for the selected company.",
                )
        ranked = sorted(
            candidates.items(),
            key=lambda item: (
                priority.get(item[0][1], 0),
                len(set(item[1])),
                max(
                    {"tier_a": 3, "tier_b": 2, "tier_c": 1, "unknown": 0}.get(allowed_docs[url].source_tier, 0)
                    for url in item[1]
                    if url in allowed_docs
                ),
            ),
            reverse=True,
        )
        (value, count_type), urls = ranked[0]
        support = dedupe_preserve_order([url for url in urls if url in allowed_docs])
        status = (
            FieldEvidenceStatus.SATISFIED
            if support and count_type in {"exact", "range"}
            else FieldEvidenceStatus.WEAKLY_SUPPORTED
            if support and count_type == "estimate"
            else FieldEvidenceStatus.UNKNOWN
        )
        note = f"Employee estimate selected from grounded segment assertions using {count_type} priority."
        return value, status, support, [], note

    def _resolve_subject_website(
        self,
        *,
        subject_company: str,
        website_assertions: list[ChunkFieldAssertion],
        subject_docs: list[EvidenceDocument],
        source_result: SourcePassResult,
        allowed_docs: dict[str, EvidenceDocument],
    ) -> tuple[WebsiteResolution, list[str], list[str]]:
        candidate_urls: dict[str, list[str]] = defaultdict(list)
        for assertion in website_assertions:
            website = canonicalize_website(str(assertion.value)) if assertion.value is not None else None
            if website:
                candidate_urls[website].append(assertion.source_url)
        for hint in source_result.website_candidates:
            website = canonicalize_website(hint.candidate_website)
            if website:
                candidate_urls[website].extend(url for url in hint.evidence_urls if url in allowed_docs)
        for document in subject_docs:
            extracted = extracted_official_website_from_document(document, subject_company)
            if extracted and canonicalize_website(extracted):
                website = canonicalize_website(extracted)
                candidate_urls[website].append(document.url)
            elif document.is_company_controlled_source:
                parsed = urlparse(document.url)
                if parsed.scheme and parsed.netloc:
                    inferred = canonicalize_website(f"{parsed.scheme}://{parsed.netloc}")
                    if (
                        inferred
                        and not domain_is_directory(inferred)
                        and not domain_is_publisher_like(inferred)
                    ):
                        candidate_urls[inferred].append(document.url)

        filtered_candidates: dict[str, list[str]] = {}
        for website, urls in candidate_urls.items():
            seed_urls = [
                url
                for url in dedupe_preserve_order(urls)
                if url in allowed_docs and document_can_seed_website_candidate(allowed_docs[url], website, anchor_company=subject_company)
            ]
            if seed_urls:
                filtered_candidates[website] = seed_urls
        if not filtered_candidates:
            resolution = resolve_website_resolution(company_name=subject_company, candidate_website=None, documents=subject_docs)
            return resolution, [], []

        chosen_website, support_urls = max(
            filtered_candidates.items(),
            key=lambda item: (
                len(set(item[1])),
                max(
                    {"tier_a": 3, "tier_b": 2, "tier_c": 1, "unknown": 0}.get(allowed_docs[url].source_tier, 0)
                    for url in item[1]
                    if url in allowed_docs
                ),
            ),
        )
        resolution = resolve_website_resolution(
            company_name=subject_company,
            candidate_website=chosen_website,
            documents=subject_docs,
            evidence_urls=support_urls,
        )
        contradicting_urls = (
            []
            if resolution.officiality == "confirmed"
            else dedupe_preserve_order(
                [
                    url
                    for website, urls in filtered_candidates.items()
                    if website != chosen_website
                    for url in urls
                    if url in allowed_docs
                ]
            )
        )
        return resolution, dedupe_preserve_order([url for url in support_urls if url in allowed_docs]), contradicting_urls

    def _resolve_contact_pair(
        self,
        state: EngineRuntimeState,
        *,
        subject_company: str,
        contact_assertions: list[ChunkContactAssertion],
        allowed_docs: dict[str, EvidenceDocument],
    ) -> dict[str, object]:
        pairs: dict[tuple[str, str], dict[str, object]] = {}
        for assertion in contact_assertions:
            person_name = clean_person_name(assertion.person_name)
            role_title = clean_role_title(assertion.role_title)
            if not person_name or not role_title:
                continue
            key = (normalize_text(person_name), normalize_text(role_title))
            bucket = pairs.setdefault(
                key,
                {
                    "person_name": person_name,
                    "role_title": role_title,
                    "support_urls": [],
                    "score": 0,
                    "support_type": assertion.support_type,
                },
            )
            bucket["support_urls"] = dedupe_preserve_order([*bucket["support_urls"], assertion.source_url])  # type: ignore[index]
            role_bonus = 3 if any(target.replace("_", " ") in normalize_text(role_title) for target in state.run.request.buyer_targets) else 0
            bucket["score"] = int(bucket["score"]) + 4 + role_bonus + self._support_weight(assertion.support_type)
            if assertion.support_type == "corroborated":
                bucket["support_type"] = "corroborated"

        if not pairs:
            return {
                "person_name": None,
                "role_title": None,
                "support_urls": [],
                "status": FieldEvidenceStatus.UNKNOWN,
                "support_type": "explicit",
                "reasoning_note": "No explicit person+role pair was grounded in the segment assertions.",
            }

        chosen = max(
            pairs.values(),
            key=lambda item: (int(item["score"]), len(item["support_urls"])),  # type: ignore[index]
        )
        support_urls = dedupe_preserve_order([url for url in chosen["support_urls"] if url in allowed_docs])  # type: ignore[index]
        status = FieldEvidenceStatus.SATISFIED if support_urls else FieldEvidenceStatus.UNKNOWN
        return {
            "person_name": chosen["person_name"],
            "role_title": chosen["role_title"],
            "support_urls": support_urls,
            "status": status,
            "support_type": chosen["support_type"],
            "reasoning_note": "Person and role kept only from explicit grounded contact assertions tied to the selected company.",
        }

    def _merge_fit_signals(
        self,
        state: EngineRuntimeState,
        *,
        subject_company: str,
        segment_resolutions: list[ChunkExtractionResolution],
        allowed_docs: dict[str, EvidenceDocument],
    ) -> list[str]:
        signals: list[str] = []
        for resolution in segment_resolutions:
            segment_company = clean_company_name(resolution.segment_company_name)
            if segment_company and not company_name_matches_anchor(segment_company, subject_company):
                continue
            signals.extend(resolution.fit_signals)
        canonical = canonicalize_search_themes(signals)
        if canonical:
            return dedupe_preserve_order([item for item in canonical if item in state.run.request.search_themes] or canonical)
        return dedupe_preserve_order(signals)

    def _assertion_weight(self, assertion: ChunkFieldAssertion, allowed_docs: dict[str, EvidenceDocument]) -> int:
        base = 1
        if assertion.field_name == "company_name":
            base += 2
        if assertion.status == FieldEvidenceStatus.SATISFIED:
            base += 2
        if assertion.status == FieldEvidenceStatus.WEAKLY_SUPPORTED:
            base += 1
        base += self._support_weight(assertion.support_type)
        if assertion.source_url in allowed_docs:
            base += {"tier_a": 3, "tier_b": 2, "tier_c": 1, "unknown": 0}.get(allowed_docs[assertion.source_url].source_tier, 0)
        return base

    def _support_weight(self, support_type: str) -> int:
        return {"corroborated": 3, "explicit": 2, "weak_inference": 1}.get(support_type, 0)

    def _assertion_matches_subject(self, company_name: str | None, subject_company: str) -> bool:
        return bool(company_name and company_name_matches_anchor(company_name, subject_company))

    def _company_name_for_assertion(self, assertion: ChunkFieldAssertion) -> str | None:
        if assertion.company_name:
            return clean_company_name(assertion.company_name)
        if assertion.field_name == "company_name" and isinstance(assertion.value, str):
            return clean_company_name(assertion.value)
        return None

    def _segment_company_name(self, assertions: list[ChunkFieldAssertion]) -> str | None:
        for assertion in assertions:
            company_name = self._company_name_for_assertion(assertion)
            if company_name:
                return company_name
        return None

    def _segment_trace_rows(self, resolution: ChunkExtractionResolution) -> list[dict]:
        rows: list[dict] = []
        for assertion in resolution.field_assertions:
            rows.append(
                {
                    "field_name": assertion.field_name,
                    "company_name": assertion.company_name,
                    "value": assertion.value,
                    "status": assertion.status,
                    "support_type": assertion.support_type,
                    "segment_index": assertion.segment_index,
                    "source_url": assertion.source_url,
                    "evidence_excerpt": assertion.evidence_excerpt,
                }
            )
        for assertion in resolution.contact_assertions:
            rows.append(
                {
                    "field_name": "contact_pair",
                    "company_name": assertion.company_name,
                    "value": f"{assertion.person_name} | {assertion.role_title}",
                    "status": assertion.status,
                    "support_type": assertion.support_type,
                    "segment_index": assertion.segment_index,
                    "source_url": assertion.source_url,
                    "evidence_excerpt": assertion.evidence_excerpt,
                }
            )
        return rows

    def _serialize_document_step(self, step: dict) -> dict:
        return {
            "url": step.get("url"),
            "llm_input_payload": step.get("llm_input_payload"),
            "llm_error": step.get("llm_error"),
            "mode": step.get("mode"),
            "estimated_input_tokens": step.get("estimated_input_tokens"),
            "llm_latency_ms": step.get("llm_latency_ms", 0),
            "parse_success": step.get("parse_success", False),
            "segment_count": step.get("segment_count", 0),
        }

    def _serialize_candidate_document_step(self, step: dict) -> dict:
        return {
            "url": step.get("url"),
            "mode": step.get("mode"),
            "estimated_input_tokens": step.get("estimated_input_tokens"),
            "llm_latency_ms": step.get("llm_latency_ms", 0),
            "parse_success": step.get("parse_success", False),
            "candidate_count": step.get("candidate_count", 0),
        }

    def _extract_discovery_candidates_from_document(
        self,
        state: EngineRuntimeState,
        document: EvidenceDocument,
        *,
        candidate_extraction_inputs: list[dict],
        candidate_extraction_raw_outputs: list[dict],
        candidate_extraction_sanitized_outputs: list[dict],
    ) -> dict:
        normalized_text = self._normalized_document_text(document)
        estimated_input_tokens = self._estimate_input_tokens(normalized_text)
        if normalized_text and estimated_input_tokens <= WHOLE_DOCUMENT_TOKEN_THRESHOLD:
            return self._extract_discovery_candidates_whole_document(
                state,
                document,
                normalized_text=normalized_text,
                estimated_input_tokens=estimated_input_tokens,
                candidate_extraction_inputs=candidate_extraction_inputs,
                candidate_extraction_raw_outputs=candidate_extraction_raw_outputs,
                candidate_extraction_sanitized_outputs=candidate_extraction_sanitized_outputs,
            )
        return self._extract_discovery_candidates_chunked(
            state,
            document,
            estimated_input_tokens=max(estimated_input_tokens, self._estimate_input_tokens(self._document_text(document))),
            candidate_extraction_inputs=candidate_extraction_inputs,
            candidate_extraction_raw_outputs=candidate_extraction_raw_outputs,
            candidate_extraction_sanitized_outputs=candidate_extraction_sanitized_outputs,
        )

    def _extract_discovery_candidates_whole_document(
        self,
        state: EngineRuntimeState,
        document: EvidenceDocument,
        *,
        normalized_text: str,
        estimated_input_tokens: int,
        candidate_extraction_inputs: list[dict],
        candidate_extraction_raw_outputs: list[dict],
        candidate_extraction_sanitized_outputs: list[dict],
    ) -> dict:
        payload = {
            "mode": "discovery_candidate_document_mode",
            "request_summary": self._request_summary(state),
            "document": self._compact_document_payload(document, raw_limit=600),
            "document_text": normalized_text,
            "section_map": self._document_section_map(document),
            "excluded_companies": self._excluded_company_names(state),
        }
        candidate_extraction_inputs.append({"url": document.url, "mode": payload["mode"], "payload": payload})
        started = time.perf_counter()
        attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
            payload=payload,
            output_model=DiscoveryCandidateExtractionResolution,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        resolution = self._sanitize_discovery_candidate_resolution(
            attempt.parsed if isinstance(attempt.parsed, DiscoveryCandidateExtractionResolution) else None,
            document=document,
            segment_index=1,
            total_segments=1,
            error=attempt.error,
        )
        if isinstance(attempt.raw, dict):
            candidate_extraction_raw_outputs.append(
                {"url": document.url, "mode": payload["mode"], "segment_index": 1, "raw_output": attempt.raw}
            )
        candidate_extraction_sanitized_outputs.append(
            {
                "url": document.url,
                "mode": payload["mode"],
                "segment_index": 1,
                "resolution": resolution.model_dump(mode="json"),
                "llm_error": attempt.error,
            }
        )
        candidates = [item for item in resolution.discovery_candidates if item.is_real_company_candidate and not item.rejection_reason]
        return {
            "url": document.url,
            "mode": payload["mode"],
            "estimated_input_tokens": estimated_input_tokens,
            "llm_latency_ms": latency_ms,
            "parse_success": isinstance(attempt.parsed, DiscoveryCandidateExtractionResolution),
            "candidate_count": len(candidates),
            "candidates": candidates,
        }

    def _extract_discovery_candidates_chunked(
        self,
        state: EngineRuntimeState,
        document: EvidenceDocument,
        *,
        estimated_input_tokens: int,
        candidate_extraction_inputs: list[dict],
        candidate_extraction_raw_outputs: list[dict],
        candidate_extraction_sanitized_outputs: list[dict],
    ) -> dict:
        segments = self._segment_document(document)
        aggregated: list[DiscoveryCompanyCandidate] = []
        total_latency_ms = 0
        parse_success = True
        for chunk in segments:
            payload = {
                "mode": "discovery_candidate_chunk_mode",
                "request_summary": self._request_summary(state),
                "document": self._compact_document_payload(document, raw_limit=600),
                "chunk": {
                    "index": chunk["index"],
                    "total": chunk["total"],
                    "text": chunk["text"],
                    "truncated": chunk["truncated"],
                    "segment_type": chunk.get("segment_type"),
                    "heading_path": chunk.get("heading_path", []),
                    "noise": bool(chunk.get("noise", False)),
                },
                "excluded_companies": self._excluded_company_names(state),
            }
            candidate_extraction_inputs.append({"url": document.url, "mode": payload["mode"], "payload": payload})
            started = time.perf_counter()
            attempt = self._agent_executor.generate_structured_attempt(
                spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
                payload=payload,
                output_model=DiscoveryCandidateExtractionResolution,
            )
            total_latency_ms += int((time.perf_counter() - started) * 1000)
            resolution = self._sanitize_discovery_candidate_resolution(
                attempt.parsed if isinstance(attempt.parsed, DiscoveryCandidateExtractionResolution) else None,
                document=document,
                segment_index=chunk["index"],
                total_segments=chunk["total"],
                error=attempt.error,
            )
            if not isinstance(attempt.parsed, DiscoveryCandidateExtractionResolution):
                parse_success = False
            if isinstance(attempt.raw, dict):
                candidate_extraction_raw_outputs.append(
                    {"url": document.url, "mode": payload["mode"], "segment_index": chunk["index"], "raw_output": attempt.raw}
                )
            candidate_extraction_sanitized_outputs.append(
                {
                    "url": document.url,
                    "mode": payload["mode"],
                    "segment_index": chunk["index"],
                    "resolution": resolution.model_dump(mode="json"),
                    "llm_error": attempt.error,
                }
            )
            aggregated.extend(
                [item for item in resolution.discovery_candidates if item.is_real_company_candidate and not item.rejection_reason]
            )
        return {
            "url": document.url,
            "mode": "discovery_candidate_chunk_mode",
            "estimated_input_tokens": estimated_input_tokens,
            "llm_latency_ms": total_latency_ms,
            "parse_success": parse_success,
            "candidate_count": len(aggregated),
            "candidates": aggregated,
        }

    def _sanitize_discovery_candidate_resolution(
        self,
        generated: DiscoveryCandidateExtractionResolution | None,
        *,
        document: EvidenceDocument,
        segment_index: int,
        total_segments: int,
        error: str | None,
    ) -> DiscoveryCandidateExtractionResolution:
        if generated is None:
            notes = ["fallback_discovery_candidate_resolution"]
            if error:
                notes.append(f"llm_error={error}")
            return DiscoveryCandidateExtractionResolution(notes=notes)
        sanitized: list[DiscoveryCompanyCandidate] = []
        for item in generated.discovery_candidates:
            candidate = self._sanitize_discovery_candidate(
                item,
                document=document,
                segment_index=segment_index,
                total_segments=total_segments,
            )
            if candidate is not None:
                sanitized.append(candidate)
        return DiscoveryCandidateExtractionResolution(
            segment_company_name=clean_company_name(generated.segment_company_name),
            discovery_candidates=sanitized,
            notes=dedupe_preserve_order([*generated.notes, *([f"candidate_source={document.url}"] if document.url else [])]),
        )

    def _sanitize_discovery_candidate(
        self,
        candidate: DiscoveryCompanyCandidate,
        *,
        document: EvidenceDocument,
        segment_index: int,
        total_segments: int,
    ) -> DiscoveryCompanyCandidate | None:
        company_name = clean_company_name(candidate.company_name)
        legal_name = clean_company_name(candidate.legal_name) or company_name
        query_name = clean_company_name(candidate.query_name) or company_name
        if not company_name:
            return None
        evidence_urls = [url for url in candidate.evidence_urls if url == document.url]
        if not evidence_urls and candidate.is_real_company_candidate:
            evidence_urls = [document.url]
        evidence_excerpt = candidate.evidence_excerpt or ""
        if not evidence_excerpt:
            excerpt_seed = company_name if candidate.is_real_company_candidate else (candidate.rejection_reason or company_name)
            evidence_excerpt = self._excerpt_for_value(self._normalized_document_text(document), excerpt_seed)
        return candidate.model_copy(
            update={
                "company_name": company_name,
                "legal_name": legal_name,
                "query_name": query_name,
                "brand_aliases": dedupe_preserve_order(
                    [clean_company_name(alias) for alias in candidate.brand_aliases if clean_company_name(alias)]
                ),
                "candidate_website": canonicalize_website(candidate.candidate_website),
                "country_code": canonicalize_country_code(candidate.country_code) if candidate.country_code else None,
                "theme_tags": dedupe_preserve_order(canonicalize_search_themes(candidate.theme_tags) or candidate.theme_tags),
                "evidence_urls": evidence_urls,
                "evidence_excerpt": evidence_excerpt,
            }
        )

    def _score_discovery_candidates(
        self,
        candidates: list[DiscoveryCompanyCandidate],
        *,
        preferred_country: str | None,
        min_size: int | None,
        max_size: int | None,
        request_text: str,
    ) -> list[DiscoveryCompanyCandidate]:
        deduped = self._dedupe_discovery_candidates(
            [item for item in candidates if item.is_real_company_candidate and not item.rejection_reason]
        )
        return [
            self._score_discovery_candidate(
                item,
                preferred_country=preferred_country,
                min_size=min_size,
                max_size=max_size,
                request_text=request_text,
            )
            for item in deduped
        ]

    def _extract_document_assertions(
        self,
        state: EngineRuntimeState,
        document: EvidenceDocument,
        *,
        focus_company: str | None,
        extraction_inputs: list[dict],
        extraction_raw_outputs: list[dict],
        extraction_sanitized_outputs: list[dict],
        segment_field_resolutions: list[dict],
    ) -> dict:
        normalized_text = self._normalized_document_text(document)
        estimated_input_tokens = self._estimate_input_tokens(normalized_text)
        if normalized_text and estimated_input_tokens <= WHOLE_DOCUMENT_TOKEN_THRESHOLD:
            return self._extract_whole_document_assertions(
                state,
                document,
                focus_company=focus_company,
                normalized_text=normalized_text,
                estimated_input_tokens=estimated_input_tokens,
                extraction_inputs=extraction_inputs,
                extraction_raw_outputs=extraction_raw_outputs,
                extraction_sanitized_outputs=extraction_sanitized_outputs,
                segment_field_resolutions=segment_field_resolutions,
            )
        return self._extract_segment_assertions(
            state,
            document,
            focus_company=focus_company,
            estimated_input_tokens=max(estimated_input_tokens, self._estimate_input_tokens(self._document_text(document))),
            extraction_inputs=extraction_inputs,
            extraction_raw_outputs=extraction_raw_outputs,
            extraction_sanitized_outputs=extraction_sanitized_outputs,
            segment_field_resolutions=segment_field_resolutions,
        )

    def _extract_whole_document_assertions(
        self,
        state: EngineRuntimeState,
        document: EvidenceDocument,
        *,
        focus_company: str | None,
        normalized_text: str,
        estimated_input_tokens: int,
        extraction_inputs: list[dict],
        extraction_raw_outputs: list[dict],
        extraction_sanitized_outputs: list[dict],
        segment_field_resolutions: list[dict],
    ) -> dict:
        payload = {
            "mode": "focus_locked_document_mode",
            "request_summary": self._request_summary(state),
            "focus_company": focus_company,
            "document": self._compact_document_payload(document, raw_limit=600),
            "document_text": normalized_text,
            "section_map": self._document_section_map(document),
            "excluded_companies": self._excluded_company_names(state),
        }
        extraction_inputs.append({"url": document.url, "mode": payload["mode"], "payload": payload})
        started = time.perf_counter()
        attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
            payload=payload,
            output_model=ChunkExtractionResolution,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        pseudo_chunk = {
            "index": 1,
            "total": 1,
            "text": normalized_text,
            "truncated": False,
            "segment_type": "document",
            "heading_path": [document.title] if document.title else [],
            "noise": False,
        }
        sanitized = self._sanitize_segment_resolution(
            attempt.parsed if isinstance(attempt.parsed, ChunkExtractionResolution) else None,
            document=document,
            chunk=pseudo_chunk,
            focus_company=focus_company,
            error=attempt.error,
        )
        if isinstance(attempt.raw, dict):
            extraction_raw_outputs.append(
                {"url": document.url, "mode": payload["mode"], "chunk_index": 1, "raw_output": attempt.raw}
            )
        extraction_sanitized_outputs.append(
            {
                "url": document.url,
                "mode": payload["mode"],
                "chunk_index": 1,
                "resolution": sanitized.model_dump(mode="json"),
                "llm_error": attempt.error,
            }
        )
        segment_field_resolutions.extend(self._segment_trace_rows(sanitized))
        return {
            "url": document.url,
            "mode": payload["mode"],
            "estimated_input_tokens": estimated_input_tokens,
            "llm_input_payload": payload,
            "llm_error": attempt.error,
            "llm_latency_ms": latency_ms,
            "parse_success": isinstance(attempt.parsed, ChunkExtractionResolution),
            "segment_count": 1,
            "segment_resolutions": [sanitized],
        }

    def _extract_segment_assertions(
        self,
        state: EngineRuntimeState,
        document: EvidenceDocument,
        *,
        focus_company: str | None,
        estimated_input_tokens: int,
        extraction_inputs: list[dict],
        extraction_raw_outputs: list[dict],
        extraction_sanitized_outputs: list[dict],
        segment_field_resolutions: list[dict],
    ) -> dict:
        segments = self._segment_document(document)
        sanitized_segments: list[ChunkExtractionResolution] = []
        llm_error: str | None = None
        total_latency_ms = 0
        parse_success = True
        for chunk in segments:
            payload = {
                "mode": "focus_locked_chunk_mode",
                "request_summary": self._request_summary(state),
                "focus_company": focus_company,
                "document": self._compact_document_payload(document, raw_limit=600),
                "chunk": {
                    "index": chunk["index"],
                    "total": chunk["total"],
                    "text": chunk["text"],
                    "truncated": chunk["truncated"],
                    "segment_type": chunk.get("segment_type"),
                    "heading_path": chunk.get("heading_path", []),
                    "noise": bool(chunk.get("noise", False)),
                },
                "excluded_companies": self._excluded_company_names(state),
            }
            extraction_inputs.append({"url": document.url, "mode": payload["mode"], "payload": payload})
            started = time.perf_counter()
            attempt = self._agent_executor.generate_structured_attempt(
                spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
                payload=payload,
                output_model=ChunkExtractionResolution,
            )
            total_latency_ms += int((time.perf_counter() - started) * 1000)
            sanitized = self._sanitize_segment_resolution(
                attempt.parsed if isinstance(attempt.parsed, ChunkExtractionResolution) else None,
                document=document,
                chunk=chunk,
                focus_company=focus_company,
                error=attempt.error,
            )
            if llm_error is None and attempt.error:
                llm_error = attempt.error
            if not isinstance(attempt.parsed, ChunkExtractionResolution):
                parse_success = False
            if isinstance(attempt.raw, dict):
                extraction_raw_outputs.append(
                    {"url": document.url, "mode": payload["mode"], "chunk_index": chunk["index"], "raw_output": attempt.raw}
                )
            extraction_sanitized_outputs.append(
                {
                    "url": document.url,
                    "mode": payload["mode"],
                    "chunk_index": chunk["index"],
                    "resolution": sanitized.model_dump(mode="json"),
                    "llm_error": attempt.error,
                }
            )
            segment_field_resolutions.extend(self._segment_trace_rows(sanitized))
            sanitized_segments.append(sanitized)
        return {
            "url": document.url,
            "mode": "focus_locked_chunk_mode",
            "estimated_input_tokens": estimated_input_tokens,
            "llm_input_payload": {
                "mode": "focus_locked_chunk_mode",
                "segment_count": len(segments),
                "document": self._compact_document_payload(document, raw_limit=600),
            },
            "llm_error": llm_error,
            "llm_latency_ms": total_latency_ms,
            "parse_success": parse_success,
            "segment_count": len(sanitized_segments),
            "segment_resolutions": sanitized_segments,
        }

    def _estimate_input_tokens(self, text: str) -> int:
        return int(math.ceil(len(text or "") / 4))

    def _normalized_document_text(self, document: EvidenceDocument) -> str:
        if document.logical_segments:
            parts: list[str] = []
            for segment in document.logical_segments:
                if segment.noise or not segment.text.strip():
                    continue
                heading = " > ".join(segment.heading_path) if segment.heading_path else ""
                prefix = []
                if heading:
                    prefix.append(f"Heading path: {heading}")
                if segment.segment_type:
                    prefix.append(f"Segment type: {segment.segment_type}")
                prelude = "\n".join(prefix)
                parts.append(f"{prelude}\n\n{segment.text.strip()}".strip())
            if parts:
                return "\n\n".join(parts)
        if document.normalized_blocks:
            rendered: list[str] = []
            for block in document.normalized_blocks:
                text = block.text.strip()
                if not text:
                    continue
                if block.block_type == "heading":
                    level = min(max(block.heading_level or 1, 1), 6)
                    rendered.append(f"{'#' * level} {text}")
                else:
                    rendered.append(text)
            if rendered:
                return "\n\n".join(rendered)
        return self._document_text(document)

    def _document_section_map(self, document: EvidenceDocument) -> list[dict]:
        if document.logical_segments:
            return [
                {
                    "segment_id": item.segment_id,
                    "segment_type": item.segment_type,
                    "heading_path": item.heading_path,
                    "noise": item.noise,
                }
                for item in document.logical_segments[:24]
            ]
        if document.normalized_blocks:
            return [
                {
                    "block_index": item.index,
                    "block_type": item.block_type,
                    "heading_level": item.heading_level,
                    "text": item.text[:120],
                }
                for item in document.normalized_blocks[:24]
            ]
        return []

    def _segment_document(self, document: EvidenceDocument) -> list[dict]:
        if document.logical_segments:
            logical_chunks = self._logical_segments_to_chunks(document)
            if logical_chunks:
                total = len(logical_chunks)
                for item in logical_chunks:
                    item["total"] = total
                return logical_chunks
        text = document.raw_content or ""
        chunk_size = 4000
        overlap = 300
        chunks: list[dict] = []
        if len(text) <= chunk_size:
            chunks.append({"index": 1, "text": text, "used_fallback_split": False, "truncated": False})
        else:
            start = 0
            index = 0
            total_length = len(text)
            while start < total_length:
                end = min(start + chunk_size, total_length)
                piece = text[start:end]
                if not piece:
                    break
                index += 1
                chunks.append(
                    {
                        "index": index,
                        "text": piece,
                        "used_fallback_split": False,
                        "truncated": False,
                    }
                )
                if end >= total_length:
                    break
                start = max(start + 1, end - overlap)
        total = len(chunks)
        for item in chunks:
            item["total"] = total
        return chunks

    def _logical_segments_to_chunks(self, document: EvidenceDocument) -> list[dict]:
        chunks: list[dict] = []
        chunk_size = 4000
        overlap = 300
        for segment in document.logical_segments:
            if segment.noise:
                continue
            base_text = segment.text.strip()
            if not base_text:
                continue
            prefix = ""
            if segment.heading_path:
                prefix = f"Heading path: {' > '.join(segment.heading_path)}\n"
            prefix += f"Segment type: {segment.segment_type}\n\n"
            text = f"{prefix}{base_text}".strip()
            if len(text) <= chunk_size:
                chunks.append(
                    {
                        "index": len(chunks) + 1,
                        "text": text,
                        "used_fallback_split": False,
                        "truncated": False,
                        "segment_type": segment.segment_type,
                        "heading_path": segment.heading_path,
                        "noise": segment.noise,
                    }
                )
                continue
            start = 0
            while start < len(base_text):
                end = min(start + max(1, chunk_size - len(prefix)), len(base_text))
                piece = base_text[start:end].strip()
                if not piece:
                    break
                chunks.append(
                    {
                        "index": len(chunks) + 1,
                        "text": f"{prefix}{piece}".strip(),
                        "used_fallback_split": True,
                        "truncated": end < len(base_text),
                        "segment_type": segment.segment_type,
                        "heading_path": segment.heading_path,
                        "noise": segment.noise,
                    }
                )
                if end >= len(base_text):
                    break
                start = max(start + 1, end - overlap)
        return chunks

    def _chunk_document(self, document: EvidenceDocument) -> list[dict]:
        return self._segment_document(document)

    def _sanitize_segment_resolution(
        self,
        generated: ChunkExtractionResolution | None,
        *,
        document: EvidenceDocument,
        chunk: dict,
        focus_company: str | None,
        error: str | None,
    ) -> ChunkExtractionResolution:
        if generated is None:
            notes = ["fallback_chunk_resolution"]
            if error:
                notes.append(f"llm_error={error}")
            if chunk.get("truncated"):
                notes.append("content_truncated_after_chunk_limit")
            return ChunkExtractionResolution(notes=notes)
        segment_company = clean_company_name(generated.segment_company_name)
        field_assertions = [
            self._sanitize_chunk_field_assertion(
                item,
                document=document,
                chunk=chunk,
                segment_company=segment_company,
                focus_company=focus_company,
            )
            for item in generated.field_assertions
        ]
        field_assertions = [item for item in field_assertions if item is not None]
        if segment_company is None:
            segment_company = self._segment_company_name(field_assertions)
        if segment_company is None and focus_company and document_matches_anchor_strong(document, focus_company):
            segment_company = clean_company_name(focus_company)
        contact_assertions = [
            self._sanitize_chunk_contact_assertion(
                item,
                document=document,
                chunk=chunk,
                segment_company=segment_company,
                focus_company=focus_company,
            )
            for item in generated.contact_assertions
        ]
        contact_assertions = [item for item in contact_assertions if item is not None]
        if segment_company is None and contact_assertions:
            segment_company = clean_company_name(contact_assertions[0].company_name)
        notes = dedupe_preserve_order([*generated.notes, *([f"chunk_source={document.url}"] if document.url else [])])
        if chunk.get("truncated"):
            notes.append("content_truncated_after_chunk_limit")
        return ChunkExtractionResolution(
            segment_company_name=segment_company,
            field_assertions=field_assertions,
            contact_assertions=contact_assertions,
            fit_signals=dedupe_preserve_order(canonicalize_search_themes(generated.fit_signals) or generated.fit_signals),
            contradictions=dedupe_preserve_order(generated.contradictions),
            notes=dedupe_preserve_order(notes),
        )

    def _sanitize_chunk_field_assertion(
        self,
        assertion: ChunkFieldAssertion,
        *,
        document: EvidenceDocument,
        chunk: dict,
        segment_company: str | None,
        focus_company: str | None,
    ) -> ChunkFieldAssertion | None:
        company_name = clean_company_name(assertion.company_name)
        if company_name is None and assertion.field_name == "company_name" and isinstance(assertion.value, str):
            company_name = clean_company_name(assertion.value)
        if company_name is None and segment_company is not None:
            company_name = segment_company
        if company_name is None and focus_company and document_matches_anchor_strong(document, focus_company):
            company_name = clean_company_name(focus_company)

        value = assertion.value
        if assertion.field_name == "website":
            value = canonicalize_website(str(assertion.value)) if assertion.value is not None else None
            if value is None:
                return None
        elif assertion.field_name == "country":
            value = canonicalize_country_code(str(assertion.value)) if assertion.value is not None else None
            if value is None:
                return None
        elif assertion.field_name == "employee_estimate":
            if not isinstance(assertion.value, int):
                return None
            value = assertion.value
        else:
            if company_name is None and isinstance(assertion.value, str):
                company_name = clean_company_name(assertion.value)
                value = company_name
        status = assertion.status
        if status == FieldEvidenceStatus.UNKNOWN and value is not None:
            if assertion.field_name in {"company_name", "country"}:
                status = FieldEvidenceStatus.SATISFIED
            elif assertion.field_name == "website":
                status = (
                    FieldEvidenceStatus.SATISFIED
                    if assertion.support_type in {"explicit", "corroborated"}
                    else FieldEvidenceStatus.WEAKLY_SUPPORTED
                )
            elif assertion.field_name == "employee_estimate":
                status = (
                    FieldEvidenceStatus.SATISFIED
                    if assertion.employee_count_type in {"exact", "range"}
                    else FieldEvidenceStatus.WEAKLY_SUPPORTED
                )

        return assertion.model_copy(
            update={
                "company_name": company_name,
                "value": value,
                "status": status,
                "segment_index": chunk["index"],
                "source_url": document.url,
                "evidence_excerpt": self._excerpt_for_value(chunk["text"], value or company_name),
            }
        )

    def _sanitize_chunk_contact_assertion(
        self,
        assertion: ChunkContactAssertion,
        *,
        document: EvidenceDocument,
        chunk: dict,
        segment_company: str | None,
        focus_company: str | None,
    ) -> ChunkContactAssertion | None:
        person_name = clean_person_name(assertion.person_name)
        role_title = clean_role_title(assertion.role_title)
        if not person_name or not role_title:
            return None
        company_name = clean_company_name(assertion.company_name) or segment_company
        if company_name is None and focus_company and document_matches_anchor_strong(document, focus_company):
            company_name = clean_company_name(focus_company)
        if company_name is None:
            return None
        status = assertion.status
        if status == FieldEvidenceStatus.UNKNOWN:
            status = (
                FieldEvidenceStatus.SATISFIED
                if assertion.support_type in {"explicit", "corroborated"}
                else FieldEvidenceStatus.WEAKLY_SUPPORTED
            )
        return assertion.model_copy(
            update={
                "person_name": person_name,
                "role_title": role_title,
                "company_name": company_name,
                "status": status,
                "segment_index": chunk["index"],
                "source_url": document.url,
                "evidence_excerpt": self._excerpt_for_value(chunk["text"], person_name) or self._excerpt_for_value(chunk["text"], role_title),
            }
        )

    def _excerpt_for_value(self, text: str, value) -> str:
        if not text:
            return ""
        needle = normalize_text(str(value)) if value is not None else ""
        haystack = normalize_text(text)
        if needle and needle in haystack:
            index = haystack.index(needle)
            start = max(0, index - 80)
            end = min(len(text), start + 220)
            return text[start:end]
        return text[:220]

    def _excluded_company_names(self, state: EngineRuntimeState) -> list[str]:
        run_company_names = [item.company_name for item in state.run.accepted_leads]
        if normalize_text(state.environment) == "development":
            return dedupe_preserve_order(run_company_names)
        return dedupe_preserve_order(
            [
                *state.memory.searched_company_names,
                *run_company_names,
                *request_scoped_company_exclusions(state.memory.company_observations, state.run.request),
            ]
        )

    def _focus_source_result(self, source_result: SourcePassResult, focus_company: str | None) -> SourcePassResult:
        if not focus_company or not source_result.documents:
            return source_result
        focused_documents = [
            item
            for item in source_result.documents
            if document_matches_anchor_strong(item, focus_company)
            or (item.company_anchor and company_name_matches_anchor(item.company_anchor, focus_company))
        ]
        if not focused_documents:
            return source_result
        focused_urls = {item.url for item in focused_documents}
        focused_trace = []
        for item in source_result.research_trace:
            selected_urls = [url for url in item.selected_urls if url in focused_urls]
            if not selected_urls:
                continue
            focused_trace.append(
                item.model_copy(
                    update={
                        "selected_urls": selected_urls,
                        "documents_selected": len(selected_urls),
                    }
                )
            )
        focused_candidates = [
            item
            for item in source_result.website_candidates
            if any(url in focused_urls for url in item.evidence_urls)
        ]
        return source_result.model_copy(
            update={
                "documents": focused_documents,
                "website_candidates": focused_candidates,
                "research_trace": focused_trace or source_result.research_trace,
                "notes": dedupe_preserve_order([*source_result.notes, f"focus_locked_documents={len(focused_documents)}"]),
            }
        )

    def _single_document_source_result(self, source_result: SourcePassResult, document: EvidenceDocument) -> SourcePassResult:
        matching_trace = []
        for item in source_result.research_trace:
            if document.url in item.selected_urls:
                matching_trace.append(
                    item.model_copy(update={"selected_urls": [document.url], "documents_selected": 1})
                )
        return source_result.model_copy(update={"documents": [document], "research_trace": matching_trace or source_result.research_trace[:1]})

    def _prioritize_documents(self, documents: list[EvidenceDocument], focus_company: str | None) -> list[EvidenceDocument]:
        def rank(item: EvidenceDocument) -> tuple[int, int, int]:
            focus_match = 1 if focus_company and document_matches_anchor_strong(item, focus_company) else 0
            company_controlled = 1 if item.is_company_controlled_source else 0
            tier_rank = {"tier_a": 3, "tier_b": 2, "tier_c": 1}.get(item.source_tier, 0)
            return (focus_match, company_controlled, tier_rank)

        return sorted(documents, key=rank, reverse=True)

    def _sanitize_focus_resolution(
        self,
        *,
        documents: list[EvidenceDocument],
        attempts: int,
        excluded_companies: list[str],
        ledger_candidates: list[DiscoveryCompanyCandidate],
    ) -> CompanyFocusResolution:
        resolution = self._resolve_focus_from_candidates(
            ledger_candidates,
            attempts=attempts,
            preferred_name=None,
            excluded_companies=excluded_companies,
            trust_preferred=False,
        )
        if resolution.selected_company and not any(
            company_name_matches_anchor(item.company_name, resolution.selected_company) for item in ledger_candidates
        ):
            return CompanyFocusResolution(
                selection_mode="none",
                discovery_candidates=ledger_candidates,
                notes=["selected_company_missing_from_llm_candidate_ledger"],
            )
        return resolution.model_copy(
            update={
                "notes": dedupe_preserve_order([*resolution.notes, f"candidate_ledger_documents={len(documents)}"]),
            }
        )

    def _sanitize_generated_discovery_candidates(
        self,
        candidates: list[DiscoveryCompanyCandidate],
        *,
        allowed_urls: set[str],
    ) -> list[DiscoveryCompanyCandidate]:
        sanitized: list[DiscoveryCompanyCandidate] = []
        for item in candidates:
            company_name = clean_company_name(item.company_name or item.legal_name or item.query_name)
            if not company_name:
                continue
            legal_name = clean_company_name(item.legal_name) or company_name
            candidate_website = canonicalize_website(item.candidate_website)
            sanitized.append(
                item.model_copy(
                    update={
                        "company_name": company_name,
                        "legal_name": legal_name,
                        "query_name": clean_company_name(item.query_name) or clean_company_name(item.company_name),
                        "brand_aliases": dedupe_preserve_order([clean_company_name(alias) for alias in item.brand_aliases if clean_company_name(alias)]),
                        "candidate_website": candidate_website,
                        "evidence_urls": [url for url in item.evidence_urls if url in allowed_urls],
                    }
                )
            )
        return sanitized

    def _fallback_discovery_candidates(
        self,
        documents: list[EvidenceDocument],
        *,
        excluded_companies: list[str],
        request_text: str,
        preferred_country: str | None,
        min_size: int | None,
        max_size: int | None,
    ) -> list[DiscoveryCompanyCandidate]:
        candidates: list[DiscoveryCompanyCandidate] = []
        for document in documents:
            extracted = self._discovery_candidates_from_document(document, request_text=request_text)
            for item in extracted:
                if any(company_name_matches_anchor(item.company_name, excluded) for excluded in excluded_companies):
                    continue
                candidates.append(self._score_discovery_candidate(item, preferred_country=preferred_country, min_size=min_size, max_size=max_size, request_text=request_text))
        return self._dedupe_discovery_candidates(candidates)

    def _discovery_candidates_from_document(self, document: EvidenceDocument, *, request_text: str) -> list[DiscoveryCompanyCandidate]:
        names: list[str] = []
        person, company = parse_candidate_from_text(self._document_text(document), document.url)
        if company and company.name and not self._discovery_name_is_generic(company.name):
            names.append(company.name)
        title_candidate = self._discovery_title_company_name(document.title)
        if title_candidate:
            names.append(title_candidate)
        names.extend(item for item in self._explicit_company_names(document) if not self._discovery_name_is_generic(item))
        names = dedupe_preserve_order([clean_company_name(item) for item in names if clean_company_name(item)])
        if not names:
            return []
        text = self._document_text(document)
        website = None
        for name in names:
            website = extracted_official_website_from_document(document, name)
            if website:
                break
        hint_value, hint_type = extract_employee_size_hint(text)
        country_code = self._country_hint_from_text(text)
        location_hint = self._location_hint_from_text(text)
        operational_status = "non_operational" if text_has_spanish_non_operational_signal(text) else "active"
        theme_tags = self._theme_tags_from_text(text, request_text)
        primary = self._choose_primary_company_name(names, preferred=title_candidate)
        legal_name = self._choose_primary_company_name(
            [item for item in names if self._name_has_legal_suffix(item)] or names,
            preferred=title_candidate,
        )
        return [
            DiscoveryCompanyCandidate(
                company_name=primary,
                legal_name=derive_anchor_legal_name(legal_name, [document]) or legal_name,
                query_name=derive_anchor_query_name(primary, [document], website) or primary,
                brand_aliases=derive_brand_aliases(primary, [document], website),
                country_code=country_code,
                location_hint=location_hint,
                theme_tags=theme_tags,
                candidate_website=website,
                employee_count_hint_value=hint_value,
                employee_count_hint_type=hint_type,
                operational_status=operational_status,
                evidence_urls=[document.url],
            )
        ]

    def _score_discovery_candidate(
        self,
        candidate: DiscoveryCompanyCandidate,
        *,
        preferred_country: str | None,
        min_size: int | None,
        max_size: int | None,
        request_text: str,
    ) -> DiscoveryCompanyCandidate:
        score = 0.15
        reasons: list[str] = []
        hard_rejections: list[str] = []
        if candidate.operational_status == "non_operational":
            hard_rejections.append("non_operational_entity")
        if preferred_country and candidate.country_code and candidate.country_code != preferred_country:
            hard_rejections.append("country_mismatch")
        if candidate.employee_count_hint_value is not None:
            if min_size is not None and candidate.employee_count_hint_value < min_size:
                hard_rejections.append("size_below_request")
            if max_size is not None and candidate.employee_count_hint_value > max_size:
                hard_rejections.append("size_above_request")
        if candidate.country_code == preferred_country or (preferred_country == "es" and candidate.location_hint):
            score += 0.25
            reasons.append("country_or_location_matches")
        if candidate.employee_count_hint_value is not None and not any(reason.startswith("size_") for reason in hard_rejections):
            score += 0.20
            reasons.append(f"size_hint_{candidate.employee_count_type if hasattr(candidate,'employee_count_type') else candidate.employee_count_hint_type}")
        if candidate.theme_tags:
            score += 0.18
            reasons.append("theme_match")
        if candidate.candidate_website:
            score += 0.08
            reasons.append("explicit_website_field")
        if any(token in normalize_text(candidate.legal_name or candidate.company_name) for token in {"sl", "sociedad", "sa", "slu"}):
            score += 0.06
            reasons.append("legal_entity_name_detected")
        if len((candidate.legal_name or candidate.company_name).split()) >= 2:
            score += 0.05
            reasons.append("clean_company_ficha")
        score = max(0.0, min(score, 0.99))
        return candidate.model_copy(update={"selection_score": score, "selection_reasons": dedupe_preserve_order(reasons), "hard_rejections": dedupe_preserve_order(hard_rejections)})

    def _dedupe_discovery_candidates(self, candidates: list[DiscoveryCompanyCandidate]) -> list[DiscoveryCompanyCandidate]:
        grouped: dict[str, DiscoveryCompanyCandidate] = {}
        for item in candidates:
            key = self._discovery_candidate_key(item.company_name)
            current = grouped.get(key)
            if current is None:
                grouped[key] = item
                continue
            grouped[key] = current.model_copy(
                update={
                    "legal_name": current.legal_name or item.legal_name,
                    "query_name": current.query_name or item.query_name,
                    "brand_aliases": dedupe_preserve_order([*current.brand_aliases, *item.brand_aliases]),
                    "country_code": current.country_code or item.country_code,
                    "location_hint": current.location_hint or item.location_hint,
                    "theme_tags": dedupe_preserve_order([*current.theme_tags, *item.theme_tags]),
                    "candidate_website": current.candidate_website or item.candidate_website,
                    "employee_count_hint_value": current.employee_count_hint_value or item.employee_count_hint_value,
                    "employee_count_hint_type": current.employee_count_hint_type if current.employee_count_hint_type != "unknown" else item.employee_count_hint_type,
                    "operational_status": "non_operational" if "non_operational" in {current.operational_status, item.operational_status} else current.operational_status,
                    "evidence_urls": dedupe_preserve_order([*current.evidence_urls, *item.evidence_urls]),
                    "selection_score": max(current.selection_score, item.selection_score),
                    "selection_reasons": dedupe_preserve_order([*current.selection_reasons, *item.selection_reasons]),
                    "hard_rejections": dedupe_preserve_order([*current.hard_rejections, *item.hard_rejections]),
                }
            )
        return sorted(grouped.values(), key=lambda item: (not item.hard_rejections, item.selection_score, len(item.evidence_urls)), reverse=True)

    def _merge_discovery_candidates(
        self,
        fallback: list[DiscoveryCompanyCandidate],
        llm: list[DiscoveryCompanyCandidate],
    ) -> list[DiscoveryCompanyCandidate]:
        return self._dedupe_discovery_candidates([*fallback, *llm])

    def _resolve_focus_from_candidates(
        self,
        candidates: list[DiscoveryCompanyCandidate],
        *,
        attempts: int,
        preferred_name: str | None,
        excluded_companies: list[str],
        trust_preferred: bool = False,
    ) -> CompanyFocusResolution:
        filtered = [
            item for item in candidates
            if not any(company_name_matches_anchor(item.company_name, excluded) for excluded in excluded_companies)
        ]
        if preferred_name:
            filtered = sorted(filtered, key=lambda item: company_name_matches_anchor(item.company_name, preferred_name), reverse=True)
        if not filtered:
            return CompanyFocusResolution(selection_mode="none", discovery_candidates=candidates, notes=["no_discovery_candidates"])
        viable = [item for item in filtered if not item.hard_rejections]
        selected: DiscoveryCompanyCandidate | None = None
        notes: list[str] = []
        preferred_candidates = [item for item in filtered if preferred_name and company_name_matches_anchor(item.company_name, preferred_name)]
        if trust_preferred and preferred_candidates:
            preferred_viable = [item for item in preferred_candidates if not item.hard_rejections]
            if preferred_viable:
                selected = preferred_viable[0]
                notes.append("focus_trusted_from_llm")
            else:
                notes.append("focus_rejected_by_hard_contradiction")
        if attempts <= 1:
            if selected is None and viable:
                top = viable[0]
                second_score = viable[1].selection_score if len(viable) > 1 else -1
                mode = self._discovery_selection_mode(top)
                if mode == "confident" or (mode == "plausible" and top.selection_score >= second_score + 0.12):
                    selected = top
        else:
            if selected is None and viable:
                selected = viable[0]
            elif selected is None and filtered:
                selected = filtered[0]
        rejected = [
            RejectedCompanyCandidate(company_name=item.company_name, reason="; ".join(item.hard_rejections or ["lower_ranked_candidate"]), evidence_urls=item.evidence_urls[:2])
            for item in filtered[1:4]
        ]
        if selected is None:
            return CompanyFocusResolution(
                selection_mode="none",
                discovery_candidates=filtered,
                rejected_candidates=rejected,
                notes=dedupe_preserve_order([*notes, "no_plausible_focus_after_current_discovery_batches"]),
            )
        mode = self._discovery_selection_mode(selected)
        return CompanyFocusResolution(
            selected_company=selected.legal_name or selected.company_name,
            legal_name=selected.legal_name or selected.company_name,
            query_name=selected.query_name or selected.company_name,
            brand_aliases=selected.brand_aliases,
            selection_mode=mode if not selected.hard_rejections else "fallback",
            confidence=max(0.2, min(selected.selection_score, 0.95)),
            evidence_urls=selected.evidence_urls[:4],
            selection_reasons=selected.selection_reasons,
            hard_rejections=selected.hard_rejections,
            rejected_candidates=rejected,
            discovery_candidates=filtered,
            notes=dedupe_preserve_order([*notes, f"selected_by={mode}"]),
        )

    def _selected_company_supported_by_documents(self, company_name: str, documents: list[EvidenceDocument]) -> bool:
        for item in documents:
            if any(company_name_matches_anchor(candidate, company_name) for candidate in self._explicit_company_names(item)):
                return True
            if self._discovery_title_company_name(item.title) and company_name_matches_anchor(self._discovery_title_company_name(item.title), company_name):
                return True
            _, company = parse_candidate_from_text(self._document_text(item), item.url)
            if company and company.name and company_name_matches_anchor(company.name, company_name):
                return True
        return False

    def _discovery_selection_mode(self, candidate: DiscoveryCompanyCandidate) -> str:
        if candidate.hard_rejections:
            return "fallback"
        if candidate.selection_score >= 0.72:
            return "confident"
        if candidate.selection_score >= 0.42:
            return "plausible"
        return "fallback"

    def _explicit_company_names(self, document: EvidenceDocument) -> list[str]:
        text = self._document_text(document)
        matches: list[str] = []
        patterns = [
            re.compile(r"\b(?:company|empresa|raz[oó]n social|denominaci[oó]n)\s*:\s*([^\n|]+)", re.IGNORECASE),
            re.compile(r"^([A-Z0-9][A-Za-z0-9 .,&'/-]{4,120}\b(?:S\.L\.|SL|S\.A\.|SA|SOCIEDAD LIMITADA|SOCIEDAD ANONIMA))\b", re.IGNORECASE | re.MULTILINE),
        ]
        for pattern in patterns:
            for match in pattern.finditer(text):
                candidate = clean_company_name(match.group(1))
                if candidate:
                    matches.append(candidate)
        return dedupe_preserve_order(matches)

    def _discovery_title_company_name(self, title: str) -> str | None:
        primary = re.split(r"\s+-\s+", title or "", maxsplit=1)[0].strip(" |")
        primary = re.sub(r"\b(Tel[eé]fono y direcci[oó]n|Infoempresa|Empresite|Datoscif|Iberinform)\b.*$", "", primary, flags=re.IGNORECASE).strip(" .-|")
        primary = clean_company_name(primary)
        if not primary:
            return None
        normalized = normalize_text(primary)
        if any(token in normalized for token in {"actividad", "categoria", "categorias", "provincia", "empresas "}):
            return None
        if len(primary.split()) == 1 and len(primary) < 4:
            return None
        return primary

    def _country_hint_from_text(self, text: str) -> str | None:
        normalized = normalize_text(text)
        if "spain" in normalized or "espana" in normalized or any(city in normalized for city in SPANISH_CITY_HINTS):
            return "es"
        if "united states" in normalized or "usa" in normalized or "new york" in normalized:
            return "us"
        return None

    def _location_hint_from_text(self, text: str) -> str | None:
        normalized = normalize_text(text)
        for city in SPANISH_CITY_HINTS:
            if city in normalized:
                return city.title()
        return None

    def _theme_tags_from_text(self, text: str, request_text: str) -> list[str]:
        normalized = normalize_text(f"{text}\n{request_text}")
        mapping = {
            "software": {"software", "saas"},
            "ia": {"ia", "artificial intelligence", "inteligencia artificial", "genai", "ai"},
            "automation": {"automation", "automatizacion", "automatización"},
            "data": {"data", "datos", "analytics"},
        }
        tags: list[str] = []
        for label, keywords in mapping.items():
            if any(keyword in normalized for keyword in keywords):
                tags.append(label)
        return dedupe_preserve_order(tags)

    def _discovery_candidate_key(self, value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", "", normalize_text(value or ""))

    def _document_text(self, document: EvidenceDocument) -> str:
        return "\n".join(part for part in [document.raw_content, document.snippet, document.title] if part)

    def _document_snapshot(self, document: EvidenceDocument) -> dict:
        return {
            "url": document.url,
            "title": document.title,
            "snippet": document.snippet,
            "raw_content": document.raw_content,
            "has_raw_html": bool(document.raw_html),
            "content_format": document.content_format,
            "source_tier": document.source_tier,
            "source_type": document.source_type,
            "company_anchor": document.company_anchor,
            "is_company_controlled_source": document.is_company_controlled_source,
            "chunker_adapter": document.chunker_adapter,
            "normalized_block_count": len(document.normalized_blocks),
            "logical_segment_count": len(document.logical_segments),
            "debug_markdown_artifact_path": document.debug_markdown_artifact_path,
        }

    def _choose_primary_company_name(self, names: list[str], *, preferred: str | None = None) -> str:
        def score(value: str) -> tuple[int, int, int]:
            preferred_match = 1 if preferred and company_name_matches_anchor(value, preferred) else 0
            legal = 1 if self._name_has_legal_suffix(value) else 0
            generic_penalty = 0 if not self._discovery_name_is_generic(value) else -1
            return (preferred_match + legal + generic_penalty, len(value.split()), len(value))

        return max(names, key=score)

    def _name_has_legal_suffix(self, value: str | None) -> bool:
        normalized = normalize_text(value or "")
        return any(token in normalized.split() for token in {"sl", "sa", "slu", "sociedad", "limitada", "anonima", "anónima"})

    def _discovery_name_is_generic(self, value: str | None) -> bool:
        normalized = normalize_text(value or "")
        if not normalized:
            return True
        if normalized.startswith("de "):
            return True
        generic_phrases = {
            "empresa de software",
            "software company",
            "technology company",
            "de software",
            "de tecnologia",
            "de tecnologia en madrid",
        }
        return normalized in generic_phrases
