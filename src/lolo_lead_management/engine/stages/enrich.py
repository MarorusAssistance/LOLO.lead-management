from __future__ import annotations

from lolo_lead_management.domain.enums import SourcingStatus
from lolo_lead_management.domain.models import SourcingDossier
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.rules import collect_fit_signals, dedupe_preserve_order, parse_candidate_from_text
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.search import SearchPort


class EnrichStage:
    def __init__(self, *, search_port: SearchPort, agent_executor: StageAgentExecutor, max_results: int) -> None:
        self._search_port = search_port
        self._agent_executor = agent_executor
        _ = agent_executor
        self._max_results = max_results

    def execute(self, state: EngineRuntimeState) -> SourcingDossier:
        dossier = state.current_dossier
        if dossier is None or dossier.company is None:
            return SourcingDossier(sourcing_status=SourcingStatus.NO_CANDIDATE, notes=["no_dossier_to_enrich"])

        role_terms = [item.replace("_", " ") for item in state.run.request.buyer_targets[:3]]
        role_hint = role_terms[0] if role_terms else (dossier.person.role_title if dossier.person and dossier.person.role_title else "founder")
        country_hint = state.run.request.constraints.preferred_country or dossier.company.country_code or "spain"
        theme_hint = ", ".join((dossier.fit_signals or state.run.request.search_themes)[:2]).replace(",", " ")
        query = f'{dossier.company.name} {role_hint} {country_hint} {theme_hint}'.strip()

        state.run.budget.search_calls_used += 1
        results = self._search_port.web_search(query, max_results=self._max_results)
        evidence = list(dossier.evidence)
        existing_urls = {entry.url for entry in evidence}
        page_payload: list[dict] = []
        for item in results[: self._max_results]:
            if item.url in existing_urls:
                continue
            page_text = self._safe_fetch_page(item.url)
            page_payload.append(
                {
                    "url": item.url,
                    "title": item.title,
                    "snippet": item.snippet,
                    "source_type": item.source_type,
                    "page_excerpt": page_text[:2000],
                }
            )
            evidence.append(item)

        person = dossier.person
        company = dossier.company.model_copy(deep=True)
        for item in evidence:
            page_text = ""
            if any(payload["url"] == item.url for payload in page_payload):
                page_text = next(payload["page_excerpt"] for payload in page_payload if payload["url"] == item.url)
            combined_text = " ".join([item.title, item.snippet, page_text])
            parsed_person, parsed_company = parse_candidate_from_text(combined_text, item.url)
            if person is None and parsed_person is not None:
                person = parsed_person
            if parsed_company is not None:
                if company.employee_estimate is None and parsed_company.employee_estimate is not None:
                    company.employee_estimate = parsed_company.employee_estimate
                if company.country_code is None and parsed_company.country_code is not None:
                    company.country_code = parsed_company.country_code
                if company.website is None and parsed_company.website is not None:
                    company.website = parsed_company.website
        fit_signals = collect_fit_signals(" ".join([item.title + " " + item.snippet for item in evidence]), state.run.request)
        return SourcingDossier(
            sourcing_status=SourcingStatus.FOUND,
            query_used=query,
            person=person,
            company=company,
            fit_signals=fit_signals or dossier.fit_signals,
            evidence=evidence[: max(len(dossier.evidence) + 2, 2)],
            notes=dossier.notes + [f"enrichment_query={query}"],
        )

    def _safe_fetch_page(self, url: str) -> str:
        try:
            return self._search_port.fetch_page(url)
        except Exception:
            return ""
