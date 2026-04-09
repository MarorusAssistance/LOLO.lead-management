from __future__ import annotations

import re
from collections import defaultdict

from lolo_lead_management.domain.enums import FieldEvidenceStatus, SourceQuality, SourcingStatus, StageName
from lolo_lead_management.domain.models import (
    AssembledFieldEvidence,
    AssembledLeadDossier,
    AssemblyResolution,
    ChunkExtractionResolution,
    CompanyCandidate,
    CompanyFocusResolution,
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
    canonicalize_website,
    clean_company_name,
    company_name_matches_anchor,
    company_name_matches_anchor_strict,
    dedupe_preserve_order,
    derive_anchor_legal_name,
    derive_anchor_query_name,
    derive_brand_aliases,
    document_matches_anchor_strong,
    domain_from_url,
    domain_is_directory,
    domain_is_publisher_like,
    extracted_official_website_from_document,
    extract_employee_size_hint,
    merge_documents,
    normalize_text,
    parse_candidate_from_text,
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
        chunk_inputs: list[dict] = []
        chunk_raw_outputs: list[dict] = []
        chunk_sanitized_outputs: list[dict] = []
        chunk_merge_summary: list[dict] = []
        for document in documents:
            self._extract_chunk_clues(
                state,
                document,
                focus_company=focus_company,
                allow_chunking=True,
                chunk_inputs=chunk_inputs,
                chunk_raw_outputs=chunk_raw_outputs,
                chunk_sanitized_outputs=chunk_sanitized_outputs,
                chunk_merge_summary=chunk_merge_summary,
            )
        payload = self._assembly_payload(
            state,
            compact_source,
            state.current_dossier,
            focus_company,
            chunk_summary=chunk_merge_summary or None,
        )
        attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
            payload=payload,
            output_model=AssemblyResolution,
        )
        if isinstance(attempt.parsed, AssemblyResolution):
            assembled = self._llm_first_dossier(
                attempt.parsed,
                source_result=compact_source,
                prior_dossier=state.current_dossier,
                focus_company=focus_company,
            )
            used_fallback = False
            status = "ok"
        else:
            assembled = build_fallback_assembled_dossier(
                request=state.run.request,
                source_result=compact_source,
                prior_dossier=state.current_dossier,
            )
            used_fallback = True
            status = attempt.error or "fallback"

        state.current_assembler_trace = {
            "status": status,
            "used_fallback": used_fallback,
            "focus_company": focus_company,
            "input_documents": [self._document_snapshot(item) for item in documents],
            "llm_input_payload": payload,
            "llm_error": attempt.error,
            "llm_raw_output": attempt.raw if isinstance(attempt.raw, dict) else None,
            "evidence_classification": self._evidence_classification(attempt.parsed, documents),
            "field_confidence": self._field_confidence(attempt.parsed),
            "cross_company_reasons": attempt.parsed.contradictions if isinstance(attempt.parsed, AssemblyResolution) else [],
            "document_steps": [],
            "chunk_inputs": chunk_inputs,
            "chunk_raw_outputs": chunk_raw_outputs,
            "chunk_sanitized_outputs": chunk_sanitized_outputs,
            "chunk_merge_summary": chunk_merge_summary,
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
        fallback_candidates = self._fallback_discovery_candidates(
            source_result.documents,
            excluded_companies=excluded_companies,
                request_text=state.run.request.user_text,
                preferred_country=state.run.request.constraints.preferred_country,
                min_size=state.run.request.constraints.min_company_size,
                max_size=state.run.request.constraints.max_company_size,
        )
        payload = self._company_selection_payload(state, source_result, fallback_candidates, excluded_companies)
        attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
            payload=payload,
            output_model=CompanyFocusResolution,
        )
        allowed_urls = {item.url for item in source_result.documents}
        sanitized_llm_candidates = self._sanitize_generated_discovery_candidates(
            attempt.parsed.discovery_candidates if isinstance(attempt.parsed, CompanyFocusResolution) else [],
            allowed_urls=allowed_urls,
        )
        resolution = self._llm_first_focus_resolution(
            attempt.parsed if isinstance(attempt.parsed, CompanyFocusResolution) else None,
            documents=source_result.documents,
            fallback_candidates=fallback_candidates,
            allowed_urls=allowed_urls,
        )
        self.last_company_selection_trace = {
            "mode": "company_selection_mode",
            "status": "ok" if isinstance(attempt.parsed, CompanyFocusResolution) else (attempt.error or "fallback"),
            "error": None if isinstance(attempt.parsed, CompanyFocusResolution) else attempt.error,
            "input_documents": [self._document_snapshot(item) for item in source_result.documents],
            "excluded_companies": excluded_companies,
            "llm_input_payload": payload,
            "llm_raw_output": attempt.raw if isinstance(attempt.raw, dict) else None,
            "sanitized_discovery_candidates": [item.model_dump(mode="json") for item in sanitized_llm_candidates],
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
            "documents": [self._compact_document_payload(item, raw_limit=90) for item in source_result.documents[:3]],
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

    def _llm_first_dossier(
        self,
        generated: AssemblyResolution,
        *,
        source_result: SourcePassResult,
        prior_dossier: AssembledLeadDossier | None,
        focus_company: str | None,
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
        company_name = clean_company_name(generated.subject_company_name) or focus_company or source_result.anchored_company_name
        if not company_name:
            return AssembledLeadDossier(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_used=source_result.executed_queries[0].query if source_result.executed_queries else None,
                notes=dedupe_preserve_order([*source_result.notes, "llm_subject_company_missing"]),
                evidence=source_result.documents[:6],
                research_trace=source_result.research_trace,
                documents_considered=sum(item.documents_considered for item in source_result.research_trace),
                documents_selected=len(source_result.documents[:6]),
            )
        website = canonicalize_website(generated.candidate_website or generated.website)
        country_code = generated.country_code or (prior_dossier.company.country_code if prior_dossier and prior_dossier.company else None)
        employee_estimate = generated.employee_estimate if generated.employee_estimate is not None else (prior_dossier.company.employee_estimate if prior_dossier and prior_dossier.company else None)
        person_name = generated.person_name or (prior_dossier.person.full_name if prior_dossier and prior_dossier.person else None)
        role_title = generated.role_title or (prior_dossier.person.role_title if prior_dossier and prior_dossier.person else None)
        fit_signals = dedupe_preserve_order([*generated.fit_signals, *(prior_dossier.fit_signals if prior_dossier else [])])

        company = CompanyCandidate(name=company_name, website=website, country_code=country_code, employee_estimate=employee_estimate)
        person = PersonCandidate(full_name=person_name, role_title=role_title) if person_name or role_title else None
        website_resolution = WebsiteResolution(
            candidate_website=website,
            officiality=generated.website_officiality or "unknown",
            confidence=generated.website_confidence or 0,
            evidence_urls=[url for url in generated.website_evidence_urls if url in allowed_docs],
            signals=dedupe_preserve_order(generated.website_signals),
            risks=dedupe_preserve_order(generated.website_risks),
        )
        field_evidence = [
            self._field_evidence_from_assertion("company_name", company.name, assertion_map.get("company_name"), allowed_docs),
            self._field_evidence_from_assertion("website", company.website, assertion_map.get("website"), allowed_docs),
            self._field_evidence_from_assertion("country", company.country_code, assertion_map.get("country"), allowed_docs),
            self._field_evidence_from_assertion("employee_estimate", company.employee_estimate, assertion_map.get("employee_estimate"), allowed_docs),
            self._field_evidence_from_assertion("person_name", person.full_name if person else None, assertion_map.get("person_name"), allowed_docs),
            self._field_evidence_from_assertion("role_title", person.role_title if person else None, assertion_map.get("role_title"), allowed_docs),
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
                    reasoning_note="Fit signals preserved directly from LLM-reviewed evidence.",
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
            notes=dedupe_preserve_order([*source_result.notes, *generated.confidence_notes, *generated.notes, "assembled_by_llm_first"]),
            anchored_company_name=company_name,
            website_resolution=website_resolution,
            research_trace=source_result.research_trace,
            field_evidence=field_evidence,
            contradictions=dedupe_preserve_order(generated.contradictions),
            evidence_quality=self._source_quality_from_docs(evidence),
            documents_considered=sum(item.documents_considered for item in source_result.research_trace),
            documents_selected=len(evidence),
        )

    def _field_evidence_from_assertion(
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
                reasoning_note=assertion.reasoning_note or f"{field_name} preserved from LLM field assertion.",
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
            reasoning_note=f"{field_name} has no explicit field assertion from the LLM.",
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

    def _extract_chunk_clues(
        self,
        state: EngineRuntimeState,
        document: EvidenceDocument,
        *,
        focus_company: str | None,
        allow_chunking: bool,
        chunk_inputs: list[dict],
        chunk_raw_outputs: list[dict],
        chunk_sanitized_outputs: list[dict],
        chunk_merge_summary: list[dict],
    ) -> dict | None:
        if not allow_chunking:
            return None
        chunks = self._chunk_document(document)
        if not chunks:
            return None
        sanitized_chunks: list[ChunkExtractionResolution] = []
        for chunk in chunks:
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
                },
                "excluded_companies": self._excluded_company_names(state),
            }
            chunk_inputs.append({"url": document.url, "payload": payload})
            attempt = self._agent_executor.generate_structured_attempt(
                spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
                payload=payload,
                output_model=ChunkExtractionResolution,
            )
            sanitized = self._sanitize_chunk_resolution(
                attempt.parsed if isinstance(attempt.parsed, ChunkExtractionResolution) else None,
                document=document,
                chunk=chunk,
                error=attempt.error,
            )
            if isinstance(attempt.raw, dict):
                chunk_raw_outputs.append({"url": document.url, "chunk_index": chunk["index"], "raw_output": attempt.raw})
            chunk_sanitized_outputs.append(
                {
                    "url": document.url,
                    "chunk_index": chunk["index"],
                    "resolution": sanitized.model_dump(mode="json"),
                    "llm_error": attempt.error,
                }
            )
            sanitized_chunks.append(sanitized)
        merged = self._merge_chunk_resolutions(sanitized_chunks)
        summary = merged.model_dump(mode="json")
        summary["url"] = document.url
        chunk_merge_summary.append(summary)
        return summary

    def _chunk_document(self, document: EvidenceDocument) -> list[dict]:
        text = document.raw_content or ""
        chunk_size = 4000
        overlap = 300
        if len(text) <= chunk_size:
            return []
        chunks: list[dict] = []
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

    def _sanitize_chunk_resolution(
        self,
        generated: ChunkExtractionResolution | None,
        *,
        document: EvidenceDocument,
        chunk: dict,
        error: str | None,
    ) -> ChunkExtractionResolution:
        if generated is None:
            notes = ["fallback_chunk_resolution"]
            if error:
                notes.append(f"llm_error={error}")
            if chunk.get("truncated"):
                notes.append("content_truncated_after_chunk_limit")
            return ChunkExtractionResolution(notes=notes)
        candidate_website = canonicalize_website(generated.candidate_website)
        notes = dedupe_preserve_order([*generated.notes, *([f"chunk_source={document.url}"] if document.url else [])])
        if chunk.get("truncated"):
            notes.append("content_truncated_after_chunk_limit")
        return generated.model_copy(
            update={
                "candidate_website": candidate_website,
                "website_signals": dedupe_preserve_order(generated.website_signals),
                "person_clues": dedupe_preserve_order(generated.person_clues),
                "role_clues": dedupe_preserve_order(generated.role_clues),
                "fit_signals": dedupe_preserve_order(generated.fit_signals),
                "contradictions": dedupe_preserve_order(generated.contradictions),
                "notes": dedupe_preserve_order(notes),
            }
        )

    def _merge_chunk_resolutions(self, resolutions: list[ChunkExtractionResolution]) -> ChunkExtractionResolution:
        merged = ChunkExtractionResolution()
        priority = {"exact": 3, "range": 2, "estimate": 1, "unknown": 0}
        for item in resolutions:
            if not merged.candidate_website and item.candidate_website:
                merged = merged.model_copy(update={"candidate_website": item.candidate_website})
            if not merged.country_code and item.country_code:
                merged = merged.model_copy(update={"country_code": item.country_code})
            if not merged.location_hint and item.location_hint:
                merged = merged.model_copy(update={"location_hint": item.location_hint})
            if (
                item.employee_count_hint_value is not None
                and priority[item.employee_count_hint_type] >= priority[merged.employee_count_hint_type]
            ):
                merged = merged.model_copy(
                    update={
                        "employee_count_hint_value": item.employee_count_hint_value,
                        "employee_count_hint_type": item.employee_count_hint_type,
                    }
                )
            merged = merged.model_copy(
                update={
                    "website_signals": dedupe_preserve_order([*merged.website_signals, *item.website_signals]),
                    "person_clues": dedupe_preserve_order([*merged.person_clues, *item.person_clues]),
                    "role_clues": dedupe_preserve_order([*merged.role_clues, *item.role_clues]),
                    "fit_signals": dedupe_preserve_order([*merged.fit_signals, *item.fit_signals]),
                    "contradictions": dedupe_preserve_order([*merged.contradictions, *item.contradictions]),
                    "notes": dedupe_preserve_order([*merged.notes, *item.notes]),
                }
            )
        return merged

    def _excluded_company_names(self, state: EngineRuntimeState) -> list[str]:
        return []

    def _focus_source_result(self, source_result: SourcePassResult, focus_company: str | None) -> SourcePassResult:
        return source_result

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
        generated: CompanyFocusResolution | None,
        *,
        documents: list[EvidenceDocument],
        attempts: int,
        fallback_candidates: list[DiscoveryCompanyCandidate],
        excluded_companies: list[str],
        llm_candidates: list[DiscoveryCompanyCandidate],
    ) -> CompanyFocusResolution:
        candidates = self._merge_discovery_candidates(fallback_candidates, llm_candidates)
        preferred_name = generated.selected_company if generated and generated.selected_company else None
        trust_preferred = bool(
            preferred_name
            and (
                self._selected_company_supported_by_documents(preferred_name, documents)
                or bool(
                    generated
                    and any(url in {item.url for item in documents} for url in generated.evidence_urls)
                )
            )
        )
        resolution = self._resolve_focus_from_candidates(
            candidates,
            attempts=attempts,
            preferred_name=preferred_name,
            excluded_companies=excluded_companies,
            trust_preferred=trust_preferred,
        )
        if (
            generated
            and trust_preferred
            and resolution.selected_company
            and company_name_matches_anchor(resolution.selected_company, generated.selected_company)
        ):
            clean_selected = clean_company_name(generated.selected_company) or resolution.selected_company
            clean_legal = clean_company_name(generated.legal_name) or clean_selected
            clean_query = clean_company_name(generated.query_name) or clean_selected
            clean_aliases = dedupe_preserve_order(
                [
                    clean_company_name(alias)
                    for alias in generated.brand_aliases
                    if clean_company_name(alias)
                ]
            ) or resolution.brand_aliases
            resolution = resolution.model_copy(
                update={
                    "selected_company": clean_selected,
                    "legal_name": clean_legal,
                    "query_name": clean_query,
                    "brand_aliases": clean_aliases,
                    "notes": dedupe_preserve_order([*resolution.notes, "focus_fields_preserved_from_llm"]),
                }
            )
        return resolution

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
            "source_tier": document.source_tier,
            "source_type": document.source_type,
            "company_anchor": document.company_anchor,
            "is_company_controlled_source": document.is_company_controlled_source,
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
