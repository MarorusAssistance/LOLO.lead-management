from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from lolo_lead_management.domain.enums import SourceQuality, StageName, SourcingStatus
from lolo_lead_management.domain.models import (
    ResearchQuery,
    ResearchQueryPlan,
    ResearchTraceEntry,
    SearchResultTrace,
    SourceAnchorCandidate,
    SourceDocumentSelectionTrace,
    SourcePassResult,
    SourceQueryTrace,
    SourceStageTrace,
    SourceTraceDocumentSnapshot,
    WebsiteCandidateHint,
)
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    anchor_name_looks_corrupted,
    build_research_query_plan,
    canonicalize_website,
    candidate_company_names_from_document,
    choose_queries,
    classify_person_lead_source,
    company_name_matches_anchor,
    company_name_matches_anchor_strict,
    dedupe_preserve_order,
    directory_title_company_name,
    derive_anchor_query_name,
    derive_anchor_legal_name,
    derive_brand_aliases,
    document_matches_anchor_strong,
    document_can_seed_website_candidate,
    document_explicitly_supports_persona_candidate,
    document_is_multi_entity_listing,
    domain_from_url,
    domain_has_public_suffix,
    domain_is_directory,
    domain_is_publisher_like,
    domain_is_unofficial_website_host,
    enrich_document_metadata,
    extract_employee_size_hint,
    extract_employee_estimate_from_text,
    extracted_official_website_from_document,
    is_spain_secondary_signal_only_domain,
    is_spanish_category_page,
    domain_root_name,
    merge_research_query_plans,
    merge_documents,
    normalize_text,
    parse_candidate_from_text,
    request_scoped_company_exclusions,
    resolve_person_signal,
    sanitize_research_query_plan,
    select_anchor_company,
    text_has_spanish_non_operational_signal,
    title_company_name,
)
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.search import SearchPort


DISCOVERY_FETCH_TRUSTED_DOMAINS = {
    "empresite.eleconomista.es",
    "infoempresa.com",
    "datoscif.es",
    "iberinform.es",
    "einforma.com",
    "axesor.es",
}

SPAIN_DISCOVERY_DIRECTORY_LADDER = [
    "empresite.eleconomista.es",
    "infoempresa.com",
    "datoscif.es",
    "censo.camara.es",
]

SPANISH_DIRECTORY_FETCH_TOKENS = {
    "empresa",
    "cif",
    "razón social",
    "sitio web",
    "página web",
    "empleados",
    "plantilla",
    "administradores",
    "directivos",
}


