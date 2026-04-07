from __future__ import annotations

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import AssembledLeadDossier, AssemblyResolution, ResearchTraceEntry, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    candidate_company_names_from_document,
    company_name_matches_anchor,
    dedupe_preserve_order,
    merge_documents,
    sanitize_assembly_resolution,
    select_anchor_company,
)
from lolo_lead_management.engine.state import EngineRuntimeState


class AssembleStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor

    def execute(self, state: EngineRuntimeState) -> AssembledLeadDossier:
        source_result = state.current_source_result
        if source_result is None:
            state.current_assembler_trace = {
                "status": "no_source_result",
                "used_fallback": True,
            }
            return AssembledLeadDossier(sourcing_status=SourcingStatus.NO_CANDIDATE, notes=["no_source_result_to_assemble"])

        prior_dossier = state.current_dossier
        focus_company = self._select_focus_company(state, source_result, prior_dossier)
        focused_result = self._focus_source_result(source_result, focus_company)
        focused_payload = self._compact_source_result_payload(focused_result.model_dump(mode="json"))
        prior_payload = self._compact_dossier_payload(prior_dossier.model_dump(mode="json")) if prior_dossier else None

        working_dossier = prior_dossier
        step_traces: list[dict] = []
        successful_resolution = False
        for index, document in enumerate(focused_result.documents[:4], start=1):
            document_result = self._single_document_source_result(focused_result, document)
            assembler_payload = {
                "request": state.run.request.model_dump(mode="json"),
                "focus_company": focus_company,
                "excluded_companies": self._excluded_company_names(state),
                "assembly_policy": {
                    "mode": "incremental",
                    "missing_fields_allowed": True,
                    "one_document_at_a_time": True,
                    "reuse_prior_dossier_when_still_coherent": True,
                    "prefer_cautious_partial_dossier_over_guessing": True,
                    "fields_may_come_from_different_documents_and_different_passes": True,
                },
                "source_result": self._compact_source_result_payload(document_result.model_dump(mode="json")),
                "prior_dossier": self._compact_dossier_payload(working_dossier.model_dump(mode="json")) if working_dossier else prior_payload,
            }
            attempt = self._agent_executor.generate_structured_attempt(
                spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
                payload=assembler_payload,
                output_model=AssemblyResolution,
            )
            if attempt.parsed is not None:
                successful_resolution = True
                working_dossier = sanitize_assembly_resolution(
                    attempt.parsed,
                    request=state.run.request,
                    source_result=document_result,
                    prior_dossier=working_dossier,
                )
            step_traces.append(
                {
                    "index": index,
                    "url": document.url,
                    "status": "ok" if attempt.error is None else "llm_error",
                    "error": attempt.error,
                    "used_fallback": attempt.parsed is None,
                    "assembler_input": assembler_payload,
                    "assembler_raw_output": self._compact_resolution_payload(attempt.raw) if isinstance(attempt.raw, dict) else None,
                    "current_dossier": self._compact_dossier_payload(working_dossier.model_dump(mode="json")) if working_dossier else None,
                }
            )

        sanitized = working_dossier or sanitize_assembly_resolution(
            None,
            request=state.run.request,
            source_result=focused_result,
            prior_dossier=prior_dossier,
        )
        state.current_assembler_trace = {
            "status": "ok" if successful_resolution else "llm_error",
            "used_fallback": not successful_resolution,
            "error": None if successful_resolution else next((item["error"] for item in step_traces if item.get("error")), None),
            "focus_company": focus_company,
            "source_result": focused_payload,
            "prior_dossier": prior_payload,
            "document_steps": step_traces,
            "sanitized_output": self._compact_dossier_payload(sanitized.model_dump(mode="json")),
        }
        return sanitized

    def _select_focus_company(
        self,
        state: EngineRuntimeState,
        source_result: SourcePassResult,
        prior_dossier: AssembledLeadDossier | None,
    ) -> str | None:
        if source_result.anchored_company_name:
            return source_result.anchored_company_name
        if prior_dossier and prior_dossier.anchored_company_name:
            return prior_dossier.anchored_company_name
        return select_anchor_company(
            source_result.documents,
            prior_anchor=prior_dossier.anchored_company_name if prior_dossier else None,
            excluded_companies=self._excluded_company_names(state),
        )

    def _excluded_company_names(self, state: EngineRuntimeState) -> list[str]:
        accepted_companies = [item.company_name for item in state.run.accepted_leads]
        return dedupe_preserve_order([*state.memory.searched_company_names, *accepted_companies])

    def _focus_source_result(self, source_result: SourcePassResult, focus_company: str | None) -> SourcePassResult:
        if focus_company is None:
            return source_result
        focused_documents = []
        for document in source_result.documents:
            candidates = candidate_company_names_from_document(document)
            if any(company_name_matches_anchor(candidate, focus_company) for candidate in candidates):
                focused_documents.append(document)
                continue
            if document.company_anchor and company_name_matches_anchor(document.company_anchor, focus_company):
                focused_documents.append(document)
                continue
            lowered = f"{document.title}\n{document.snippet}\n{document.raw_content}".lower()
            if focus_company.lower() in lowered:
                focused_documents.append(document)
        focused_documents = merge_documents(focused_documents or source_result.documents)
        selected_urls = {item.url for item in focused_documents}
        focused_trace = [
            item.model_copy(update={"selected_urls": [url for url in item.selected_urls if url in selected_urls]})
            for item in source_result.research_trace
            if any(url in selected_urls for url in item.selected_urls)
        ]
        return source_result.model_copy(
            update={
                "documents": focused_documents,
                "anchored_company_name": focus_company,
                "research_trace": focused_trace or source_result.research_trace,
            }
        )

    def _single_document_source_result(self, source_result: SourcePassResult, document) -> SourcePassResult:
        matching_trace = self._trace_for_document(source_result.research_trace, document.url)
        trace = [matching_trace] if matching_trace else []
        return source_result.model_copy(
            update={
                "documents": [document],
                "research_trace": trace,
            }
        )

    def _trace_for_document(self, entries: list[ResearchTraceEntry], url: str) -> ResearchTraceEntry | None:
        for entry in entries:
            if url in entry.selected_urls:
                return entry.model_copy(update={"selected_urls": [url], "documents_selected": 1})
        return None

    def _compact_source_result_payload(self, payload: dict) -> dict:
        return {
            "sourcing_status": payload.get("sourcing_status"),
            "anchored_company_name": payload.get("anchored_company_name"),
            "notes": payload.get("notes", [])[:6],
            "executed_queries": [
                {
                    "query": item.get("query"),
                    "objective": item.get("objective"),
                    "research_phase": item.get("research_phase"),
                    "candidate_company_name": item.get("candidate_company_name"),
                }
                for item in payload.get("executed_queries", [])[:4]
            ],
            "research_trace": [
                {
                    "query_executed": item.get("query_executed"),
                    "research_phase": item.get("research_phase"),
                    "objective": item.get("objective"),
                    "documents_considered": item.get("documents_considered"),
                    "documents_selected": item.get("documents_selected"),
                    "selected_urls": item.get("selected_urls", [])[:4],
                }
                for item in payload.get("research_trace", [])[:4]
            ],
            "documents": [self._compact_evidence_item(item) for item in payload.get("documents", [])[:5]],
        }

    def _compact_dossier_payload(self, payload: dict) -> dict:
        return {
            "sourcing_status": payload.get("sourcing_status"),
            "query_used": payload.get("query_used"),
            "anchored_company_name": payload.get("anchored_company_name"),
            "company": payload.get("company"),
            "person": payload.get("person"),
            "fit_signals": payload.get("fit_signals", [])[:5],
            "notes": payload.get("notes", [])[:6],
            "contradictions": payload.get("contradictions", [])[:5],
            "evidence": [self._compact_evidence_item(item) for item in payload.get("evidence", [])[:4]],
            "field_evidence": [
                {
                    "field_name": item.get("field_name"),
                    "value": item.get("value"),
                    "status": item.get("status"),
                    "supporting_urls": [doc.get("url") for doc in item.get("supporting_evidence", [])[:3]],
                    "contradicting_urls": [doc.get("url") for doc in item.get("contradicting_evidence", [])[:2]],
                    "reasoning_note": item.get("reasoning_note"),
                }
                for item in payload.get("field_evidence", [])
            ],
        }

    def _compact_resolution_payload(self, payload: dict) -> dict:
        return {
            "subject_company_name": payload.get("subject_company_name"),
            "website": payload.get("website"),
            "country_code": payload.get("country_code"),
            "employee_estimate": payload.get("employee_estimate"),
            "person_name": payload.get("person_name"),
            "role_title": payload.get("role_title"),
            "fit_signals": payload.get("fit_signals", [])[:5],
            "selected_evidence_urls": payload.get("selected_evidence_urls", [])[:5],
            "field_assertions": payload.get("field_assertions", [])[:6],
            "contradictions": payload.get("contradictions", [])[:5],
            "unresolved_fields": payload.get("unresolved_fields", [])[:6],
            "notes": payload.get("notes", [])[:6],
        }

    def _compact_evidence_item(self, payload: dict) -> dict:
        return {
            "url": payload.get("url"),
            "title": payload.get("title"),
            "domain": payload.get("domain"),
            "source_type": payload.get("source_type"),
            "snippet": (payload.get("snippet") or "")[:280],
            "raw_content": (payload.get("raw_content") or "")[:900],
            "source_quality": payload.get("source_quality"),
            "query_executed": payload.get("query_executed"),
            "research_phase": payload.get("research_phase"),
            "objective": payload.get("objective"),
            "is_company_controlled_source": payload.get("is_company_controlled_source"),
            "is_publisher_like": payload.get("is_publisher_like"),
        }
