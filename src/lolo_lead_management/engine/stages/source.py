from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import ResearchQueryPlan, ResearchTraceEntry, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    build_research_query_plan,
    choose_queries,
    dedupe_preserve_order,
    enrich_document_metadata,
    merge_documents,
    sanitize_research_query_plan,
)
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.search import SearchPort


class SourceStage:
    def __init__(self, *, search_port: SearchPort, agent_executor: StageAgentExecutor, max_results: int) -> None:
        self._search_port = search_port
        self._agent_executor = agent_executor
        self._max_results = max_results

    def execute(self, state: EngineRuntimeState) -> SourcePassResult:
        request = state.run.request
        fallback_plan = build_research_query_plan(
            request,
            state.run.applied_relaxation_stage,
            mode="source",
        )
        try:
            generated_plan = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.SOURCE],
                payload={
                    "request": request.model_dump(mode="json"),
                    "memory": state.memory.model_dump(mode="json"),
                    "relaxation_stage": state.run.applied_relaxation_stage,
                    "fallback_plan": fallback_plan.model_dump(mode="json"),
                },
                output_model=ResearchQueryPlan,
            )
        except Exception:
            generated_plan = None

        plan = sanitize_research_query_plan(generated_plan, fallback=fallback_plan)
        selected_queries = choose_queries(plan, state.memory.query_history + ([state.current_query] if state.current_query else []), limit=4)
        if not selected_queries:
            return SourcePassResult(sourcing_status=SourcingStatus.NO_CANDIDATE, query_plan=plan, notes=["no_unused_queries_left"])

        documents = []
        research_trace: list[ResearchTraceEntry] = []
        for query in selected_queries:
            state.run.budget.search_calls_used += 1
            results = self._search_port.web_search(query, max_results=self._max_results)
            filtered = [
                item for item in results if item.url not in state.memory.visited_urls and self._is_usable_search_result(item.url)
            ]
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

        documents = merge_documents(documents)
        anchored_company = next((item.candidate_company_name for item in selected_queries if item.candidate_company_name), None)
        if not documents:
            return SourcePassResult(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_plan=plan,
                executed_queries=selected_queries,
                anchored_company_name=anchored_company,
                research_trace=research_trace,
                notes=["no_documents_selected"],
            )

        return SourcePassResult(
            sourcing_status=SourcingStatus.FOUND,
            query_plan=plan,
            executed_queries=selected_queries,
            documents=documents,
            anchored_company_name=anchored_company,
            research_trace=research_trace,
            notes=[f"queries_executed={len(selected_queries)}"],
        )

    def _enrich_missing_content(self, documents, query_text: str):
        pending = [item for item in documents if not item.raw_content]
        fetched: dict[str, str] = {}
        if pending:
            with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
                pairs = list(executor.map(lambda entry: (entry.url, self._safe_fetch_page(entry.url)), pending))
            fetched = {url: text for url, text in pairs}
        enriched = []
        for item in documents:
            enriched.append(
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
            )
        return enriched

    def _safe_fetch_page(self, url: str) -> str:
        try:
            return self._search_port.fetch_page(url)
        except Exception:
            return ""

    def _is_usable_search_result(self, url: str) -> bool:
        try:
            hostname = (urlparse(url).hostname or "").removeprefix("www.")
        except ValueError:
            return False
        blocked = {"facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com"}
        return hostname not in blocked
