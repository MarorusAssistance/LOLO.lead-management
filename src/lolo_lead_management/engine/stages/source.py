from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import ResearchQueryPlan, ResearchTraceEntry, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    build_research_query_plan,
    candidate_company_names_from_document,
    choose_queries,
    company_name_matches_anchor,
    dedupe_preserve_order,
    enrich_document_metadata,
    merge_research_query_plans,
    merge_documents,
    sanitize_research_query_plan,
    select_anchor_company,
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
                    "memory": self._memory_payload(state),
                    "relaxation_stage": state.run.applied_relaxation_stage,
                    "fallback_plan": fallback_plan.model_dump(mode="json"),
                },
                output_model=ResearchQueryPlan,
            )
        except Exception:
            generated_plan = None

        sanitized_plan = sanitize_research_query_plan(generated_plan, fallback=fallback_plan, request=request)
        plan = merge_research_query_plans(sanitized_plan, fallback_plan)
        query_history = state.memory.query_history + ([state.current_query] if state.current_query else [])
        selected_queries = choose_queries(plan, query_history, limit=2)
        if not selected_queries:
            return SourcePassResult(sourcing_status=SourcingStatus.NO_CANDIDATE, query_plan=plan, notes=["no_unused_queries_left"])

        documents = []
        executed_queries = []
        research_trace: list[ResearchTraceEntry] = []
        excluded_companies = self._excluded_company_names(state)
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
            executed_queries.append(query)

        documents = merge_documents(documents)
        anchored_company = next((item.candidate_company_name for item in executed_queries if item.candidate_company_name), None)
        if anchored_company is None and documents:
            anchored_company = select_anchor_company(documents, excluded_companies=excluded_companies)
        if anchored_company is None and documents:
            return SourcePassResult(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_plan=plan,
                executed_queries=executed_queries,
                documents=documents,
                research_trace=research_trace,
                notes=["no_fresh_company_anchor"],
            )

        if anchored_company:
            anchor_plan = build_research_query_plan(
                request,
                state.run.applied_relaxation_stage,
                anchor_company=anchored_company,
                mode="source_anchor_followup",
            )
            anchor_queries = self._choose_anchor_queries(
                anchor_plan,
                query_history + [item.query for item in executed_queries],
            )
            for query in anchor_queries:
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
                executed_queries.append(query)
            documents = merge_documents(documents)
            documents = self._focus_documents(documents, anchored_company)

        if not documents:
            return SourcePassResult(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_plan=plan,
                executed_queries=executed_queries,
                anchored_company_name=anchored_company,
                research_trace=research_trace,
                notes=["no_documents_selected"],
            )

        return SourcePassResult(
            sourcing_status=SourcingStatus.FOUND,
            query_plan=plan,
            executed_queries=executed_queries,
            documents=documents,
            anchored_company_name=anchored_company,
            research_trace=research_trace,
            notes=[f"queries_executed={len(executed_queries)}"],
        )

    def _excluded_company_names(self, state: EngineRuntimeState) -> list[str]:
        run_company_names = [item.company_name for item in state.run.accepted_leads]
        return dedupe_preserve_order([*state.memory.searched_company_names, *run_company_names])

    def _focus_documents(self, documents, anchored_company: str):
        focused = []
        for item in documents:
            candidates = candidate_company_names_from_document(item)
            if any(company_name_matches_anchor(candidate, anchored_company) for candidate in candidates):
                focused.append(item)
                continue
            if item.company_anchor and company_name_matches_anchor(item.company_anchor, anchored_company):
                focused.append(item)
                continue
            text = f"{item.title}\n{item.snippet}\n{item.raw_content}".lower()
            if anchored_company.lower() in text:
                focused.append(item)
        return merge_documents(focused or documents)

    def _choose_anchor_queries(self, plan: ResearchQueryPlan, query_history: list[str]):
        selected = []
        selected_queries = set()
        priority_groups = [
            lambda item: item.research_phase == "company_anchoring",
            lambda item: "buyer persona" in item.objective.lower() or "role title" in item.objective.lower(),
            lambda item: item.research_phase == "evidence_closing" or "company size" in item.objective.lower(),
            lambda item: item.research_phase == "field_acquisition" and "fit signals" in item.objective.lower(),
        ]
        for predicate in priority_groups:
            for query in choose_queries(plan, query_history + [item.query for item in selected], limit=6):
                if query.query in selected_queries:
                    continue
                if predicate(query):
                    selected.append(query)
                    selected_queries.add(query.query)
                    break
            if len(selected) >= 3:
                break
        if len(selected) < 3:
            for query in choose_queries(plan, query_history + [item.query for item in selected], limit=6):
                if query.query in selected_queries:
                    continue
                selected.append(query)
                selected_queries.add(query.query)
                if len(selected) >= 3:
                    break
        return selected

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

    def _memory_payload(self, state: EngineRuntimeState) -> dict:
        return {
            "scope": state.memory.scope,
            "query_history": state.memory.query_history[-20:],
            "visited_urls": state.memory.visited_urls[-30:],
            "searched_company_names": state.memory.searched_company_names[-25:],
            "registered_lead_names": state.memory.registered_lead_names[-15:],
            "consecutive_hard_miss_runs": state.memory.consecutive_hard_miss_runs,
        }

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
