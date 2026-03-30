from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from lolo_lead_management.domain.errors import InvalidAgentOutputError
from lolo_lead_management.ports.llm import LlmPort

from .specs import StageAgentSpec

T = TypeVar("T", bound=BaseModel)


class StageAgentExecutor:
    def __init__(self, llm_port: LlmPort | None) -> None:
        self._llm_port = llm_port

    def generate_structured(
        self,
        *,
        spec: StageAgentSpec,
        payload: dict[str, Any],
        output_model: type[T],
    ) -> T | None:
        if not spec.uses_llm or self._llm_port is None:
            return None

        raw = self._llm_port.generate_json(
            agent_name=spec.role_name,
            system_prompt=spec.system_prompt,
            input_payload=payload,
            schema=output_model.model_json_schema(),
        )
        try:
            return output_model.model_validate(raw)
        except Exception as exc:  # pragma: no cover - defensive path
            raise InvalidAgentOutputError(f"{spec.role_name} returned invalid JSON: {exc}") from exc
