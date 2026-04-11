from __future__ import annotations

from abc import ABC, abstractmethod

from lolo_lead_management.domain.models import EvidenceDocument, PageCapture, ResearchQuery


class SearchPort(ABC):
    @abstractmethod
    def web_search(self, query: ResearchQuery, *, max_results: int) -> list[EvidenceDocument]:
        raise NotImplementedError

    @abstractmethod
    def fetch_page_capture(self, url: str) -> PageCapture:
        raise NotImplementedError

    def fetch_page(self, url: str) -> str:
        capture = self.fetch_page_capture(url)
        return capture.extracted_text or ""

    @abstractmethod
    def extract_pages(self, urls: list[str], *, extract_depth: str = "advanced") -> list[EvidenceDocument]:
        raise NotImplementedError
