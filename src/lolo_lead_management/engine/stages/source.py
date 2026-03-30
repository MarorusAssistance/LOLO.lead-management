from __future__ import annotations

from urllib.parse import urlparse

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import EvidenceItem, SourcingDossier
from lolo_lead_management.engine.agent_models import SourceQueryPlan
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import (
    build_heuristic_dossier,
    build_query_candidates,
    choose_query,
    clean_company_name,
    collect_fit_signals,
    dedupe_preserve_order,
    extract_country_code,
    extract_official_website,
    parse_candidate_from_text,
)
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.search import SearchPort


class SourceStage:
    def __init__(self, *, search_port: SearchPort, agent_executor: StageAgentExecutor, max_results: int) -> None:
        self._search_port = search_port
        self._agent_executor = agent_executor
        self._max_results = max_results

    def execute(self, state: EngineRuntimeState) -> tuple[str | None, SourcingDossier]:
        request = state.run.request
        deterministic_candidates = build_query_candidates(request, state.run.applied_relaxation_stage)
        try:
            query_plan = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.SOURCE],
                payload={
                    "task": "plan_queries",
                    "request": request.model_dump(mode="json"),
                    "memory": state.memory.model_dump(mode="json"),
                    "relaxation_stage": state.run.applied_relaxation_stage,
                    "deterministic_query_candidates": deterministic_candidates,
                },
                output_model=SourceQueryPlan,
            )
        except Exception:
            query_plan = None
        llm_candidates = self._sanitize_queries(query_plan.suggested_queries if query_plan else [])
        query_candidates = dedupe_preserve_order([*llm_candidates, *deterministic_candidates])
        query = choose_query(
            query_candidates,
            state.memory.query_history + ([state.current_query] if state.current_query else []),
        )
        if query is None:
            return None, SourcingDossier(sourcing_status=SourcingStatus.NO_CANDIDATE, notes=["no_unused_queries_left"])

        state.run.budget.search_calls_used += 1
        search_results = self._search_port.web_search(query, max_results=self._max_results)
        filtered = [item for item in search_results if item.url not in state.memory.visited_urls and self._is_usable_search_result(item.url)]
        if not filtered:
            return query, SourcingDossier(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_used=query,
                notes=["all_results_were_already_visited"],
            )

        evidence_items: list[EvidenceItem] = []
        page_texts: dict[str, str] = {}
        result_payload: list[dict] = []
        for item in filtered[: self._max_results]:
            page_text = self._safe_fetch_page(item.url)
            evidence_items.append(item)
            page_texts[item.url] = page_text
            result_payload.append(
                {
                    "url": item.url,
                    "title": item.title,
                    "snippet": item.snippet,
                    "source_type": item.source_type,
                    "page_excerpt": page_text[:2000],
                }
            )

        heuristic = build_heuristic_dossier(
            request=request,
            query=query,
            evidence_items=evidence_items,
            page_texts=page_texts,
        )

        try:
            generated = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.SOURCE],
                payload={
                    "task": "extract_candidate",
                    "request": request.model_dump(mode="json"),
                    "memory": state.memory.model_dump(mode="json"),
                    "relaxation_stage": state.run.applied_relaxation_stage,
                    "query_used": query,
                    "search_results": result_payload,
                    "rules": {
                        "allowed_evidence_urls": [item.url for item in evidence_items],
                        "already_explored_companies": state.memory.searched_company_names,
                        "already_registered_leads": state.memory.registered_lead_names,
                    },
                },
                output_model=SourcingDossier,
            )
        except Exception:
            generated = None
        if generated is not None:
            sanitized = self._sanitize_generated_dossier(
                generated=generated,
                query=query,
                request=request,
                evidence_items=evidence_items,
                page_texts=page_texts,
                heuristic=heuristic,
            )
            if sanitized is not None:
                company_name = sanitized.company.name.lower() if sanitized.company else ""
                if company_name and company_name not in {name.lower() for name in state.memory.searched_company_names}:
                    return query, self._maybe_follow_up(state, sanitized)

        dossier = heuristic
        if dossier.company and dossier.company.name.lower() in {name.lower() for name in state.memory.searched_company_names}:
            return query, SourcingDossier(
                sourcing_status=SourcingStatus.NO_CANDIDATE,
                query_used=query,
                notes=dossier.notes + ["company_already_terminally_explored"],
            )
        return query, self._maybe_follow_up(state, dossier)

    def _sanitize_queries(self, values: list[str]) -> list[str]:
        queries: list[str] = []
        for value in values:
            normalized = " ".join((value or "").split()).strip()
            if len(normalized) < 10:
                continue
            if normalized not in queries:
                queries.append(normalized)
        return queries[:6]

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
        blocked = {
            "facebook.com",
            "instagram.com",
            "twitter.com",
            "x.com",
            "youtube.com",
            "tiktok.com",
        }
        return hostname not in blocked

    def _sanitize_generated_dossier(
        self,
        *,
        generated: SourcingDossier,
        query: str,
        request,
        evidence_items: list[EvidenceItem],
        page_texts: dict[str, str],
        heuristic: SourcingDossier,
    ) -> SourcingDossier | None:
        evidence_by_url = {item.url: item for item in evidence_items}
        evidence = [evidence_by_url[item.url] for item in generated.evidence if item.url in evidence_by_url]
        if generated.company is None:
            return None
        baseline_company = heuristic.company
        baseline_person = heuristic.person
        supporting_evidence = evidence or heuristic.evidence or evidence_items[:1]
        evidence_text = " ".join(
            [item.title + " " + item.snippet + " " + page_texts.get(item.url, "") for item in supporting_evidence]
        )
        company = generated.company.model_copy(deep=True)
        cleaned_name = clean_company_name(company.name)
        if cleaned_name is None or len(cleaned_name.split()) > 8 or "{" in company.name or "}" in company.name:
            cleaned_name = baseline_company.name if baseline_company else None
        if cleaned_name is None:
            return None
        company.name = cleaned_name

        supported_country = extract_country_code(evidence_text)
        company.country_code = supported_country or (baseline_company.country_code if baseline_company else company.country_code)

        parsed_companies = [
            parse_candidate_from_text(" ".join([item.title, item.snippet, page_texts.get(item.url, "")]), item.url)[1]
            for item in supporting_evidence
        ]
        employee_candidates = [item.employee_estimate for item in parsed_companies if item and item.employee_estimate is not None]
        company.employee_estimate = employee_candidates[0] if employee_candidates else (baseline_company.employee_estimate if baseline_company else None)

        supported_website = extract_official_website(evidence_text, supporting_evidence[0].url)
        company.website = supported_website or (baseline_company.website if baseline_company else None)
        try:
            evidence_host = (urlparse(supporting_evidence[0].url).hostname or "").removeprefix("www.")
        except ValueError:
            evidence_host = ""
        if evidence_host.endswith("eu-startups.com") or evidence_host.endswith("seedtable.com") or evidence_host.endswith("wellfound.com"):
            company.website = None

        person = generated.person
        if person and person.full_name and person.full_name not in evidence_text:
            person = None
        if person is None and baseline_person is not None:
            person = baseline_person

        fit_signals = [item for item in dedupe_preserve_order(generated.fit_signals) if item in request.search_themes]
        if not fit_signals:
            fit_signals = heuristic.fit_signals or collect_fit_signals(
                " ".join([item.title + " " + item.snippet for item in evidence_items]),
                request,
            )
        if not self._is_company_name_usable(company.name):
            return None
        if person and not self._is_person_name_usable(person.full_name):
            person = None
        return SourcingDossier(
            sourcing_status=SourcingStatus.FOUND,
            query_used=query,
            person=person,
            company=company,
            fit_signals=fit_signals,
            evidence=supporting_evidence,
            notes=["query_used=" + query, "candidate_selected_by=llm"],
        )

    def _is_company_name_usable(self, value: str | None) -> bool:
        if not value:
            return False
        normalized = value.strip().lower()
        if normalized in {"artificial intelligence", "ai", "software company", "startup", "company"}:
            return False
        return len(normalized.split()) <= 8

    def _is_person_name_usable(self, value: str | None) -> bool:
        if not value:
            return False
        normalized = " ".join(value.split())
        return len(normalized) <= 80 and len(normalized.split()) <= 6 and "industries:" not in normalized.lower()

    def _maybe_follow_up(self, state: EngineRuntimeState, dossier: SourcingDossier) -> SourcingDossier:
        if dossier.sourcing_status != SourcingStatus.FOUND or dossier.company is None or len(dossier.evidence) >= 2:
            return dossier

        request = state.run.request
        country_hint = request.constraints.preferred_country or dossier.company.country_code or "spain"
        theme_hint = " ".join((dossier.fit_signals or request.search_themes)[:2])
        buyer_hint = request.buyer_targets[0].replace("_", " ") if request.buyer_targets else "founder"
        follow_up_query = f'{dossier.company.name} {buyer_hint} {country_hint} {theme_hint}'.strip()
        state.run.budget.search_calls_used += 1
        try:
            results = self._search_port.web_search(follow_up_query, max_results=self._max_results)
        except Exception:
            return dossier

        evidence = list(dossier.evidence)
        existing_urls = {item.url for item in evidence}
        person = dossier.person
        company = dossier.company.model_copy(deep=True)
        for item in results[: self._max_results]:
            if item.url in existing_urls or item.url in state.memory.visited_urls or not self._is_usable_search_result(item.url):
                continue
            page_text = self._safe_fetch_page(item.url)
            combined_text = " ".join([item.title, item.snippet, page_text])
            if dossier.company.name.lower() not in combined_text.lower():
                continue
            parsed_person, parsed_company = parse_candidate_from_text(combined_text, item.url)
            evidence.append(item)
            if person is None and parsed_person is not None and self._is_person_name_usable(parsed_person.full_name):
                person = parsed_person
            if parsed_company is not None:
                if company.country_code is None and parsed_company.country_code is not None:
                    company.country_code = parsed_company.country_code
                if company.employee_estimate is None and parsed_company.employee_estimate is not None:
                    company.employee_estimate = parsed_company.employee_estimate
                if company.website is None and parsed_company.website is not None:
                    company.website = parsed_company.website
            break

        if len(evidence) == len(dossier.evidence):
            return dossier
        fit_signals = collect_fit_signals(" ".join([item.title + " " + item.snippet for item in evidence]), request) or dossier.fit_signals
        return SourcingDossier(
            sourcing_status=SourcingStatus.FOUND,
            query_used=dossier.query_used,
            person=person,
            company=company,
            fit_signals=fit_signals,
            evidence=evidence,
            notes=dedupe_preserve_order([*dossier.notes, f"follow_up_query={follow_up_query}"]),
        )