class SourceStage:
    def __init__(self, *, search_port: SearchPort, agent_executor: StageAgentExecutor, max_results: int) -> None:
        self._search_port = search_port
        self._agent_executor = agent_executor
        self._max_results = max_results
        self.last_trace: SourceStageTrace | None = None

    def execute(self, state: EngineRuntimeState) -> SourcePassResult:
        if state.focus_company_locked and state.current_focus_company_resolution and state.current_focus_company_resolution.selected_company:
            return self._execute_focus_locked_retrieval(state)
        return self._execute_discovery_batch(state)

    def _execute_discovery_batch(self, state: EngineRuntimeState) -> SourcePassResult:
        request = state.run.request
        fallback_plan = build_research_query_plan(
            request,
            state.run.applied_relaxation_stage,
            mode="source_discovery",
        )
        plan_input = {
            "request": request.model_dump(mode="json"),
            "memory": self._memory_payload(state),
            "relaxation_stage": state.run.applied_relaxation_stage,
            "fallback_plan": fallback_plan.model_dump(mode="json"),
        }
        plan_attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.SOURCE],
            payload=plan_input,
            output_model=ResearchQueryPlan,
        )
        generated_plan = plan_attempt.parsed if isinstance(plan_attempt.parsed, ResearchQueryPlan) else None

        sanitized_plan = sanitize_research_query_plan(generated_plan, fallback=fallback_plan, request=request)
        plan = merge_research_query_plans(sanitized_plan, fallback_plan)
        query_history = state.memory.query_history + [item.query for item in state.pending_discovery_queries]
        selected_queries, discovery_directory_selected, discovery_ladder_position = self._choose_discovery_query(
            state,
            plan,
            query_history,
        )
        excluded_companies = self._excluded_company_names(state)
        request_scoped_exclusions = self._request_scoped_company_exclusions(state)
        stage_trace = SourceStageTrace(
            mode="source",
            pass_kind="discovery_batch",
            batch_traces=[],
            discovery_directory_selected=discovery_directory_selected,
            discovery_directories_consumed_in_run=state.discovery_directories_consumed_in_run[:],
            discovery_ladder_position=discovery_ladder_position,
            llm_plan_status=self._llm_plan_status(plan_attempt.error, generated_plan),
            llm_plan_error=plan_attempt.error,
            llm_plan_input=plan_input,
            llm_raw_plan=plan_attempt.raw if isinstance(plan_attempt.raw, dict) else None,
            sanitized_query_plan=sanitized_plan.model_dump(mode="json"),
            merged_query_plan=plan.model_dump(mode="json"),
            fallback_query_count=len(fallback_plan.planned_queries),
            llm_query_count=len(generated_plan.planned_queries) if generated_plan is not None else 0,
            merged_query_count=len(plan.planned_queries),
            selected_query_count=len(selected_queries),
            query_history=query_history[-20:],
            excluded_companies=excluded_companies,
            request_scoped_company_exclusions=request_scoped_exclusions,
            selected_queries=[item.query for item in selected_queries],
            notes=[],
        )
        if not selected_queries:
            stage_trace.notes.append("no_unused_queries_left")
            self.last_trace = stage_trace
            return SourcePassResult(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_plan=plan,
                notes=["no_unused_queries_left"],
                source_trace=stage_trace,
            )

        documents = []
        executed_queries = []
        research_trace: list[ResearchTraceEntry] = []
        query_notes: list[str] = []
        for query in selected_queries:
            if not state.run.budget.can_search():
                stage_trace.notes.append("search_call_budget_exhausted_before_query_execution")
                break
            selected, trace_entry, query_trace, error_note = self._execute_query(state, query)
            research_trace.append(trace_entry)
            stage_trace.query_traces.append(query_trace)
            documents.extend(selected)
            executed_queries.append(query)
            if error_note:
                query_notes.append(error_note)

        documents = merge_documents(documents)
        documents, excluded_terminal_company_urls = self._exclude_terminal_discovery_documents(
            documents,
            excluded_companies=excluded_companies,
        )
        stage_trace.excluded_terminal_company_documents = excluded_terminal_company_urls
        stage_trace.anchor_candidates = self._anchor_candidates(documents, excluded_companies)
        stage_trace.cross_company_rejections = self._cross_company_rejections(stage_trace.query_traces)
        stage_trace.documents_passed_to_assembler = self._document_snapshots(documents)
        stage_trace.batch_traces = [
            {
                "discovery_directory_selected": stage_trace.discovery_directory_selected,
                "discovery_directories_consumed_in_run": stage_trace.discovery_directories_consumed_in_run,
                "discovery_ladder_position": stage_trace.discovery_ladder_position,
                "selected_queries": stage_trace.selected_queries,
                "llm_plan_input": stage_trace.llm_plan_input,
                "llm_raw_plan": stage_trace.llm_raw_plan,
                "sanitized_query_plan": stage_trace.sanitized_query_plan,
                "merged_query_plan": stage_trace.merged_query_plan,
                "query_traces": [item.model_dump(mode="json") for item in stage_trace.query_traces],
                "documents_passed_to_assembler": [item.model_dump(mode="json") for item in stage_trace.documents_passed_to_assembler],
                "excluded_terminal_company_documents": stage_trace.excluded_terminal_company_documents,
                "notes": stage_trace.notes,
            }
        ]
        self._mark_run_scoped_visited(state, documents)
        stage_trace.notes.extend([f"queries_executed={len(executed_queries)}", *query_notes])
        if state.discovery_ladder_exhausted_in_run:
            stage_trace.notes.append("discovery_ladder_exhausted_in_run")
        self.last_trace = stage_trace
        return SourcePassResult(
            sourcing_status=SourcingStatus.FOUND if documents else SourcingStatus.NO_CANDIDATE,
            query_plan=plan,
            executed_queries=executed_queries,
            documents=documents,
            research_trace=research_trace,
            notes=stage_trace.notes,
            source_trace=stage_trace,
        )

    def _execute_focus_locked_retrieval(self, state: EngineRuntimeState) -> SourcePassResult:
        request = state.run.request
        focus = state.current_focus_company_resolution
        assert focus is not None and focus.selected_company is not None
        query_history = state.memory.query_history + ([state.current_query] if state.current_query else [])
        seed_result = state.current_source_result
        seed_documents = self._focus_documents(seed_result.documents if seed_result else [], focus.selected_company)
        seed_queries = seed_result.executed_queries[:] if seed_result else []
        seed_trace = seed_result.research_trace[:] if seed_result else []
        fallback_plan = build_research_query_plan(
            request,
            state.run.applied_relaxation_stage,
            anchor_company=focus.query_name or focus.selected_company,
            mode="source_focus_locked",
        )
        plan_input = {
            "request": request.model_dump(mode="json"),
            "focus_company": focus.model_dump(mode="json"),
            "memory": self._memory_payload(state),
            "relaxation_stage": state.run.applied_relaxation_stage,
            "fallback_plan": fallback_plan.model_dump(mode="json"),
        }
        plan_attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.SOURCE],
            payload=plan_input,
            output_model=ResearchQueryPlan,
        )
        generated_plan = plan_attempt.parsed if isinstance(plan_attempt.parsed, ResearchQueryPlan) else None
        sanitized_plan = sanitize_research_query_plan(
            generated_plan,
            fallback=fallback_plan,
            request=request,
            anchor_company=focus.query_name or focus.selected_company,
        )
        plan = merge_research_query_plans(sanitized_plan, fallback_plan)
        stage_trace = SourceStageTrace(
            mode="source",
            pass_kind="focus_locked_retrieval",
            batch_traces=[],
            llm_plan_status=self._llm_plan_status(plan_attempt.error, generated_plan),
            llm_plan_error=plan_attempt.error,
            llm_plan_input=plan_input,
            llm_raw_plan=plan_attempt.raw if isinstance(plan_attempt.raw, dict) else None,
            sanitized_query_plan=sanitized_plan.model_dump(mode="json"),
            merged_query_plan=plan.model_dump(mode="json"),
            fallback_query_count=len(fallback_plan.planned_queries),
            llm_query_count=len(generated_plan.planned_queries) if generated_plan is not None else 0,
            merged_query_count=len(plan.planned_queries),
            selected_query_count=0,
            query_history=query_history[-20:],
            excluded_companies=self._excluded_company_names(state),
            anchored_company=focus.selected_company,
            anchor_raw_name=focus.legal_name or focus.selected_company,
            anchor_query_name=focus.query_name or focus.selected_company,
            anchor_brand_aliases=focus.brand_aliases,
            notes=[],
        )
        if anchor_name_looks_corrupted(stage_trace.anchor_raw_name):
            stage_trace.notes.append("anchor_name_corrupted")

        documents = merge_documents(seed_documents)
        executed_queries = seed_queries[:]
        research_trace: list[ResearchTraceEntry] = seed_trace[:]
        query_notes: list[str] = []

        initial_queries = self._choose_focus_locked_queries(
            plan,
            query_history + [item.query for item in executed_queries],
            current_documents=documents,
            anchored_company=focus.selected_company,
        )
        stage_trace.selected_query_count = len(initial_queries)
        stage_trace.selected_queries = [item.query for item in initial_queries]
        for query in initial_queries:
            if not state.run.budget.can_search():
                stage_trace.notes.append("search_call_budget_exhausted_before_focus_locked_query")
                break
            selected, trace_entry, query_trace, error_note = self._execute_query(state, query)
            research_trace.append(trace_entry)
            stage_trace.query_traces.append(query_trace)
            documents.extend(selected)
            executed_queries.append(query)
            if error_note:
                query_notes.append(error_note)

        documents = self._focus_documents(merge_documents(documents), focus.selected_company)
        official_domain = self._official_domain_for_company(documents, focus.selected_company)
        website_candidates = self._website_candidates_for_company(documents, focus.selected_company)
        stage_trace.domain_validation_strategy = "domain_based" if official_domain else "name_based"
        size_hint_value, size_hint_type = self._size_hint_for_documents(documents)
        stage_trace.size_hint_value = size_hint_value
        stage_trace.size_hint_type = size_hint_type
        stage_trace.operational_status_hint = self._operational_status_hint(documents)

        if self._size_mismatch_lt50(request, size_hint_value):
            stage_trace.candidate_branch_stop_reason = "size_mismatch_lt50"

        if stage_trace.candidate_branch_stop_reason is None:
            gap_queries = self._choose_gap_queries(
                plan,
                query_history + [item.query for item in executed_queries],
                current_documents=documents,
                request=request,
                size_hint_value=size_hint_value,
            )
            for query in gap_queries:
                if not state.run.budget.can_search():
                    stage_trace.notes.append("search_call_budget_exhausted_before_gap_followup")
                    break
                selected, trace_entry, query_trace, error_note = self._execute_query(state, query)
                research_trace.append(trace_entry)
                stage_trace.query_traces.append(query_trace)
                documents.extend(selected)
                executed_queries.append(query)
                if error_note:
                    query_notes.append(error_note)
            documents = self._focus_documents(merge_documents(documents), focus.selected_company)
            website_candidates = self._website_candidates_for_company(documents, focus.selected_company)
            official_domain = self._official_domain_for_company(documents, focus.selected_company)
            size_hint_value, size_hint_type = self._size_hint_for_documents(documents)
            stage_trace.size_hint_value = size_hint_value
            stage_trace.size_hint_type = size_hint_type

        person_supported, role_supported = self._has_person_role_support(documents, focus.selected_company)
        should_attempt_website = not (size_hint_value is not None and person_supported and role_supported)

        if should_attempt_website and not official_domain and not website_candidates:
            extra_website_queries = self._additional_website_candidate_queries(
                plan,
                query_history + [item.query for item in executed_queries],
                current_documents=documents,
                already_selected=initial_queries,
            )
            for query in extra_website_queries:
                if not state.run.budget.can_search():
                    stage_trace.notes.append("search_call_budget_exhausted_before_second_website_attempt")
                    break
                selected, trace_entry, query_trace, error_note = self._execute_query(state, query)
                research_trace.append(trace_entry)
                stage_trace.query_traces.append(query_trace)
                documents.extend(selected)
                executed_queries.append(query)
                if error_note:
                    query_notes.append(error_note)
            if extra_website_queries:
                stage_trace.selected_queries = [*stage_trace.selected_queries, *[item.query for item in extra_website_queries]]
                stage_trace.selected_query_count = len(stage_trace.selected_queries)
                documents = self._focus_documents(merge_documents(documents), focus.selected_company)
                official_domain = self._official_domain_for_company(documents, focus.selected_company)
                website_candidates = self._website_candidates_for_company(documents, focus.selected_company)

        if should_attempt_website and official_domain and not self._has_domain_validation_support(documents, official_domain):
            domain_queries = self._official_domain_queries(
                focus.query_name or focus.selected_company,
                official_domain,
                query_history + [item.query for item in executed_queries],
            )
            for query in domain_queries:
                if not state.run.budget.can_search():
                    stage_trace.notes.append("search_call_budget_exhausted_before_domain_validation")
                    break
                selected, trace_entry, query_trace, error_note = self._execute_query(state, query)
                research_trace.append(trace_entry)
                stage_trace.query_traces.append(query_trace)
                documents.extend(selected)
                executed_queries.append(query)
                if error_note:
                    query_notes.append(error_note)
            documents = self._focus_documents(merge_documents(documents), focus.selected_company)

        official_domain = self._official_domain_for_company(documents, focus.selected_company)
        website_candidates = self._website_candidates_for_company(documents, focus.selected_company)
        if stage_trace.candidate_branch_stop_reason is None and should_attempt_website:
            if not official_domain and website_candidates:
                stage_trace.candidate_branch_stop_reason = "website_only_directory_support"
            elif not official_domain and not website_candidates:
                stage_trace.candidate_branch_stop_reason = "no_candidate_website"
            elif official_domain and not self._has_domain_validation_support(documents, official_domain):
                stage_trace.candidate_branch_stop_reason = "zero_results_on_domain_validation"

        stage_trace.focused_document_urls = [item.url for item in documents]
        stage_trace.official_domain = official_domain
        stage_trace.website_candidates = website_candidates
        stage_trace.cross_company_rejections = self._cross_company_rejections(stage_trace.query_traces)
        stage_trace.anchor_confidence = self._anchor_confidence(
            documents,
            anchored_company=focus.selected_company,
            official_domain=official_domain,
        )

        if not documents:
            stage_trace.notes.extend([*query_notes, "no_documents_selected"])
            self.last_trace = stage_trace
            return SourcePassResult(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_plan=plan,
                executed_queries=executed_queries,
                anchored_company_name=focus.selected_company,
                research_trace=research_trace,
                notes=stage_trace.notes,
                source_trace=stage_trace,
            )

        selected_documents, selection_notes = self._select_documents_for_assembler(
            documents,
            anchored_company=focus.selected_company,
            official_domain=official_domain,
            research_trace=research_trace,
        )
        if not selected_documents:
            selected_documents = documents[:4]
            selection_notes = [*selection_notes, "assembler_selection_fallback_used"]
        filtered_trace = self._filter_research_trace(research_trace, selected_documents)
        stage_trace.selected_documents = self._selected_document_trace(selected_documents, research_trace)
        stage_trace.documents_passed_to_assembler = self._document_snapshots(selected_documents)
        notes = [f"queries_executed={len(executed_queries)}", f"anchor_confidence={stage_trace.anchor_confidence}", *query_notes, *selection_notes]
        if website_candidates:
            notes.append(f"website_candidate={website_candidates[0].candidate_website}")
        if stage_trace.candidate_branch_stop_reason:
            notes.append(stage_trace.candidate_branch_stop_reason)
        stage_trace.notes.extend(dedupe_preserve_order(notes))
        self._mark_run_scoped_visited(state, selected_documents)
        self.last_trace = stage_trace
        return SourcePassResult(
            sourcing_status=SourcingStatus.FOUND,
            query_plan=plan,
            executed_queries=executed_queries,
            documents=selected_documents,
            website_candidates=website_candidates,
            anchored_company_name=focus.selected_company,
            research_trace=filtered_trace or research_trace,
            notes=stage_trace.notes,
            source_trace=stage_trace,
        )

    def _llm_plan_status(self, error: str | None, generated_plan: ResearchQueryPlan | None) -> str:
        if error == "llm_disabled":
            return "llm_disabled"
        if error:
            return "llm_error"
        if generated_plan is None:
            return "fallback_only"
        return "ok"

    def _execute_query(self, state: EngineRuntimeState, query: ResearchQuery):
        state.run.budget.search_calls_used += 1
        raw_results = []
        filtered = []
        selected = []
        enriched = []
        fetched_urls: list[str] = []
        empty_fetch_urls: list[str] = []
        enrichment_details: dict[str, dict] = {}
        error_note: str | None = None
        error: str | None = None
        try:
            raw_results = self._search_port.web_search(query, max_results=self._max_results)
            filtered = []
            result_traces: list[SearchResultTrace] = []
            for item in raw_results:
                rejection_reasons = self._result_rejection_reasons(item, query, state)
                kept = len(rejection_reasons) == 0
                if kept:
                    filtered.append(item)
                reused_in_run = self._can_reuse_anchor_directory_result_in_run(item, query, state)
                result_traces.append(
                    SearchResultTrace(
                        url=item.url,
                        domain=domain_from_url(item.url),
                        title=item.title,
                        source_type=item.source_type,
                        search_score=item.search_score,
                        kept=kept,
                        rejection_reasons=rejection_reasons,
                        notes=[
                            f"company_anchor={item.company_anchor}"
                            for _ in [1]
                            if item.company_anchor
                        ]
                        + (["reused_anchor_directory_page_in_run"] if reused_in_run else []),
                    )
                )
            enriched, fetched_urls, empty_fetch_urls, enrichment_details = self._enrich_missing_content(filtered, query)
            selected = merge_documents(enriched[: self._max_results])
        except Exception as exc:
            error = str(exc)
            error_note = f"search_query_failed={query.query}: {exc}"
            result_traces = []
        trace_entry = ResearchTraceEntry(
            query_planned=query.query,
            query_executed=query.query,
            research_phase=query.research_phase,
            objective=query.objective,
            source_role=query.source_role,
            candidate_company_name=query.candidate_company_name,
            source_tier_target=query.source_tier_target,
            expected_field=query.expected_field,
            documents_considered=len(filtered),
            documents_selected=len(selected),
            selected_urls=[item.url for item in selected],
        )
        query_trace = SourceQueryTrace(
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
            raw_result_count=len(raw_results),
            filtered_result_count=len(filtered),
            enriched_result_count=len(filtered),
            selected_result_count=len(selected),
            selected_urls=[item.url for item in selected],
            fetched_urls=fetched_urls,
            empty_fetch_urls=empty_fetch_urls,
            results=result_traces,
            raw_results_before_filter=self._document_snapshots(raw_results),
            documents_after_enrichment=self._document_snapshots(enriched, enrichment_details),
            documents_selected_for_pass=self._document_snapshots(selected, enrichment_details),
            error=error,
            notes=self._query_trace_notes(query, filtered, fetched_urls, empty_fetch_urls, enrichment_details),
        )
        self._mark_run_scoped_visited(state, selected)
        return selected, trace_entry, query_trace, error_note

    def _result_rejection_reasons(self, item, query: ResearchQuery, state: EngineRuntimeState) -> list[str]:
        reasons: list[str] = []
        if item.url in state.visited_urls_run_scoped and not self._can_reuse_anchor_directory_result_in_run(item, query, state):
            reasons.append("visited_url_in_run")
        if self._is_globally_blocked_official_domain(domain_from_url(item.url), state):
            reasons.append("blocked_official_domain")
        if not self._is_usable_search_result(item.url):
            reasons.append("blocked_host")
        reasons.extend(self._query_policy_rejection_reasons(item, query, request=state.run.request))
        return reasons

    def _query_trace_notes(
        self,
        query: ResearchQuery,
        filtered,
        fetched_urls: list[str],
        empty_fetch_urls: list[str],
        enrichment_details: dict[str, dict],
    ) -> list[str]:
        notes: list[str] = []
        if query.preferred_domains:
            notes.append(f"preferred_domains={','.join(query.preferred_domains[:4])}")
        if any(
            detail.get("enrichment_strategy_used") == "search_raw"
            for detail in enrichment_details.values()
        ):
            notes.append("raw_content_from_search")
        if any(
            detail.get("extract_attempted")
            or detail.get("enrichment_strategy_used") in {"extract_pages", "extract_then_fetch"}
            for detail in enrichment_details.values()
        ):
            notes.append("extract_pages_used")
        if any(detail.get("fetch_attempted") for detail in enrichment_details.values()):
            notes.append("fetch_page_fallback_used")
        if any(
            detail.get("raw_content_len_after", 0) < 300
            and detail.get("enrichment_strategy_used") not in {None, "none"}
            for detail in enrichment_details.values()
        ):
            notes.append("content_still_poor_after_enrichment")
        if (
            filtered
            and not fetched_urls
            and not any(detail.get("extract_attempted") for detail in enrichment_details.values())
            and query.research_phase in {"field_acquisition", "evidence_closing"}
        ):
            notes.append("no_additional_raw_content_fetched")
        if empty_fetch_urls:
            notes.append(f"empty_fetches={len(empty_fetch_urls)}")
        return dedupe_preserve_order(notes)

    def _anchor_candidates(self, documents, excluded_companies: list[str]) -> list[SourceAnchorCandidate]:
        scores: dict[str, dict] = {}
        excluded = {normalize_text(item) for item in excluded_companies}
        for item in documents:
            for candidate in candidate_company_names_from_document(item):
                normalized = normalize_text(candidate)
                if not candidate or normalized in excluded:
                    continue
                entry = scores.setdefault(candidate, {"support_count": 0, "evidence_urls": [], "notes": []})
                entry["support_count"] += 1
                entry["evidence_urls"] = dedupe_preserve_order([*entry["evidence_urls"], item.url])
                if item.is_company_controlled_source:
                    entry["notes"] = dedupe_preserve_order([*entry["notes"], "company_controlled_source"])
                if item.source_tier == "tier_a":
                    entry["notes"] = dedupe_preserve_order([*entry["notes"], "tier_a_support"])
        ranked = sorted(scores.items(), key=lambda pair: (pair[1]["support_count"], len(pair[1]["evidence_urls"])), reverse=True)
        return [
            SourceAnchorCandidate(
                company_name=company_name,
                support_count=data["support_count"],
                evidence_urls=data["evidence_urls"][:4],
                notes=data["notes"][:4],
            )
            for company_name, data in ranked[:5]
        ]

    def _choose_discovery_query(
        self,
        state: EngineRuntimeState,
        plan: ResearchQueryPlan,
        query_history: list[str],
    ) -> tuple[list[ResearchQuery], str | None, int | None]:
        available = [
            query
            for query in choose_queries(plan, query_history, limit=20)
            if query.research_phase == "company_discovery"
        ]
        if not available:
            state.discovery_ladder_exhausted_in_run = False
            return [], None, None
        if not self._uses_spanish_discovery_ladder(available):
            state.discovery_ladder_exhausted_in_run = False
            return [available[0]], self._discovery_directory_for_query(available[0]), None

        consumed = {normalize_text(item) for item in state.discovery_directories_consumed_in_run}
        for index, domain in enumerate(SPAIN_DISCOVERY_DIRECTORY_LADDER, start=1):
            if normalize_text(domain) in consumed:
                continue
            for query in available:
                if self._discovery_directory_for_query(query) != domain:
                    continue
                state.discovery_directories_consumed_in_run = dedupe_preserve_order(
                    [*state.discovery_directories_consumed_in_run, domain]
                )
                state.discovery_ladder_exhausted_in_run = len(state.discovery_directories_consumed_in_run) >= len(
                    SPAIN_DISCOVERY_DIRECTORY_LADDER
                )
                return [query], domain, index
        state.discovery_ladder_exhausted_in_run = True
        return [], None, len(state.discovery_directories_consumed_in_run) or None

    def _selected_document_trace(self, documents, research_trace: list[ResearchTraceEntry]) -> list[SourceDocumentSelectionTrace]:
        trace_by_url = self._trace_by_url(research_trace)
        selections: list[SourceDocumentSelectionTrace] = []
        for item in documents:
            trace = trace_by_url.get(item.url)
            selections.append(
                SourceDocumentSelectionTrace(
                    url=item.url,
                    domain=domain_from_url(item.url),
                    selected_for_field=item.selected_for_field,
                    why_selected=item.why_selected,
                    source_tier=item.source_tier,
                    is_company_controlled_source=item.is_company_controlled_source,
                    research_phase=trace.research_phase if trace else None,
                    source_role=trace.source_role if trace else None,
                    expected_field=trace.expected_field if trace else None,
                )
            )
        return selections

    def _excluded_company_names(self, state: EngineRuntimeState) -> list[str]:
        run_company_names = [item.company_name for item in state.run.accepted_leads]
        if self._ignore_persistent_search_memory(state):
            return dedupe_preserve_order(run_company_names)
        request_scoped_exclusions = self._request_scoped_company_exclusions(state)
        return dedupe_preserve_order([*state.memory.searched_company_names, *run_company_names, *request_scoped_exclusions])

    def _choose_focus_locked_queries(self, plan: ResearchQueryPlan, query_history: list[str], *, current_documents, anchored_company: str | None):
        selected: list[ResearchQuery] = []
        seen_queries: set[str] = set()
        size_hint_value, _ = self._size_hint_for_documents(current_documents)
        person_supported, role_supported = self._has_person_role_support(current_documents, anchored_company)
        desired_fields = ["company_name"]
        if size_hint_value is None:
            desired_fields.append("employee_estimate")
        elif not person_supported:
            desired_fields.append("person_name")
        elif not role_supported:
            desired_fields.append("role_title")
        else:
            desired_fields.append("fit_signals")
        for desired_field in desired_fields:
            for query in choose_queries(plan, query_history + [item.query for item in selected], limit=20):
                if query.query in seen_queries or query.expected_field != desired_field:
                    continue
                if query.stop_if_resolved and self._query_already_resolved(query, current_documents):
                    continue
                selected.append(query)
                seen_queries.add(query.query)
                if len(selected) >= 2:
                    return selected
                break
        return selected

    def _additional_website_candidate_queries(
        self,
        plan: ResearchQueryPlan,
        query_history: list[str],
        *,
        current_documents,
        already_selected: list[ResearchQuery],
    ) -> list[ResearchQuery]:
        seen_queries = {item.query for item in already_selected}
        extra: list[ResearchQuery] = []
        website_attempts_used = sum(1 for item in already_selected if item.expected_field == "website")
        remaining_attempts = max(0, 2 - website_attempts_used)
        for query in choose_queries(plan, query_history, limit=20):
            if query.query in seen_queries or query.expected_field != "website":
                continue
            if query.stop_if_resolved and self._query_already_resolved(query, current_documents):
                continue
            extra.append(query)
            if len(extra) >= remaining_attempts:
                break
        return extra

    def _focus_documents(self, documents, anchored_company: str):
        focused = []
        for item in documents:
            if document_matches_anchor_strong(item, anchored_company):
                focused.append(item)
        return merge_documents(focused)

    def _choose_anchor_queries(self, plan: ResearchQueryPlan, query_history: list[str], *, current_documents):
        selected = []
        selected_queries = set()
        priority_groups = [
            lambda item: item.expected_field == "website",
            lambda item: item.expected_field == "company_name",
        ]
        for predicate in priority_groups:
            for query in choose_queries(plan, query_history + [item.query for item in selected], limit=20):
                if query.query in selected_queries:
                    continue
                if query.stop_if_resolved and self._query_already_resolved(query, current_documents):
                    continue
                if predicate(query):
                    selected.append(query)
                    selected_queries.add(query.query)
                    break
            if len(selected) >= 2:
                break
        if len(selected) < 2:
            for query in choose_queries(plan, query_history + [item.query for item in selected], limit=20):
                if query.query in selected_queries:
                    continue
                if query.stop_if_resolved and self._query_already_resolved(query, current_documents):
                    continue
                selected.append(query)
                selected_queries.add(query.query)
                if len(selected) >= 2:
                    break
        return selected

    def _choose_gap_queries(self, plan: ResearchQueryPlan, query_history: list[str], *, current_documents, request, size_hint_value: int | None):
        candidates = []
        selected_queries: set[str] = set()
        size_first = bool((request.constraints.min_company_size is not None or request.constraints.max_company_size is not None) and size_hint_value is None)
        desired_fields = ["employee_estimate", "person_name", "role_title", "website", "fit_signals"] if size_first else ["person_name", "role_title", "employee_estimate", "website", "fit_signals"]
        for desired_field in desired_fields:
            for query in choose_queries(plan, query_history, limit=20):
                if query.query in selected_queries or query.expected_field != desired_field:
                    continue
                if query.stop_if_resolved and self._query_already_resolved(query, current_documents):
                    continue
                candidates.append(query)
                selected_queries.add(query.query)
                if len(candidates) >= 2:
                    return candidates
                break
        return candidates

    def _query_already_resolved(self, query: ResearchQuery, documents) -> bool:
        if query.expected_field == "website":
            anchor_company = next((item.company_anchor for item in documents if item.company_anchor), None)
            return bool(self._website_candidates_for_company(documents, anchor_company)) or any(item.is_company_controlled_source for item in documents)
        if query.expected_field == "company_name" and query.candidate_company_name:
            return any(self._document_is_isolated_for_anchor(item, query.candidate_company_name) for item in documents)
        if query.expected_field == "employee_estimate":
            value, _ = self._size_hint_for_documents(documents)
            return value is not None
        if query.expected_field in {"person_name", "role_title"} and query.candidate_company_name:
            person_supported, role_supported = self._has_person_role_support(documents, query.candidate_company_name)
            return person_supported if query.expected_field == "person_name" else role_supported
        if query.expected_field == "fit_signals":
            return any(item.source_tier == "tier_a" and any(token in f"{item.title} {item.snippet} {item.raw_content}".lower() for token in ["ai", "automation", "agent", "software"]) for item in documents)
        return False

    def _mark_run_scoped_visited(self, state: EngineRuntimeState, documents) -> None:
        state.visited_urls_run_scoped = dedupe_preserve_order([*state.visited_urls_run_scoped, *[item.url for item in documents]])

    def _is_globally_blocked_official_domain(self, domain: str | None, state: EngineRuntimeState) -> bool:
        if self._ignore_persistent_search_memory(state):
            return False
        normalized = normalize_text(domain or "")
        if not normalized:
            return False
        for blocked in state.memory.blocked_official_domains:
            blocked_normalized = normalize_text(blocked)
            if normalized == blocked_normalized or normalized.endswith(f".{blocked_normalized}"):
                return True
        return False

    def _cross_company_rejections(self, query_traces: list[SourceQueryTrace]) -> list[str]:
        rejections: list[str] = []
        for trace in query_traces:
            for result in trace.results:
                if "cross_company_result" in result.rejection_reasons:
                    rejections.append(result.url)
        return dedupe_preserve_order(rejections)

    def _enrich_missing_content(self, documents, query: ResearchQuery):
        pending = [
            item
            for item in documents
            if self._content_is_poor(getattr(item, "raw_content", "")) and self._should_fetch_raw_content(item, query)
        ]
        if pending:
            pending = pending[:5]
        pending_urls = {item.url for item in pending}
        query_text = query.query
        extracted: dict[str, str] = {}
        fetched: dict[str, str] = {}
        fetched_urls: list[str] = []
        empty_fetch_urls: list[str] = []
        enrichment_details: dict[str, dict] = {}
        if pending:
            pending_urls = [item.url for item in pending]
            try:
                extracted_docs = self._search_port.extract_pages(pending_urls, extract_depth="advanced")
            except Exception:
                extracted_docs = []
            extracted = {item.url: item.raw_content or "" for item in extracted_docs}
            fetch_pending = [
                item
                for item in pending
                if self._content_is_poor(extracted.get(item.url, ""))
            ]
            if fetch_pending:
                with ThreadPoolExecutor(max_workers=min(4, len(fetch_pending))) as executor:
                    pairs = list(executor.map(lambda entry: (entry.url, self._safe_fetch_page(entry.url)), fetch_pending))
                fetched = {url: text for url, text in pairs}
                fetched_urls = [url for url, text in pairs if text]
                empty_fetch_urls = [url for url, text in pairs if not text]
        enriched = []
        for item in documents:
            raw_before = getattr(item, "raw_content", "") or ""
            raw_after = raw_before
            extract_attempted = item.url in pending_urls
            fetch_attempted = item.url in fetched or item.url in empty_fetch_urls
            strategy = "none"
            if not self._content_is_poor(raw_before):
                strategy = "search_raw"
            elif item.url in extracted and not self._content_is_poor(extracted.get(item.url, "")):
                raw_after = extracted.get(item.url, "")
                strategy = "extract_pages"
            elif item.url in fetched and fetched.get(item.url):
                raw_after = fetched.get(item.url, "")
                strategy = "extract_then_fetch" if item.url in extracted or extract_attempted else "fetch_page"
            elif item.url in extracted and extracted.get(item.url):
                raw_after = extracted.get(item.url, "")
                strategy = "extract_then_fetch" if fetch_attempted else "extract_pages"
            enrichment_details[item.url] = {
                "raw_content_len_before": self._raw_content_length(raw_before),
                "raw_content_len_after": self._raw_content_length(raw_after),
                "enrichment_strategy_used": strategy,
                "extract_attempted": extract_attempted,
                "fetch_attempted": fetch_attempted,
            }
            enriched.append(
                enrich_document_metadata(
                    item.model_copy(
                        update={
                            "raw_content": raw_after,
                            "query_executed": query_text,
                            "query_planned": item.query_planned or query_text,
                        }
                    ),
                    anchor_company=item.company_anchor,
                )
            )
        return enriched, fetched_urls, empty_fetch_urls, enrichment_details

    def _should_fetch_raw_content(self, document, query: ResearchQuery) -> bool:
        if not self._content_is_poor(getattr(document, "raw_content", "")):
            return False
        if query.research_phase not in {"company_discovery", "company_anchoring"}:
            return True
        domain = domain_from_url(document.url)
        if domain not in DISCOVERY_FETCH_TRUSTED_DOMAINS:
            return False
        normalized_query = normalize_text(query.query)
        return any(token in normalized_query for token in SPANISH_DIRECTORY_FETCH_TOKENS)

    def _can_reuse_anchor_directory_result_in_run(self, item, query: ResearchQuery, state: EngineRuntimeState) -> bool:
        if item.url not in state.visited_urls_run_scoped:
            return False
        if not state.focus_company_locked or not state.current_focus_company_resolution or not state.current_focus_company_resolution.selected_company:
            return False
        if query.expected_field not in {"website", "company_name"}:
            return False
        domain = domain_from_url(item.url)
        if not domain or not domain_is_directory(domain):
            return False
        return document_matches_anchor_strong(item, state.current_focus_company_resolution.selected_company)

    def _extract_anchor_documents(self, documents, anchored_company: str):
        candidate_urls = []
        for item in documents:
            if item.is_publisher_like:
                continue
            text = f"{item.title}\n{item.snippet}\n{item.raw_content}".lower()
            if anchored_company.lower() not in text and not item.is_company_controlled_source:
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
        enriched = []
        for item in extracted:
            enriched.append(
                enrich_document_metadata(
                    item.model_copy(
                        update={
                            "query_planned": f'"{anchored_company}" extract anchored pages',
                            "query_executed": f'"{anchored_company}" extract anchored pages',
                            "research_phase": "evidence_closing",
                            "objective": "Extract richer content from the most relevant anchored pages.",
                            "company_anchor": anchored_company,
                        }
                    ),
                    anchor_company=anchored_company,
                )
            )
        return enriched, candidate_urls, None

    def _official_domain_for_company(self, documents, anchored_company: str) -> str | None:
        official_website = self._official_website_for_company(documents, anchored_company)
        if official_website:
            return domain_from_url(official_website)
        for item in documents:
            domain = domain_from_url(item.url)
            if not domain or item.is_publisher_like or domain_is_directory(domain):
                continue
            if item.is_company_controlled_source:
                return domain
            text = f"{item.title}\n{item.snippet}\n{item.raw_content}".lower()
            domain_root = domain_root_name(domain)
            if domain_root and company_name_matches_anchor_strict(domain_root, anchored_company):
                return domain
        return None

    def _has_domain_validation_support(self, documents, official_domain: str | None) -> bool:
        if not official_domain:
            return False
        for item in documents:
            item_domain = domain_from_url(item.url)
            if not item_domain or not self._domain_matches_official(item_domain, official_domain):
                continue
            if item.is_publisher_like or domain_is_directory(item_domain) or self._is_unusable_website_host(item_domain):
                continue
            return True
        return False

    def _size_hint_for_documents(self, documents) -> tuple[int | None, str]:
        best_value: int | None = None
        best_type = "unknown"
        priority = {"exact": 3, "range": 2, "estimate": 1, "unknown": 0}
        for item in documents:
            value, hint_type = extract_employee_size_hint(f"{item.title}\n{item.snippet}\n{item.raw_content}")
            if priority[hint_type] > priority[best_type] and value is not None:
                best_value = value
                best_type = hint_type
            elif priority[hint_type] == priority[best_type] and value is not None and best_value is not None:
                if hint_type == "range":
                    best_value = min(best_value, value)
                elif hint_type == "exact":
                    best_value = value
        return best_value, best_type

    def _operational_status_hint(self, documents) -> str:
        return "non_operational" if any(text_has_spanish_non_operational_signal(f"{item.title}\n{item.snippet}\n{item.raw_content}") for item in documents) else "active_or_unknown"

    def _size_mismatch_lt50(self, request, size_hint_value: int | None) -> bool:
        max_size = request.constraints.max_company_size
        return bool(max_size is not None and max_size <= 50 and size_hint_value is not None and size_hint_value > max_size)

    def _official_domain_queries(self, anchored_company: str, official_domain: str | None, query_history: list[str]):
        if not official_domain or not domain_has_public_suffix(official_domain):
            return []
        queries = [
            ResearchQuery(
                query=f"{official_domain} contacto aviso legal cif",
                objective="Validate that the anchored domain is the official company website using contact, legal, or identity pages on the company domain.",
                research_phase="company_anchoring",
                source_role="website_resolution",
                candidate_company_name=anchored_company,
                source_tier_target="tier_a",
                expected_field="website",
                stop_if_resolved=True,
                exact_match=False,
                search_depth="advanced",
                min_score=0.58,
                preferred_domains=[official_domain],
                excluded_domains=[],
                expected_source_types=["company_site"],
            ),
        ]
        used = {normalize_text(item) for item in query_history}
        selected = []
        for query in queries:
            if normalize_text(query.query) in used:
                continue
            selected.append(query)
        return selected[:1]

    def _official_website_for_company(self, documents, anchored_company: str | None) -> str | None:
        candidates = self._website_candidates_for_company(documents, anchored_company)
        return candidates[0].candidate_website if candidates else None

    def _document_snapshots(self, documents, enrichment_details: dict[str, dict] | None = None) -> list[SourceTraceDocumentSnapshot]:
        snapshots: list[SourceTraceDocumentSnapshot] = []
        for item in documents:
            source_tier = getattr(item, "source_tier", "unknown") or "unknown"
            source_quality = getattr(item, "source_quality", None) or SourceQuality.UNKNOWN
            detail = (enrichment_details or {}).get(item.url, {})
            snapshots.append(
                SourceTraceDocumentSnapshot(
                    url=item.url,
                    title=item.title,
                    snippet=item.snippet,
                    raw_content=getattr(item, "raw_content", None),
                    source_type=getattr(item, "source_type", None),
                    domain=domain_from_url(item.url),
                    source_tier=source_tier,
                    source_quality=source_quality,
                    company_anchor=getattr(item, "company_anchor", None),
                    is_company_controlled_source=bool(getattr(item, "is_company_controlled_source", False)),
                    raw_content_len_before=detail.get("raw_content_len_before"),
                    raw_content_len_after=detail.get("raw_content_len_after"),
                    enrichment_strategy_used=detail.get("enrichment_strategy_used"),
                    extract_attempted=bool(detail.get("extract_attempted", False)),
                    fetch_attempted=bool(detail.get("fetch_attempted", False)),
                )
            )
        return snapshots

    def _raw_content_length(self, text: str | None) -> int:
        return len(normalize_text(text or ""))

    def _content_is_poor(self, text: str | None) -> bool:
        return self._raw_content_length(text) < 300

    def _website_candidates_for_company(self, documents, anchored_company: str | None) -> list[WebsiteCandidateHint]:
        if not anchored_company:
            return []
        website_scores: dict[str, dict] = {}
        for item in documents:
            if not self._document_relates_to_anchor(item, anchored_company):
                continue
            if document_is_multi_entity_listing(item) and not item.is_company_controlled_source:
                continue
            website = extracted_official_website_from_document(item, anchored_company)
            if not website:
                continue
            if not document_can_seed_website_candidate(item, website, anchor_company=anchored_company):
                continue
            domain = domain_from_url(website)
            if not domain or domain_is_directory(domain) or domain_is_publisher_like(domain) or self._is_unusable_website_host(domain):
                continue
            score = 0
            signals: list[str] = []
            if item.is_company_controlled_source:
                score += 14
                signals.append("company-controlled source")
            if item.source_tier == "tier_a":
                score += 10
                signals.append("tier_a page")
            elif item.source_tier == "tier_b":
                score += 4
            if not document_is_multi_entity_listing(item):
                score += 4
                signals.append("isolated company page")
            if self._document_is_isolated_for_anchor(item, anchored_company):
                score += 6
                signals.append("anchor subject page")
            domain_root = domain_root_name(domain)
            if domain_root and company_name_matches_anchor_strict(domain_root, anchored_company):
                score += 8
                signals.append("domain root matches company")
            entry = website_scores.setdefault(website, {"score": 0, "signals": [], "evidence_urls": []})
            entry["score"] += score
            entry["signals"] = dedupe_preserve_order([*entry["signals"], *signals])
            entry["evidence_urls"] = dedupe_preserve_order([*entry["evidence_urls"], item.url])
        if website_scores:
            ranked = sorted(
                website_scores.items(),
                key=lambda item: (item[1]["score"], len(item[1]["evidence_urls"])),
                reverse=True,
            )
            return [
                WebsiteCandidateHint(
                    candidate_website=website,
                    evidence_urls=data["evidence_urls"][:4],
                    signals=data["signals"][:4],
                    score=float(data["score"]),
                )
                for website, data in ranked[:3]
            ]

        for item in documents:
            if not self._document_relates_to_anchor(item, anchored_company):
                continue
            domain = domain_from_url(item.url)
            if not domain or item.is_publisher_like or domain_is_directory(domain) or self._is_unusable_website_host(domain):
                continue
            if item.is_company_controlled_source:
                return [
                    WebsiteCandidateHint(
                        candidate_website=canonicalize_website(f"https://{domain}"),
                        evidence_urls=[item.url],
                        signals=["company-controlled source"],
                        score=24,
                    )
                ]
            domain_root = domain_root_name(domain)
            if domain_root and company_name_matches_anchor_strict(domain_root, anchored_company):
                return [
                    WebsiteCandidateHint(
                        candidate_website=canonicalize_website(f"https://{domain}"),
                        evidence_urls=[item.url],
                        signals=["domain root matches company"],
                        score=12,
                    )
                ]
        return []

    def _filter_research_trace(self, research_trace, documents):
        selected_urls = {item.url for item in documents}
        filtered = []
        for entry in research_trace:
            urls = [url for url in entry.selected_urls if url in selected_urls]
            if not urls:
                continue
            filtered.append(
                entry.model_copy(
                    update={
                        "selected_urls": urls,
                        "documents_selected": len(urls),
                    }
                )
            )
        return filtered

    def _anchor_confidence(self, documents, *, anchored_company: str | None, official_domain: str | None) -> str:
        if not anchored_company:
            return "low"
        supporting_documents = 0
        official_support = 0
        for item in documents:
            candidates = candidate_company_names_from_document(item)
            text = f"{item.title}\n{item.snippet}\n{item.raw_content}".lower()
            if any(company_name_matches_anchor(candidate, anchored_company) for candidate in candidates) or anchored_company.lower() in text:
                supporting_documents += 1
            if official_domain:
                extracted = extracted_official_website_from_document(item, anchored_company)
                if self._domain_matches_official(domain_from_url(extracted), official_domain) or self._domain_matches_official(domain_from_url(item.url), official_domain):
                    official_support += 1
        if official_domain and supporting_documents >= 2 and official_support >= 1:
            return "high"
        if supporting_documents >= 2:
            return "medium"
        return "low"

    def _select_documents_for_assembler(self, documents, *, anchored_company: str | None, official_domain: str | None, research_trace):
        trace_by_url = self._trace_by_url(research_trace)
        selected = []
        selected_urls: set[str] = set()
        notes: list[str] = []

        company_doc = self._best_document_for_field(
            documents,
            field_name="company_name",
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
        )
        if company_doc is not None:
            selected.append(self._mark_selected_document(company_doc, field_name="company_name", why_selected="Best isolated page for the anchored company identity."))
            selected_urls.add(company_doc.url)

        size_docs = self._best_documents_for_field(
            documents,
            field_name="employee_estimate",
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
            limit=2,
        )
        for item in size_docs:
            selected.append(self._mark_selected_document(item, field_name="employee_estimate", why_selected="Best supporting page for employee-count or team-size evidence."))
            selected_urls.add(item.url)
        if not size_docs and not any(extract_employee_estimate_from_text(f"{item.title}\n{item.snippet}\n{item.raw_content}") is not None for item in selected):
            notes.append("promising_missing_fields=employee_estimate")

        person_docs = self._best_documents_for_field(
            documents,
            field_name="person_name",
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
            limit=2,
        )
        for item in person_docs:
            selected.append(self._mark_selected_document(item, field_name="person_name", why_selected="Best supporting page for a named founder or technical lead."))
            selected_urls.add(item.url)
        selected_person_supported, selected_role_supported = self._has_person_role_support(selected, anchored_company)
        if not person_docs and not selected_person_supported:
            notes.append("promising_missing_fields=person_name")

        role_docs = self._best_documents_for_field(
            documents,
            field_name="role_title",
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
            limit=1,
        )
        for item in role_docs:
            selected.append(self._mark_selected_document(item, field_name="role_title", why_selected="Best supporting page for the target role title or leadership context."))
            selected_urls.add(item.url)
        if not role_docs and not selected_role_supported:
            notes.append("promising_missing_fields=role_title")

        website_doc = self._best_document_for_field(
            documents,
            field_name="website",
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
        )
        if website_doc is not None:
            selected.append(self._mark_selected_document(website_doc, field_name="website", why_selected="Best candidate for the official company website."))
            selected_urls.add(website_doc.url)
        else:
            notes.append("website_unresolved")

        fit_doc = self._best_document_for_field(
            documents,
            field_name="fit_signals",
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
        )
        if fit_doc is not None:
            selected.append(self._mark_selected_document(fit_doc, field_name="fit_signals", why_selected="Best supporting page for AI, automation, or software fit signals."))
            selected_urls.add(fit_doc.url)

        return merge_documents(selected), dedupe_preserve_order(notes)

    def _trace_by_url(self, research_trace):
        trace_by_url: dict[str, ResearchTraceEntry] = {}
        for entry in research_trace:
            for url in entry.selected_urls:
                trace_by_url[url] = entry
        return trace_by_url

    def _best_document_for_field(self, documents, *, field_name: str, anchored_company: str | None, official_domain: str | None, trace_by_url, selected_urls: set[str]):
        candidates = self._rank_documents_for_field(
            documents,
            field_name=field_name,
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
        )
        return candidates[0] if candidates else None

    def _best_documents_for_field(self, documents, *, field_name: str, anchored_company: str | None, official_domain: str | None, trace_by_url, selected_urls: set[str], limit: int):
        return self._rank_documents_for_field(
            documents,
            field_name=field_name,
            anchored_company=anchored_company,
            official_domain=official_domain,
            trace_by_url=trace_by_url,
            selected_urls=selected_urls,
        )[:limit]

    def _rank_documents_for_field(self, documents, *, field_name: str, anchored_company: str | None, official_domain: str | None, trace_by_url, selected_urls: set[str]):
        ranked = []
        for item in documents:
            if item.url in selected_urls:
                continue
            score = self._document_field_score(
                item,
                field_name=field_name,
                anchored_company=anchored_company,
                official_domain=official_domain,
                trace=trace_by_url.get(item.url),
            )
            if score <= 0:
                continue
            ranked.append((score, item))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in ranked]

    def _document_field_score(self, document, *, field_name: str, anchored_company: str | None, official_domain: str | None, trace: ResearchTraceEntry | None):
        text = f"{document.title}\n{document.snippet}\n{document.raw_content}".lower()
        extracted_website = extracted_official_website_from_document(document, anchored_company)
        extracted_domain = domain_from_url(extracted_website)
        doc_domain = domain_from_url(document.url)
        isolated = self._document_is_isolated_for_anchor(document, anchored_company)
        parsed_person, _ = parse_candidate_from_text(f"{document.title}\n{document.snippet}\n{document.raw_content}", document.url)
        score = 0

        if field_name == "company_name":
            if trace and trace.source_role == "governance_resolution" and not isolated and not document.is_company_controlled_source:
                return 0
            if isolated:
                score += 20
            if trace and trace.expected_field == "company_name":
                score += 18
            if trace and trace.source_role == "entity_validation":
                score += 10
            if document.source_tier == "tier_b":
                score += 8
            if document.is_publisher_like:
                score -= 6
        elif field_name == "website":
            if document.is_company_controlled_source:
                score += 40
            if official_domain and (self._domain_matches_official(doc_domain, official_domain) or self._domain_matches_official(extracted_domain, official_domain)):
                score += 35
            if extracted_website:
                score += 24
            if trace and trace.expected_field == "website":
                score += 20
            if trace and trace.source_role == "website_resolution":
                score += 14
            if document.source_tier == "tier_a":
                score += 18
            if not isolated:
                score -= 16
        elif field_name == "person_name":
            persona_anchor_match = self._document_supports_persona_for_anchor(document, anchored_company)
            if not persona_anchor_match:
                return 0
            source_type = classify_person_lead_source(
                document,
                company_name=anchored_company,
                person_name=parsed_person.full_name if parsed_person else None,
                role_title=parsed_person.role_title if parsed_person else None,
            )
            if source_type == "unknown":
                return 0
            if official_domain and (self._domain_matches_official(doc_domain, official_domain) or self._domain_matches_official(extracted_domain, official_domain)):
                score += 18
            if trace and trace.expected_field in {"person_name", "role_title"}:
                score += 20
            if trace and trace.source_role == "governance_resolution":
                score += 14
            if source_type == "company_team_page":
                score += 32
            elif source_type == "functional_exec":
                score += 28
            elif source_type == "mercantile_directory":
                score += 22
            elif source_type == "speaker_or_event":
                score += 14
            elif source_type == "interview_or_press":
                score += 12
            elif source_type == "legal_registry":
                score += 10
            if parsed_person and parsed_person.full_name:
                score += 20
            if parsed_person and parsed_person.role_title:
                score += 12
            if document.is_company_controlled_source:
                score += 16
            if document.source_tier == "tier_b":
                score += 10
            elif document.source_tier == "tier_a":
                score += 12
            if any(token in text for token in ["careers", "hiring", "jobs"]) and not any(token in text for token in ["founder", "co-founder", "ceo", "cto", "leadership", "administrador", "apoderado"]):
                score -= 14
        elif field_name == "role_title":
            persona_anchor_match = self._document_supports_persona_for_anchor(document, anchored_company)
            if not persona_anchor_match:
                return 0
            source_type = classify_person_lead_source(
                document,
                company_name=anchored_company,
                person_name=parsed_person.full_name if parsed_person else None,
                role_title=parsed_person.role_title if parsed_person else None,
            )
            if source_type == "unknown":
                return 0
            if official_domain and (self._domain_matches_official(doc_domain, official_domain) or self._domain_matches_official(extracted_domain, official_domain)):
                score += 14
            if trace and trace.expected_field in {"person_name", "role_title"}:
                score += 16
            if trace and trace.source_role == "governance_resolution":
                score += 14
            if source_type == "company_team_page":
                score += 30
            elif source_type == "functional_exec":
                score += 26
            elif source_type == "mercantile_directory":
                score += 20
            elif source_type == "speaker_or_event":
                score += 12
            elif source_type == "interview_or_press":
                score += 10
            elif source_type == "legal_registry":
                score += 12
            if parsed_person and parsed_person.role_title:
                score += 24
            if parsed_person and parsed_person.full_name:
                score += 10
            if any(token in text for token in ["founder", "ceo", "cto", "chief technology officer", "head of engineering", "head of technology", "director of technology", "director tecnologia", "director de tecnologia", "leadership", "administrador", "apoderado", "consejero delegado"]):
                score += 14
            if document.is_company_controlled_source:
                score += 12
            if document.source_tier == "tier_b":
                score += 8
            if any(token in text for token in ["careers", "hiring", "jobs"]) and not any(token in text for token in ["founder", "ceo", "cto", "leadership", "administrador", "apoderado"]):
                score -= 12
        elif field_name == "employee_estimate":
            explicit_size = extract_employee_estimate_from_text(text) is not None
            mentions_size = any(token in text for token in ["employees", "team size", "company size", "trabajadores", "empleados", "plantilla"])
            if not explicit_size and not mentions_size and not (trace and trace.expected_field == "employee_estimate"):
                return 0
            if explicit_size:
                score += 30
            if trace and trace.expected_field == "employee_estimate":
                score += 20
            if trace and trace.source_role == "employee_count_resolution":
                score += 12
            if official_domain and (self._domain_matches_official(doc_domain, official_domain) or self._domain_matches_official(extracted_domain, official_domain)):
                score += 16
            if document.source_tier == "tier_b":
                score += 14
            if mentions_size:
                score += 10
            if not isolated and document.source_tier != "tier_b":
                score -= 18
        elif field_name == "fit_signals":
            if official_domain and (self._domain_matches_official(doc_domain, official_domain) or self._domain_matches_official(extracted_domain, official_domain)):
                score += 20
            if trace and trace.expected_field == "fit_signals":
                score += 16
            if any(token in text for token in ["ai", "automation", "agent", "software", "machine learning", "data"]):
                score += 16
            if document.source_tier == "tier_a":
                score += 12
            if not isolated and document.is_publisher_like:
                score -= 10
        return score

    def _has_person_role_support(self, documents, anchored_company: str | None) -> tuple[bool, bool]:
        if not anchored_company:
            return False, False
        resolved = resolve_person_signal(documents, company_name=anchored_company)
        return bool(resolved["person_name"]), bool(resolved["role_title"])

    def _document_supports_persona_for_anchor(self, document, anchored_company: str | None) -> bool:
        if not anchored_company:
            return False
        parsed_person, _ = parse_candidate_from_text(f"{document.title}\n{document.snippet}\n{document.raw_content}", document.url)
        if parsed_person is None or not parsed_person.full_name or not parsed_person.role_title:
            return False
        return document_explicitly_supports_persona_candidate(
            document,
            company_name=anchored_company,
            person_name=parsed_person.full_name,
            role_title=parsed_person.role_title,
        )

    def _domain_matches_official(self, domain: str | None, official_domain: str | None) -> bool:
        if not domain or not official_domain:
            return False
        normalized_domain = normalize_text(domain)
        normalized_official = normalize_text(official_domain)
        return normalized_domain == normalized_official or normalized_domain.endswith(f".{normalized_official}")

    def _is_unusable_website_host(self, domain: str | None) -> bool:
        return domain_is_unofficial_website_host(domain)

    def _document_is_isolated_for_anchor(self, document, anchored_company: str | None) -> bool:
        if not anchored_company:
            return False
        if not document_matches_anchor_strong(document, anchored_company):
            return False
        if not document_is_multi_entity_listing(document):
            return True
        title_candidate = document.title.split(" - ", 1)[0].strip()
        return company_name_matches_anchor_strict(title_candidate, anchored_company)

    def _document_relates_to_anchor(self, document, anchored_company: str | None) -> bool:
        if not anchored_company:
            return False
        return document_matches_anchor_strong(document, anchored_company)

    def _mark_selected_document(self, document, *, field_name: str, why_selected: str):
        return document.model_copy(update={"selected_for_field": field_name, "why_selected": why_selected})

    def _memory_payload(self, state: EngineRuntimeState) -> dict:
        return {
            "scope": state.memory.scope,
            "query_history": state.memory.query_history[-20:],
            "visited_urls_run_scoped": state.visited_urls_run_scoped[-30:],
            "blocked_official_domains": [] if self._ignore_persistent_search_memory(state) else state.memory.blocked_official_domains[-15:],
            "searched_company_names": [] if self._ignore_persistent_search_memory(state) else state.memory.searched_company_names[-25:],
            "request_scoped_company_exclusions": self._request_scoped_company_exclusions(state)[:20],
            "registered_lead_names": state.memory.registered_lead_names[-15:],
            "consecutive_hard_miss_runs": state.memory.consecutive_hard_miss_runs,
        }

    def _ignore_persistent_search_memory(self, state: EngineRuntimeState) -> bool:
        return normalize_text(state.environment) == "development"

    def _request_scoped_company_exclusions(self, state: EngineRuntimeState) -> list[str]:
        if self._ignore_persistent_search_memory(state):
            return []
        return request_scoped_company_exclusions(state.memory.company_observations, state.run.request)

    def _uses_spanish_discovery_ladder(self, queries: list[ResearchQuery]) -> bool:
        discovery_domains = {
            self._discovery_directory_for_query(query)
            for query in queries
            if query.research_phase == "company_discovery"
        }
        return any(domain in discovery_domains for domain in SPAIN_DISCOVERY_DIRECTORY_LADDER)

    def _discovery_directory_for_query(self, query: ResearchQuery) -> str | None:
        preferred = [normalize_text(item) for item in query.preferred_domains if item]
        for domain in SPAIN_DISCOVERY_DIRECTORY_LADDER:
            normalized_domain = normalize_text(domain)
            if normalized_domain in preferred:
                return domain
        query_text = normalize_text(query.query)
        if "empresite" in query_text:
            return "empresite.eleconomista.es"
        if "infoempresa" in query_text:
            return "infoempresa.com"
        if "datoscif" in query_text:
            return "datoscif.es"
        if "camara" in query_text or "censo" in query_text:
            return "censo.camara.es"
        return query.preferred_domains[0] if query.preferred_domains else None

    def _exclude_terminal_discovery_documents(
        self,
        documents,
        *,
        excluded_companies: list[str],
    ) -> tuple[list, list[str]]:
        if not excluded_companies:
            return documents, []
        retained = []
        excluded_urls: list[str] = []
        for item in documents:
            if self._document_subject_matches_excluded_company(item, excluded_companies):
                excluded_urls.append(item.url)
                continue
            retained.append(item)
        return retained, dedupe_preserve_order(excluded_urls)

    def _document_subject_matches_excluded_company(self, document, excluded_companies: list[str]) -> bool:
        subject_candidates: list[str] = []
        source_domain = document.domain or domain_from_url(document.url)
        if getattr(document, "company_anchor", None):
            subject_candidates.append(document.company_anchor)
        if source_domain and (title_candidate := directory_title_company_name(document.title or "", source_domain)):
            subject_candidates.append(title_candidate)
        elif title_candidate := title_company_name(document.title or ""):
            subject_candidates.append(title_candidate)
        if not document_is_multi_entity_listing(document):
            subject_candidates.extend(candidate_company_names_from_document(document))
        for excluded in excluded_companies:
            if any(company_name_matches_anchor_strict(candidate, excluded) for candidate in subject_candidates if candidate):
                return True
        return False

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

    def _query_policy_rejection_reasons(self, document, query: ResearchQuery, *, request=None) -> list[str]:
        reasons: list[str] = []
        domain = domain_from_url(document.url)
        if query.research_phase != "company_discovery":
            if query.candidate_company_name and not document_matches_anchor_strong(document, query.candidate_company_name):
                if domain_is_directory(domain) or query.source_tier_target == "tier_b" or query.expected_field in {"website", "company_name", "country", "employee_estimate", "person_name", "role_title"}:
                    reasons.append("cross_company_result")
            return dedupe_preserve_order(reasons)
        if (
            query.country == "es"
            and query.source_role == "entity_validation"
            and domain
            and is_spain_secondary_signal_only_domain(domain)
        ):
            reasons.append("spain_secondary_signal_domain")
        url = (document.url or "").lower()
        title = (document.title or "").lower()
        snippet = (document.snippet or "").lower()
        if query.country == "es" and query.source_role == "entity_validation":
            if is_spanish_category_page(document.url, document.title):
                reasons.append("spain_category_page")
            if text_has_spanish_non_operational_signal(f"{document.title}\n{document.snippet}\n{document.raw_content}"):
                reasons.append("spain_non_operational_entity")
            size_hint_value, _ = extract_employee_size_hint(f"{document.title}\n{document.snippet}\n{document.raw_content}")
            if request is not None and self._size_mismatch_lt50(request, size_hint_value):
                reasons.append("size_mismatch_lt50")
        investor_url_tokens = ["/investor/", "/investors/", "/venture-capital", "/private-equity", "/portfolio/"]
        investor_title_tokens = [
            "investor profile",
            "venture capital",
            "venture factory",
            "private equity",
            "capital partners",
            "vc firm",
            "fund profile",
            "investor",
            "portfolio",
            "seed fund",
            "investment firm",
            "investment company",
        ]
        if any(token in url for token in investor_url_tokens):
            reasons.append("investor_profile")
        if any(token in title or token in snippet for token in investor_title_tokens):
            reasons.append("investor_profile")
        return dedupe_preserve_order(reasons)

    def _query_allows_result(self, document, query: ResearchQuery, *, request=None) -> bool:
        return not self._query_policy_rejection_reasons(document, query, request=request)

