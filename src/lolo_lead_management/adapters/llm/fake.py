from __future__ import annotations

from typing import Any

from lolo_lead_management.ports.llm import LlmPort


class FakeLlmPort(LlmPort):
    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self._responses = responses or {}

    def generate_json(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        input_payload: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        _ = (system_prompt, input_payload, schema)
        return self._responses.get(agent_name, {})
