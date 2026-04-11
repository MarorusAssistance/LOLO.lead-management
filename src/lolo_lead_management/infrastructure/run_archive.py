from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return normalized or "artifact"


class ExecutionArchiveWriter:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def write(self, *, kind: str, payload: dict[str, Any]) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = str(payload.get("run_id") or payload.get("response", {}).get("run_id") or "no-run")
        filename = f"{timestamp}_{_slugify(kind)}_{_slugify(run_id)}.json"
        path = self._base_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_text(self, *, kind: str, run_id: str, slug: str, text: str, extension: str = "md") -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{timestamp}_{_slugify(kind)}_{_slugify(run_id)}_{_slugify(slug)}.{extension.lstrip('.')}"
        path = self._base_dir / filename
        path.write_text(text, encoding="utf-8")
        return path
