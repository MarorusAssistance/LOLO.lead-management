from __future__ import annotations

from lolo_lead_management.domain.models import EvidenceDocument, ResearchQuery
from lolo_lead_management.ports.search import SearchPort


class FakeSearchPort(SearchPort):
    def __init__(self, search_index: dict[str, list[EvidenceDocument]] | None = None, pages: dict[str, str] | None = None) -> None:
        self._search_index = search_index or {}
        self._pages = pages or {}

    def web_search(self, query: ResearchQuery, *, max_results: int) -> list[EvidenceDocument]:
        return [
            item.model_copy(
                update={
                    "query_planned": query.query,
                    "query_executed": query.query,
                    "research_phase": query.research_phase,
                    "objective": query.objective,
                    "company_anchor": query.candidate_company_name,
                }
            )
            for item in self._search_index.get(query.query, [])[:max_results]
        ]

    def fetch_page(self, url: str) -> str:
        return self._pages.get(url, "")
