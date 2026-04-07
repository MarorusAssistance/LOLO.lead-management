from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import ResearchQuery, ResearchQueryPlan, ResearchTraceEntry, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    build_research_query_plan,
    canonicalize_website,
    candidate_company_names_from_document,
    choose_queries,
    company_name_matches_anchor,
    dedupe_preserve_order,
    document_is_multi_entity_listing,
    domain_from_url,
    domain_is_directory,
    domain_is_publisher_like,
    enrich_document_metadata,
    extract_employee_estimate_from_text,
    extracted_official_website_from_document,
    domain_root_name,
    merge_research_query_plans,
    merge_documents,
    normalize_text,
    parse_candidate_from_text,
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
                    source_tier_target=query.source_tier_target,
                    expected_field=query.expected_field,
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
                current_documents=documents,
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
                        source_tier_target=query.source_tier_target,
                        expected_field=query.expected_field,
                        documents_considered=len(filtered),
                        documents_selected=len(selected),
                        selected_urls=[item.url for item in selected],
                    )
                )
                documents.extend(selected)
                executed_queries.append(query)
            documents = merge_documents(documents)
            documents = self._focus_documents(documents, anchored_company)
            extracted_documents = self._extract_anchor_documents(documents, anchored_company)
            if extracted_documents:
                state.run.budget.search_calls_used += 1
                documents = merge_documents([*documents, *extracted_documents])
                documents = self._focus_documents(documents, anchored_company)
                research_trace.append(
                    ResearchTraceEntry(
                        query_planned=f'"{anchored_company}" extract anchored pages',
                        query_executed=f'"{anchored_company}" extract anchored pages',
                        research_phase="evidence_closing",
                        objective="Extract richer content from the most relevant anchored pages.",
                        candidate_company_name=anchored_company,
                        source_tier_target="tier_a",
                        expected_field="multi",
                        documents_considered=len(extracted_documents),
                        documents_selected=len(extracted_documents),
                        selected_urls=[item.url for item in extracted_documents],
                    )
                )
            official_domain = self._official_domain_for_company(documents, anchored_company)
            domain_queries = self._official_domain_queries(anchored_company, official_domain, query_history + [item.query for item in executed_queries])
            for query in domain_queries:
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
                        source_tier_target=query.source_tier_target,
                        expected_field=query.expected_field,
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

        official_website = self._official_website_for_company(documents, anchored_company)
        official_domain = domain_from_url(official_website)
        selected_documents, selection_notes = self._select_documents_for_assembler(
            documents,
            anchored_company=anchored_company,
            official_domain=official_domain,
            research_trace=research_trace,
        )
        if not selected_documents:
            return SourcePassResult(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_plan=plan,
                executed_queries=executed_queries,
                anchored_company_name=anchored_company,
                research_trace=research_trace,
                notes=[*selection_notes, "no_documents_selected_for_assembler"],
            )
        filtered_trace = self._filter_research_trace(research_trace, selected_documents)
        anchor_confidence = self._anchor_confidence(
            documents,
            anchored_company=anchored_company,
            official_domain=official_domain,
        )
        notes = [f"queries_executed={len(executed_queries)}", f"anchor_confidence={anchor_confidence}"]
        if official_website:
            notes.append(f"official_website={official_website}")
        notes.extend(selection_notes)

        return SourcePassResult(
            sourcing_status=SourcingStatus.FOUND,
            query_plan=plan,
            executed_queries=executed_queries,
            documents=selected_documents,
            anchored_company_name=anchored_company,
            research_trace=filtered_trace or research_trace,
            notes=notes,
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

    def _choose_anchor_queries(self, plan: ResearchQueryPlan, query_history: list[str], *, current_documents):
        selected = []
        selected_queries = set()
        priority_groups = [
            lambda item: item.expected_field == "website",
            lambda item: item.expected_field in {"person_name", "role_title"},
            lambda item: item.expected_field == "employee_estimate",
            lambda item: item.expected_field == "fit_signals",
        ]
        for predicate in priority_groups:
            for query in choose_queries(plan, query_history + [item.query for item in selected], limit=6):
                if query.query in selected_queries:
                    continue
                if query.stop_if_resolved and self._query_already_resolved(query, current_documents):
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
                if query.stop_if_resolved and self._query_already_resolved(query, current_documents):
                    continue
                selected.append(query)
                selected_queries.add(query.query)
                if len(selected) >= 3:
                    break
        return selected

    def _query_already_resolved(self, query: ResearchQuery, documents) -> bool:
        if query.expected_field == "website":
            anchor_company = next((item.company_anchor for item in documents if item.company_anchor), None)
            return self._official_website_for_company(documents, anchor_company) is not None or any(item.is_company_controlled_source for item in documents)
        if query.expected_field == "fit_signals":
            return any(item.source_tier == "tier_a" and any(token in f"{item.title} {item.snippet} {item.raw_content}".lower() for token in ["ai", "automation", "agent", "software"]) for item in documents)
        return False

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
            return []
        try:
            extracted = self._search_port.extract_pages(candidate_urls, extract_depth="advanced")
        except Exception:
            return []
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
        return enriched

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
            if domain_root and company_name_matches_anchor(domain_root, anchored_company):
                return domain
        return None

    def _official_domain_queries(self, anchored_company: str, official_domain: str | None, query_history: list[str]):
        if not official_domain:
            return []
        queries = [
            ResearchQuery(
                query=f'"{anchored_company}" team leadership founder ceo cto',
                objective="Search the official domain for named leaders and explicit roles tied to the company.",
                research_phase="field_acquisition",
                candidate_company_name=anchored_company,
                source_tier_target="tier_a",
                expected_field="person_name",
                exact_match=True,
                search_depth="advanced",
                min_score=0.58,
                preferred_domains=[official_domain],
                excluded_domains=[],
                expected_source_types=["company_site"],
            ),
            ResearchQuery(
                query=f'"{anchored_company}" careers team employees size',
                objective="Search the official domain for hiring, team, and explicit company-size clues.",
                research_phase="evidence_closing",
                candidate_company_name=anchored_company,
                source_tier_target="tier_a",
                expected_field="employee_estimate",
                exact_match=True,
                search_depth="advanced",
                min_score=0.56,
                preferred_domains=[official_domain],
                excluded_domains=[],
                expected_source_types=["company_site", "job_board"],
            ),
        ]
        used = {normalize_text(item) for item in query_history}
        selected = []
        for query in queries:
            if normalize_text(query.query) in used:
                continue
            selected.append(query)
        return selected[:2]

    def _official_website_for_company(self, documents, anchored_company: str | None) -> str | None:
        if not anchored_company:
            return None
        website_scores: dict[str, int] = {}
        for item in documents:
            website = extracted_official_website_from_document(item, anchored_company)
            if not website:
                continue
            domain = domain_from_url(website)
            if not domain or domain_is_directory(domain) or domain_is_publisher_like(domain):
                continue
            score = 0
            if item.is_company_controlled_source:
                score += 14
            if item.source_tier == "tier_a":
                score += 10
            elif item.source_tier == "tier_b":
                score += 4
            if not document_is_multi_entity_listing(item):
                score += 4
            text = f"{item.title}\n{item.snippet}\n{item.raw_content}"
            if anchored_company.lower() in text.lower():
                score += 6
            domain_root = domain_root_name(domain)
            if domain_root and company_name_matches_anchor(domain_root, anchored_company):
                score += 8
            website_scores[website] = website_scores.get(website, 0) + score
        if website_scores:
            return max(website_scores.items(), key=lambda item: item[1])[0]

        for item in documents:
            domain = domain_from_url(item.url)
            if not domain or item.is_publisher_like or domain_is_directory(domain):
                continue
            if item.is_company_controlled_source:
                return canonicalize_website(f"https://{domain}")
            domain_root = domain_root_name(domain)
            if domain_root and company_name_matches_anchor(domain_root, anchored_company):
                return canonicalize_website(f"https://{domain}")
        return None

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

        if official_domain:
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
            if not person_docs:
                notes.append("promising_missing_fields=person_name")

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
            if not size_docs:
                notes.append("promising_missing_fields=employee_estimate")

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
        else:
            company_doc = self._best_document_for_field(
                documents,
                field_name="company_name",
                anchored_company=anchored_company,
                official_domain=official_domain,
                trace_by_url=trace_by_url,
                selected_urls=selected_urls,
            )
            if company_doc is not None:
                selected.append(self._mark_selected_document(company_doc, field_name="company_name", why_selected="Cleanest company discovery page while the official website is still unresolved."))
                selected_urls.add(company_doc.url)

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
            if isolated:
                score += 20
            if trace and trace.expected_field == "company_name":
                score += 18
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
            if document.source_tier == "tier_a":
                score += 18
            if not isolated:
                score -= 16
        elif field_name == "person_name":
            if not official_domain:
                return 0
            if official_domain and (self._domain_matches_official(doc_domain, official_domain) or self._domain_matches_official(extracted_domain, official_domain)):
                score += 28
            if trace and trace.expected_field in {"person_name", "role_title"}:
                score += 20
            if any(token in text for token in ["founder", "co-founder", "ceo", "cto", "leadership", "team", "head of engineering", "director of engineering", "head of data", "director of data", "ai lead"]):
                score += 18
            if parsed_person and parsed_person.full_name:
                score += 20
            else:
                score -= 18
            if document.source_tier == "tier_b":
                score += 10
            if any(token in text for token in ["careers", "hiring", "jobs"]) and not any(token in text for token in ["founder", "co-founder", "ceo", "cto", "leadership"]):
                score -= 14
            if not isolated:
                score -= 22
        elif field_name == "employee_estimate":
            if not official_domain:
                return 0
            if extract_employee_estimate_from_text(text) is not None:
                score += 30
            if trace and trace.expected_field == "employee_estimate":
                score += 20
            if official_domain and (self._domain_matches_official(doc_domain, official_domain) or self._domain_matches_official(extracted_domain, official_domain)):
                score += 16
            if document.source_tier == "tier_b":
                score += 14
            if any(token in text for token in ["employees", "team size", "company size", "careers", "hiring", "jobs"]):
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

    def _domain_matches_official(self, domain: str | None, official_domain: str | None) -> bool:
        if not domain or not official_domain:
            return False
        normalized_domain = normalize_text(domain)
        normalized_official = normalize_text(official_domain)
        return normalized_domain == normalized_official or normalized_domain.endswith(f".{normalized_official}")

    def _document_is_isolated_for_anchor(self, document, anchored_company: str | None) -> bool:
        if not anchored_company:
            return False
        candidates = candidate_company_names_from_document(document)
        if any(company_name_matches_anchor(candidate, anchored_company) for candidate in candidates):
            if not document_is_multi_entity_listing(document):
                return True
            title_candidate = document.title.split(" - ", 1)[0].strip()
            return company_name_matches_anchor(title_candidate, anchored_company)
        if document.company_anchor and company_name_matches_anchor(document.company_anchor, anchored_company):
            return True
        return False

    def _mark_selected_document(self, document, *, field_name: str, why_selected: str):
        return document.model_copy(update={"selected_for_field": field_name, "why_selected": why_selected})

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
