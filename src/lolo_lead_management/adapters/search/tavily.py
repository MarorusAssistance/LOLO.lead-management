from __future__ import annotations

import json
import re
from urllib import request
from urllib.parse import urlparse

from lolo_lead_management.domain.models import EvidenceDocument, PageCapture, ResearchQuery
from lolo_lead_management.ports.search import SearchPort


class TavilySearchPort(SearchPort):
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: int = 20) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        # Ignore inherited process proxies for Tavily/web fetches. In local dev
        # this environment may inject a loopback blackhole proxy that makes all
        # outbound requests fail even when direct connectivity works.
        self._opener = request.build_opener(request.ProxyHandler({}))

    def web_search(self, query: ResearchQuery, *, max_results: int) -> list[EvidenceDocument]:
        include_raw_content = self._include_raw_content_for_query(query)
        payload = {
            "query": query.query,
            "topic": "general",
            "search_depth": query.search_depth,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": include_raw_content,
            "include_images": False,
            "include_usage": False,
            "auto_parameters": False,
        }
        if query.search_depth == "advanced":
            payload["chunks_per_source"] = 3
        if query.country:
            payload["country"] = self._country_name(query.country)
        if query.preferred_domains:
            payload["include_domains"] = query.preferred_domains
        if query.excluded_domains:
            payload["exclude_domains"] = query.excluded_domains
        if query.exact_match:
            payload["exact_match"] = True

        req = request.Request(
            self._base_url,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with self._opener.open(req, timeout=self._timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))

        results = raw.get("results", [])
        documents: list[EvidenceDocument] = []
        for item in results[:max_results]:
            score = item.get("score")
            if score is not None and score < query.min_score:
                continue
            url = item.get("url", "")
            domain = self._domain_from_url(url)
            raw_content = item.get("raw_content", "") or ""
            documents.append(
                EvidenceDocument(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("content", "") or raw_content[:400],
                    source_type="tavily_search",
                    raw_content=raw_content,
                    content_format="text",
                    domain=domain,
                    search_score=score,
                    query_planned=query.query,
                    query_executed=query.query,
                    research_phase=query.research_phase,
                    objective=query.objective,
                    source_tier=query.source_tier_target or "unknown",
                    company_anchor=query.candidate_company_name,
                )
            )
        return documents

    def _include_raw_content_for_query(self, query: ResearchQuery):
        return "text"

    def fetch_page_capture(self, url: str) -> PageCapture:
        req = request.Request(url, headers={"User-Agent": "LOLOLeadManagement/0.1"})
        with self._opener.open(req, timeout=self._timeout_seconds) as response:
            payload = response.read().decode("utf-8", errors="ignore")
            content_type = response.headers.get("Content-Type")
        is_html = "html" in (content_type or "").lower() or bool(re.search(r"<html|<body|<main|<section|<article", payload, re.IGNORECASE))
        extracted = self._html_to_text(payload) if is_html else re.sub(r"\s+", " ", payload).strip()
        return PageCapture(
            url=url,
            raw_html=payload if is_html else None,
            extracted_text=extracted,
            content_format="html" if is_html else "text",
            content_type=content_type,
        )

    def extract_pages(self, urls: list[str], *, extract_depth: str = "advanced") -> list[EvidenceDocument]:
        cleaned_urls = [url for url in urls if url]
        if not cleaned_urls:
            return []
        payload = {
            "urls": cleaned_urls[:5],
            "extract_depth": extract_depth,
            "include_images": False,
        }
        req = request.Request(
            self._endpoint("extract"),
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with self._opener.open(req, timeout=self._timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))

        documents: list[EvidenceDocument] = []
        for item in raw.get("results", [])[:5]:
            url = item.get("url", "")
            raw_content = item.get("raw_content", "") or ""
            documents.append(
                EvidenceDocument(
                    url=url,
                    title=item.get("title", ""),
                    snippet=raw_content[:400],
                    source_type="tavily_extract",
                    raw_content=raw_content,
                    content_format="text",
                    domain=self._domain_from_url(url),
                )
            )
        return documents

    def _html_to_text(self, html: str) -> str:
        payload = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        payload = re.sub(r"<style.*?</style>", " ", payload, flags=re.IGNORECASE | re.DOTALL)
        payload = re.sub(r"<[^>]+>", " ", payload)
        return re.sub(r"\s+", " ", payload).strip()

    def _country_name(self, code: str) -> str:
        mapping = {
            "es": "spain",
            "pt": "portugal",
            "fr": "france",
            "de": "germany",
            "gb": "united kingdom",
            "eu": "spain",
        }
        return mapping.get((code or "").lower(), "spain")

    def _domain_from_url(self, url: str) -> str | None:
        try:
            return (urlparse(url).hostname or "").removeprefix("www.") or None
        except ValueError:
            return None

    def _endpoint(self, name: str) -> str:
        base = self._base_url.rstrip("/")
        if base.endswith("/search"):
            return f"{base[:-7]}/{name}"
        return f"{base}/{name}"
