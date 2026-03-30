from __future__ import annotations

from lolo_lead_management.domain.models import EvidenceItem
from lolo_lead_management.ports.search import SearchPort


class FakeSearchPort(SearchPort):
    def __init__(self, search_index: dict[str, list[EvidenceItem]] | None = None, pages: dict[str, str] | None = None) -> None:
        self._search_index = search_index or {}
        self._pages = pages or {}

    def web_search(self, query: str, *, max_results: int) -> list[EvidenceItem]:
        return self._search_index.get(query, [])[:max_results]

    def fetch_page(self, url: str) -> str:
        return self._pages.get(url, "")
