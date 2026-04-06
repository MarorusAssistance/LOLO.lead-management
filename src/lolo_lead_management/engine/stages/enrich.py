from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import ResearchQueryPlan, ResearchTraceEntry, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    build_research_query_plan,
    choose_queries,
    collect_missing_fields_for_enrichment,
    enrich_document_metadata,
    merge_research_query_plans,
    merge_documents,
    sanitize_research_query_plan,
)
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.search import SearchPort


class EnrichStage:
    def __init__(self, *, search_port: SearchPort, agent_executor: StageAgentExecutor, max_results: int) -> None:
        self._search_port = search_port
        self._agent_executor = agent_executor
        self._max_results = max_results

    def execute(self, state: EngineRuntimeState) -> SourcePassResult:
        dossier = state.current_dossier
        if dossier is None or dossier.company is None:
            return SourcePassResult(sourcing_status=SourcingStatus.NO_CANDIDATE, notes=["no_dossier_to_enrich"])

        missing_fields = collect_missing_fields_for_enrichment(dossier, state.run.request)
        fallback_plan = build_research_query_plan(
            state.run.request,
            state.run.applied_relaxation_stage,
            anchor_company=dossier.company.name,
            missing_fields=missing_fields,
            mode="enrich",
        )
        try:
            generated_plan = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.ENRICH],
                payload={
                    "request": state.run.request.model_dump(mode="json"),
                    "current_dossier": self._compact_dossier_payload(dossier.model_dump(mode="json")),
                    "memory": {
                        "scope": state.memory.scope,
                        "query_history": state.memory.query_history[-20:],
                        "visited_urls": state.memory.visited_urls[-30:],
                        "searched_company_names": state.memory.searched_company_names[-25:],
                        "registered_lead_names": state.memory.registered_lead_names[-15:],
                        "consecutive_hard_miss_runs": state.memory.consecutive_hard_miss_runs,
                    },
                    "missing_fields": missing_fields,
                    "fallback_plan": fallback_plan.model_dump(mode="json"),
                },
                output_model=ResearchQueryPlan,
            )
        except Exception:
            generated_plan = None
        sanitized_plan = sanitize_research_query_plan(
            generated_plan,
            fallback=fallback_plan,
            request=state.run.request,
            anchor_company=dossier.company.name,
        )
        plan = merge_research_query_plans(sanitized_plan, fallback_plan)
        selected_queries = choose_queries(plan, state.memory.query_history + ([state.current_query] if state.current_query else []), limit=3)
        if not selected_queries:
            return SourcePassResult(sourcing_status=SourcingStatus.NO_CANDIDATE, query_plan=plan, notes=["no_unused_queries_left_for_enrichment"])

        documents = []
        research_trace: list[ResearchTraceEntry] = []
        for query in selected_queries:
            state.run.budget.search_calls_used += 1
            results = self._search_port.web_search(query, max_results=self._max_results)
            filtered = [item for item in results if item.url not in state.memory.visited_urls]
            enriched = self._enrich_missing_content(filtered, query.query)
            selected = merge_documents(enriched[: self._max_results])
            research_trace.append(
                ResearchTraceEntry(
                    query_planned=query.query,
                    query_executed=query.query,
                    research_phase=query.research_phase,
                    objective=query.objective,
                    candidate_company_name=query.candidate_company_name,
                    documents_considered=len(filtered),
                    documents_selected=len(selected),
                    selected_urls=[item.url for item in selected],
                )
            )
            documents.extend(selected)
        return SourcePassResult(
            sourcing_status=SourcingStatus.FOUND if documents else SourcingStatus.NO_CANDIDATE,
            query_plan=plan,
            executed_queries=selected_queries,
            documents=merge_documents(documents),
            anchored_company_name=dossier.company.name,
            research_trace=research_trace,
            notes=[f"enrichment_queries_executed={len(selected_queries)}"],
        )

    def _enrich_missing_content(self, documents, query_text: str):
        pending = [item for item in documents if not item.raw_content]
        fetched: dict[str, str] = {}
        if pending:
            with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
                pairs = list(executor.map(lambda entry: (entry.url, self._safe_fetch_page(entry.url)), pending))
            fetched = {url: text for url, text in pairs}
        return [
            enrich_document_metadata(
                item.model_copy(
                    update={
                        "raw_content": item.raw_content or fetched.get(item.url, ""),
                        "query_executed": query_text,
                        "query_planned": item.query_planned or query_text,
                    }
                ),
                anchor_company=item.company_anchor,
            )
            for item in documents
        ]

    def _safe_fetch_page(self, url: str) -> str:
        try:
            return self._search_port.fetch_page(url)
        except Exception:
            return ""

    def _compact_dossier_payload(self, payload: dict) -> dict:
        compact = dict(payload)
        compact["evidence"] = [self._compact_evidence_item(item) for item in payload.get("evidence", [])[:6]]
        compact["field_evidence"] = [
            {
                **item,
                "supporting_evidence": [self._compact_evidence_item(doc) for doc in item.get("supporting_evidence", [])[:3]],
                "contradicting_evidence": [self._compact_evidence_item(doc) for doc in item.get("contradicting_evidence", [])[:2]],
            }
            for item in payload.get("field_evidence", [])
        ]
        return compact

    def _compact_evidence_item(self, payload: dict) -> dict:
        compact = dict(payload)
        compact["snippet"] = (compact.get("snippet") or "")[:400]
        compact["raw_content"] = (compact.get("raw_content") or "")[:1800]
        return compact
