from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from lolo_lead_management.domain.errors import InvalidAgentOutputError
from lolo_lead_management.ports.llm import LlmPort


class LmStudioLlmPort(LlmPort):
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_completion_tokens: int | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._max_completion_tokens = max_completion_tokens
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = timeout_seconds

    def generate_json(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        input_payload: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_name = f"{agent_name.lower()}_response"
        payload = {
            "model": self._model,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                },
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "agent": agent_name,
                            "input": input_payload,
                            "instructions": "Return only valid JSON matching the schema.",
                        }
                    ),
                },
            ],
        }
        if self._max_completion_tokens is not None:
            payload["max_completion_tokens"] = self._max_completion_tokens
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = request.Request(self._base_url, method="POST", data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:  # pragma: no cover - network failure path
            detail = exc.read().decode("utf-8", errors="ignore")
            raise InvalidAgentOutputError(f"LLM endpoint returned HTTP {exc.code}: {detail}") from exc

        content = raw["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvalidAgentOutputError(f"LM Studio returned malformed JSON: {exc}") from exc
