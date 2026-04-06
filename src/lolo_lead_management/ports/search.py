from __future__ import annotations

from abc import ABC, abstractmethod

from lolo_lead_management.domain.models import EvidenceDocument, ResearchQuery


class SearchPort(ABC):
    @abstractmethod
    def web_search(self, query: ResearchQuery, *, max_results: int) -> list[EvidenceDocument]:
        raise NotImplementedError

    @abstractmethod
    def fetch_page(self, url: str) -> str:
        raise NotImplementedError
