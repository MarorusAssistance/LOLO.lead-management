from __future__ import annotations

import re

from lolo_lead_management.domain.models import EvidenceDocument, PageCapture, ResearchQuery
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

    def extract_pages(self, urls: list[str], *, extract_depth: str = "advanced") -> list[EvidenceDocument]:
        _ = extract_depth
        documents: list[EvidenceDocument] = []
        for url in urls:
            raw_content = self._pages.get(url, "")
            documents.append(
                EvidenceDocument(
                    url=url,
                    title="",
                    snippet=raw_content[:400],
                    source_type="tavily_extract",
                    raw_content=self._html_to_text(raw_content) if self._looks_like_html(raw_content) else raw_content,
                    raw_html=raw_content if self._looks_like_html(raw_content) else None,
                    content_format="html" if self._looks_like_html(raw_content) else "text",
                )
            )
        return documents

    def fetch_page_capture(self, url: str) -> PageCapture:
        payload = self._pages.get(url, "")
        is_html = self._looks_like_html(payload)
        return PageCapture(
            url=url,
            raw_html=payload if is_html else None,
            extracted_text=self._html_to_text(payload) if is_html else payload,
            content_format="html" if is_html else "text" if payload else "unknown",
        )

    def _looks_like_html(self, payload: str) -> bool:
        return bool(re.search(r"<html|<body|<main|<section|<article|<div|<h[1-6]\b", payload or "", re.IGNORECASE))

    def _html_to_text(self, html: str) -> str:
        payload = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        payload = re.sub(r"<style.*?</style>", " ", payload, flags=re.IGNORECASE | re.DOTALL)
        payload = re.sub(r"<[^>]+>", " ", payload)
        return re.sub(r"\s+", " ", payload).strip()
