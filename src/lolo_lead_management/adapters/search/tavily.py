from __future__ import annotations

import json
import re
from urllib import request

from lolo_lead_management.domain.models import EvidenceItem
from lolo_lead_management.ports.search import SearchPort


class TavilySearchPort(SearchPort):
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: int = 20) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds

    def web_search(self, query: str, *, max_results: int) -> list[EvidenceItem]:
        payload = {
            "query": query,
            "topic": "general",
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
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
        with request.urlopen(req, timeout=self._timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))

        results = raw.get("results", [])
        return [
            EvidenceItem(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", "") or item.get("raw_content", "") or "",
                source_type="tavily_search",
            )
            for item in results[:max_results]
        ]

    def fetch_page(self, url: str) -> str:
        req = request.Request(url, headers={"User-Agent": "LOLOLeadManagement/0.1"})
        with request.urlopen(req, timeout=self._timeout_seconds) as response:
            payload = response.read().decode("utf-8", errors="ignore")
        payload = re.sub(r"<script.*?</script>", " ", payload, flags=re.IGNORECASE | re.DOTALL)
        payload = re.sub(r"<style.*?</style>", " ", payload, flags=re.IGNORECASE | re.DOTALL)
        payload = re.sub(r"<[^>]+>", " ", payload)
        return re.sub(r"\s+", " ", payload).strip()
