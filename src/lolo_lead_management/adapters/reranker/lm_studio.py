from __future__ import annotations

import atexit
import json
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from lolo_lead_management.domain.errors import RerankerUnavailableError
from lolo_lead_management.ports.reranker import RerankCandidate, RerankerPort, RerankResult


class LmStudioAwareRerankerPort(RerankerPort):
    def __init__(
        self,
        *,
        model_key: str,
        model_path: str | None,
        lm_studio_base_url: str,
        engine_base_url: str,
        bootstrap_enabled: bool,
        runtime_cache_dir: str,
        timeout_seconds: int,
    ) -> None:
        self._model_key = model_key
        self._explicit_model_path = model_path
        self._lm_studio_base_url = self._normalize_base_url(lm_studio_base_url)
        self._engine_base_url = self._normalize_base_url(engine_base_url)
        self._bootstrap_enabled = bootstrap_enabled
        self._runtime_cache_dir = Path(runtime_cache_dir).expanduser()
        self._timeout_seconds = timeout_seconds
        self._sidecar_process: subprocess.Popen[str] | None = None
        self.last_call_trace: dict[str, Any] | None = None
        atexit.register(self._shutdown_sidecar)

    def rerank(self, *, query: str, candidates: list[RerankCandidate], top_k: int) -> list[RerankResult]:
        trace = {
            "reranker_provider_attempts": [],
            "lmstudio_probe_status": "not_attempted",
            "resolved_model_path": None,
            "sidecar_bootstrap_status": "not_needed",
            "rerank_query": query,
            "rerank_candidates_count": len(candidates),
            "rerank_top_results": [],
            "rerank_retry_performed": False,
            "reranker_failure_reason": None,
        }
        self.last_call_trace = trace
        if not candidates or top_k <= 0:
            return []

        lm_studio_results = self._try_lm_studio_rerank(query=query, candidates=candidates, top_k=top_k, trace=trace)
        if lm_studio_results is not None:
            trace["lmstudio_probe_status"] = "ok"
            trace["rerank_top_results"] = self._serialize_results(lm_studio_results)
            return lm_studio_results

        trace["rerank_retry_performed"] = True
        if not self._bootstrap_enabled:
            reason = "lm_studio_reranker_unavailable_and_bootstrap_disabled"
            trace["reranker_failure_reason"] = reason
            raise RerankerUnavailableError(reason)

        model_path = self._resolve_model_path()
        trace["resolved_model_path"] = str(model_path)
        sidecar_status = self._ensure_sidecar_ready(model_path)
        trace["sidecar_bootstrap_status"] = sidecar_status

        try:
            engine_results = self._post_rerank(
                base_url=self._engine_base_url,
                endpoints=self._endpoint_candidates(self._engine_base_url, default_endpoint="/reranking"),
                query=query,
                candidates=candidates,
                top_k=top_k,
                provider="engine_sidecar",
                trace=trace,
            )
        except Exception as exc:  # pragma: no cover - exercised through unit-level bootstrap tests
            reason = f"engine_managed_reranker_failed: {exc}"
            trace["reranker_failure_reason"] = reason
            raise RerankerUnavailableError(reason) from exc
        trace["rerank_top_results"] = self._serialize_results(engine_results)
        return engine_results

    def _try_lm_studio_rerank(
        self,
        *,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int,
        trace: dict[str, Any],
    ) -> list[RerankResult] | None:
        try:
            return self._post_rerank(
                base_url=self._lm_studio_base_url,
                endpoints=self._endpoint_candidates(self._lm_studio_base_url, default_endpoint="/v1/rerank"),
                query=query,
                candidates=candidates,
                top_k=top_k,
                provider="lm_studio",
                trace=trace,
            )
        except Exception as exc:
            trace["lmstudio_probe_status"] = str(exc)
            return None

    def _post_rerank(
        self,
        *,
        base_url: str,
        endpoints: list[str],
        query: str,
        candidates: list[RerankCandidate],
        top_k: int,
        provider: str,
        trace: dict[str, Any],
    ) -> list[RerankResult]:
        last_error: Exception | None = None
        payload = {
            "query": query,
            "documents": [item.text for item in candidates],
        }
        for endpoint in endpoints:
            url = base_url if endpoint == "__direct__" else f"{base_url}{endpoint}"
            attempt: dict[str, Any] = {"provider": provider, "url": url}
            try:
                raw = self._request_json(url=url, payload=payload)
                results = self._parse_rerank_response(raw, candidates=candidates, top_k=top_k)
                attempt["status"] = "ok"
                attempt["result_count"] = len(results)
                trace["reranker_provider_attempts"].append(attempt)
                return results
            except Exception as exc:
                attempt["status"] = "error"
                attempt["error"] = str(exc)
                trace["reranker_provider_attempts"].append(attempt)
                last_error = exc
        raise last_error or RerankerUnavailableError(f"{provider}_rerank_failed")

    def _request_json(self, *, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=encoded, method="POST", headers={"Content-Type": "application/json"})
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RerankerUnavailableError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
        except error.URLError as exc:
            raise RerankerUnavailableError(str(exc.reason or exc)) from exc

    def _parse_rerank_response(
        self,
        raw: dict[str, Any],
        *,
        candidates: list[RerankCandidate],
        top_k: int,
    ) -> list[RerankResult]:
        items = raw.get("results") or raw.get("data") or raw.get("reranked_documents") or []
        if isinstance(items, list) and items:
            parsed: list[RerankResult] = []
            for rank, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                if index is None:
                    index = item.get("document_index")
                if index is None and "id" in item:
                    candidate = next((entry for entry in candidates if entry.id == str(item["id"])), None)
                else:
                    candidate = candidates[int(index)] if isinstance(index, int) and 0 <= index < len(candidates) else None
                if candidate is None:
                    continue
                score = item.get("relevance_score")
                if score is None:
                    score = item.get("score")
                if score is None and "relevance" in item:
                    score = item["relevance"]
                parsed.append(
                    RerankResult(
                        id=candidate.id,
                        url=candidate.url,
                        score=float(score if score is not None else 0),
                        rank=rank,
                        field_target=candidate.field_target,
                    )
                )
            if parsed:
                parsed.sort(key=lambda item: item.score, reverse=True)
                return [item for item in parsed[:top_k]]
        scores = raw.get("scores")
        if isinstance(scores, list):
            parsed_scores = [
                RerankResult(
                    id=candidate.id,
                    url=candidate.url,
                    score=float(score),
                    rank=index + 1,
                    field_target=candidate.field_target,
                )
                for index, (candidate, score) in enumerate(zip(candidates, scores, strict=False))
            ]
            parsed_scores.sort(key=lambda item: item.score, reverse=True)
            return parsed_scores[:top_k]
        raise RerankerUnavailableError("rerank_response_shape_unrecognized")

    def _resolve_model_path(self) -> Path:
        if self._explicit_model_path:
            candidate = Path(self._explicit_model_path).expanduser()
            if candidate.is_file():
                return candidate
            raise RerankerUnavailableError(f"configured_reranker_model_missing: {candidate}")
        normalized_key = self._normalize_token(self._model_key)
        key_tokens = [
            self._normalize_token(token)
            for token in re.split(r"[^a-zA-Z0-9]+", self._model_key.lower())
            if token and token not in {"text", "embedding", "model"}
        ]
        roots = [
            Path.home() / ".lmstudio" / "models",
        ]
        matches: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for item in root.rglob("*.gguf"):
                normalized_name = self._normalize_token(item.name)
                if normalized_key in normalized_name or (
                    key_tokens and all(token in normalized_name for token in key_tokens)
                ):
                    matches.append(item)
        if matches:
            matches.sort(key=lambda item: (len(item.name), str(item)))
            return matches[0]
        raise RerankerUnavailableError(f"reranker_model_not_found_for_key={self._model_key}")

    def _ensure_sidecar_ready(self, model_path: Path) -> str:
        if self._healthcheck(self._engine_base_url):
            return "already_running"
        binary = self._ensure_runtime_binary()
        parsed = parse.urlparse(self._engine_base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8081
        api_prefix = parsed.path.rstrip("/")
        self._runtime_cache_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._runtime_cache_dir / "llama-server-reranker.log"
        log_handle = log_path.open("a", encoding="utf-8")
        command = [
            str(binary),
            "-m",
            str(model_path),
            "--host",
            host,
            "--port",
            str(port),
            "-b",
            "4096",
            "-ub",
            "4096",
            "--embedding",
            "--pooling",
            "rank",
            "--reranking",
            "--no-webui",
        ]
        if api_prefix:
            command.extend(["--api-prefix", api_prefix])
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._sidecar_process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            if self._sidecar_process.poll() is not None:
                tail = self._read_log_tail(log_path)
                raise RerankerUnavailableError(f"reranker_sidecar_exited_early: {tail}")
            if self._healthcheck(self._engine_base_url):
                return "spawned"
            time.sleep(0.5)
        tail = self._read_log_tail(log_path)
        raise RerankerUnavailableError(f"reranker_sidecar_healthcheck_timeout: {tail}")

    def _ensure_runtime_binary(self) -> Path:
        self._runtime_cache_dir.mkdir(parents=True, exist_ok=True)
        for item in self._runtime_cache_dir.rglob("llama-server.exe"):
            if item.is_file():
                return item
        download_url = self._resolve_windows_cpu_asset_url()
        asset_name = download_url.rsplit("/", 1)[-1]
        zip_path = self._runtime_cache_dir / asset_name
        extract_dir = self._runtime_cache_dir / asset_name.removesuffix(".zip")
        if not zip_path.exists():
            self._download_file(download_url, zip_path)
        if not extract_dir.exists():
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(extract_dir)
        for item in extract_dir.rglob("llama-server.exe"):
            if item.is_file():
                return item
        raise RerankerUnavailableError(f"llama_server_executable_not_found_in_archive={zip_path}")

    def _resolve_windows_cpu_asset_url(self) -> str:
        latest_url = "https://github.com/ggml-org/llama.cpp/releases/latest"
        req = request.Request(latest_url, headers={"User-Agent": "LOLO Lead Management"})
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception as exc:  # pragma: no cover - network failure path
            raise RerankerUnavailableError(f"llama_cpp_release_probe_failed: {exc}") from exc
        match = re.search(
            r'href="(?P<path>(?:https://github\.com)?/ggml-org/llama\.cpp/releases/download/[^"]*llama-[^"]*-bin-win-cpu-x64\.zip|https://github\.com/ggml-org/llama\.cpp/releases/download/[^"]*llama-[^"]*-bin-win-cpu-x64\.zip)"',
            html,
        )
        if not match:
            raise RerankerUnavailableError("llama_cpp_windows_cpu_asset_not_found")
        asset_url = match.group("path")
        if asset_url.startswith("http"):
            return asset_url
        return f"https://github.com{asset_url}"

    def _download_file(self, download_url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        req = request.Request(download_url, headers={"User-Agent": "LOLO Lead Management"})
        try:
            with request.urlopen(req, timeout=max(self._timeout_seconds, 60)) as response:
                destination.write_bytes(response.read())
        except Exception as exc:  # pragma: no cover - network failure path
            raise RerankerUnavailableError(f"reranker_runtime_download_failed: {exc}") from exc

    def _healthcheck(self, base_url: str) -> bool:
        for path in ("/health", "/v1/health"):
            url = f"{base_url}{path}"
            req = request.Request(url, method="GET")
            try:
                with request.urlopen(req, timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                continue
            if payload.get("status") == "ok":
                return True
        return False

    def _endpoint_candidates(self, base_url: str, *, default_endpoint: str) -> list[str]:
        parsed = parse.urlparse(base_url)
        if parsed.path and parsed.path not in {"", "/"}:
            return ["__direct__"]
        if default_endpoint == "/reranking":
            return ["/reranking"]
        return ["/v1/rerank", "/v1/reranking", "/reranking"]

    def _shutdown_sidecar(self) -> None:
        if self._sidecar_process is None:
            return
        if self._sidecar_process.poll() is not None:
            return
        try:
            self._sidecar_process.terminate()
            self._sidecar_process.wait(timeout=5)
        except Exception:  # pragma: no cover - cleanup path
            try:
                self._sidecar_process.kill()
            except Exception:
                pass

    def _read_log_tail(self, log_path: Path, *, tail_chars: int = 1600) -> str:
        if not log_path.exists():
            return ""
        try:
            data = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # pragma: no cover - defensive
            return ""
        return data[-tail_chars:]

    def _serialize_results(self, results: list[RerankResult]) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "url": item.url,
                "score": item.score,
                "rank": item.rank,
                "field_target": item.field_target,
            }
            for item in results
        ]

    def _normalize_base_url(self, value: str) -> str:
        return value.rstrip("/")

    def _normalize_token(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())
