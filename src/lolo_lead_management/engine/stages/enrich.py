from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import (
    ResearchQueryPlan,
    ResearchTraceEntry,
    SearchResultTrace,
    SourcePassResult,
    SourceQueryTrace,
    SourceStageTrace,
)
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
        self.last_trace: SourceStageTrace | None = None

    def execute(self, state: EngineRuntimeState) -> SourcePassResult:
        dossier = state.current_dossier
        if dossier is None or dossier.company is None:
            self.last_trace = SourceStageTrace(mode="enrich", notes=["no_dossier_to_enrich"])
            return SourcePassResult(sourcing_status=SourcingStatus.NO_CANDIDATE, notes=["no_dossier_to_enrich"], source_trace=self.last_trace)

        missing_fields = collect_missing_fields_for_enrichment(dossier, state.run.request)
        fallback_plan = build_research_query_plan(
            state.run.request,
            state.run.applied_relaxation_stage,
            anchor_company=dossier.company.name,
            missing_fields=missing_fields,
            mode="enrich",
        )
        plan_attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.ENRICH],
            payload={
                "request": state.run.request.model_dump(mode="json"),
                "current_dossier": self._compact_dossier_payload(dossier.model_dump(mode="json")),
                "memory": {
                    "scope": state.memory.scope,
                    "query_history": state.memory.query_history[-20:],
                    "visited_urls_run_scoped": state.visited_urls_run_scoped[-30:],
                    "blocked_official_domains": state.memory.blocked_official_domains[-15:],
                    "searched_company_names": state.memory.searched_company_names[-25:],
                    "registered_lead_names": state.memory.registered_lead_names[-15:],
                    "consecutive_hard_miss_runs": state.memory.consecutive_hard_miss_runs,
                },
                "missing_fields": missing_fields,
                "fallback_plan": fallback_plan.model_dump(mode="json"),
            },
            output_model=ResearchQueryPlan,
        )
        generated_plan = plan_attempt.parsed if isinstance(plan_attempt.parsed, ResearchQueryPlan) else None
        sanitized_plan = sanitize_research_query_plan(
            generated_plan,
            fallback=fallback_plan,
            request=state.run.request,
            anchor_company=dossier.company.name,
        )
        plan = merge_research_query_plans(sanitized_plan, fallback_plan)
        selected_queries = choose_queries(plan, state.memory.query_history + ([state.current_query] if state.current_query else []), limit=3)
        stage_trace = SourceStageTrace(
            mode="enrich",
            llm_plan_status="llm_disabled" if plan_attempt.error == "llm_disabled" else "llm_error" if plan_attempt.error else "ok" if generated_plan is not None else "fallback_only",
            llm_plan_error=plan_attempt.error,
            llm_raw_plan=plan_attempt.raw if isinstance(plan_attempt.raw, dict) else None,
            fallback_query_count=len(fallback_plan.planned_queries),
            llm_query_count=len(generated_plan.planned_queries) if generated_plan is not None else 0,
            merged_query_count=len(plan.planned_queries),
            selected_query_count=len(selected_queries),
            query_history=state.memory.query_history[-20:],
            selected_queries=[item.query for item in selected_queries],
            anchored_company=dossier.company.name,
            notes=[f"missing_fields={','.join(missing_fields)}"] if missing_fields else [],
        )
        if not selected_queries:
            stage_trace.notes.append("no_unused_queries_left_for_enrichment")
            self.last_trace = stage_trace
            return SourcePassResult(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_plan=plan,
                notes=["no_unused_queries_left_for_enrichment"],
                source_trace=stage_trace,
            )

        documents = []
        research_trace: list[ResearchTraceEntry] = []
        for query in selected_queries:
            if not state.run.budget.can_search():
                stage_trace.notes.append("search_call_budget_exhausted_before_enrich_query")
                break
            state.run.budget.search_calls_used += 1
            previously_visited = set(state.visited_urls_run_scoped)
            results = self._search_port.web_search(query, max_results=self._max_results)
            filtered = [item for item in results if item.url not in previously_visited]
            enriched, fetched_urls, empty_fetch_urls = self._enrich_missing_content(filtered, query.query)
            selected = merge_documents(enriched[: self._max_results])
            state.visited_urls_run_scoped = list(dict.fromkeys([*state.visited_urls_run_scoped, *[item.url for item in selected]]))
            research_trace.append(
                ResearchTraceEntry(
                    query_planned=query.query,
                    query_executed=query.query,
                    research_phase=query.research_phase,
                    objective=query.objective,
                    candidate_company_name=query.candidate_company_name,
                    source_tier_target=query.source_tier_target,
                    expected_field=query.expected_field,
                    documents_considered=len(filtered),
                    documents_selected=len(selected),
                    selected_urls=[item.url for item in selected],
                )
            )
            stage_trace.query_traces.append(
                SourceQueryTrace(
                    query=query.query,
                    objective=query.objective,
                    research_phase=query.research_phase,
                    source_role=query.source_role,
                    candidate_company_name=query.candidate_company_name,
                    source_tier_target=query.source_tier_target,
                    expected_field=query.expected_field,
                    preferred_domains=query.preferred_domains,
                    excluded_domains=query.excluded_domains,
                    max_results=self._max_results,
                    raw_result_count=len(results),
                    filtered_result_count=len(filtered),
                    enriched_result_count=len(enriched),
                    selected_result_count=len(selected),
                    selected_urls=[item.url for item in selected],
                    fetched_urls=fetched_urls,
                    empty_fetch_urls=empty_fetch_urls,
                    results=[
                        SearchResultTrace(
                            url=item.url,
                            domain=getattr(item, "domain", None),
                            title=item.title,
                            source_type=item.source_type,
                            search_score=item.search_score,
                            kept=item.url not in previously_visited,
                            rejection_reasons=[] if item.url not in previously_visited else ["visited_url_in_run"],
                        )
                        for item in results
                    ],
                )
            )
            documents.extend(selected)
        extracted_documents = []
        if state.run.budget.can_search():
            extracted_documents, extract_candidate_urls, extract_error = self._extract_anchor_documents(merge_documents(documents), dossier.company.name)
        else:
            extract_candidate_urls, extract_error = [], "search_call_budget_exhausted"
            stage_trace.notes.append("search_call_budget_exhausted_before_enrich_extract")
        if extracted_documents:
            state.run.budget.search_calls_used += 1
            documents.extend(extracted_documents)
            research_trace.append(
                ResearchTraceEntry(
                    query_planned=f'"{dossier.company.name}" extract anchored pages',
                    query_executed=f'"{dossier.company.name}" extract anchored pages',
                    research_phase="evidence_closing",
                    objective="Extract richer content from the most relevant anchored pages.",
                    candidate_company_name=dossier.company.name,
                    source_tier_target="tier_a",
                    expected_field="multi",
                    documents_considered=len(extracted_documents),
                    documents_selected=len(extracted_documents),
                    selected_urls=[item.url for item in extracted_documents],
                )
            )
        stage_trace.extract_candidate_urls = extract_candidate_urls
        stage_trace.extracted_urls = [item.url for item in extracted_documents]
        stage_trace.extract_error = extract_error
        stage_trace.focused_document_urls = [item.url for item in merge_documents(documents)]
        stage_trace.selected_documents = []
        stage_trace.notes.append(f"enrichment_queries_executed={len(selected_queries)}")
        self.last_trace = stage_trace
        return SourcePassResult(
            sourcing_status=SourcingStatus.FOUND if documents else SourcingStatus.NO_CANDIDATE,
            query_plan=plan,
            executed_queries=selected_queries,
            documents=merge_documents(documents),
            anchored_company_name=dossier.company.name,
            research_trace=research_trace,
            notes=[f"enrichment_queries_executed={len(selected_queries)}"],
            source_trace=stage_trace,
        )

    def _enrich_missing_content(self, documents, query_text: str):
        pending = [item for item in documents if not item.raw_content]
        fetched: dict[str, str] = {}
        fetched_urls: list[str] = []
        empty_fetch_urls: list[str] = []
        if pending:
            with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
                pairs = list(executor.map(lambda entry: (entry.url, self._safe_fetch_page(entry.url)), pending))
            fetched = {url: text for url, text in pairs}
            fetched_urls = [url for url, text in pairs if text]
            empty_fetch_urls = [url for url, text in pairs if not text]
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
        ], fetched_urls, empty_fetch_urls

    def _extract_anchor_documents(self, documents, anchor_company: str):
        candidate_urls = []
        for item in documents:
            if item.is_publisher_like:
                continue
            text = f"{item.title}\n{item.snippet}\n{item.raw_content}".lower()
            if anchor_company.lower() not in text and not item.is_company_controlled_source:
                continue
            candidate_urls.append(item.url)
            if len(candidate_urls) >= 3:
                break
        if not candidate_urls:
            return [], [], None
        try:
            extracted = self._search_port.extract_pages(candidate_urls, extract_depth="advanced")
        except Exception as exc:
            return [], candidate_urls, str(exc)
        return [
            enrich_document_metadata(
                item.model_copy(
                    update={
                        "query_planned": f'"{anchor_company}" extract anchored pages',
                        "query_executed": f'"{anchor_company}" extract anchored pages',
                        "research_phase": "evidence_closing",
                        "objective": "Extract richer content from the most relevant anchored pages.",
                        "company_anchor": anchor_company,
                    }
                ),
                anchor_company=anchor_company,
            )
            for item in extracted
        ], candidate_urls, None

    def _safe_fetch_page(self, url: str) -> str:
        try:
            return self._search_port.fetch_page(url)
        except Exception:
            return ""

    def _compact_dossier_payload(self, payload: dict) -> dict:
        compact = dict(payload)
        compact["website_resolution"] = payload.get("website_resolution")
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
