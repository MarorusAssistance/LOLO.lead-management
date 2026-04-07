from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from lolo_lead_management.domain.errors import InvalidAgentOutputError
from lolo_lead_management.ports.llm import LlmPort

from .specs import StageAgentSpec

T = TypeVar("T", bound=BaseModel)


@dataclass
class StructuredGenerationAttempt:
    raw: dict[str, Any] | None
    parsed: BaseModel | None
    error: str | None = None


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

    def generate_structured_attempt(
        self,
        *,
        spec: StageAgentSpec,
        payload: dict[str, Any],
        output_model: type[T],
    ) -> StructuredGenerationAttempt:
        if not spec.uses_llm or self._llm_port is None:
            return StructuredGenerationAttempt(raw=None, parsed=None, error="llm_disabled")

        try:
            raw = self._llm_port.generate_json(
                agent_name=spec.role_name,
                system_prompt=spec.system_prompt,
                input_payload=payload,
                schema=output_model.model_json_schema(),
            )
        except Exception as exc:
            return StructuredGenerationAttempt(raw=None, parsed=None, error=str(exc))
        try:
            parsed = output_model.model_validate(raw)
        except Exception as exc:
            return StructuredGenerationAttempt(raw=raw, parsed=None, error=f"{spec.role_name} returned invalid JSON: {exc}")
        return StructuredGenerationAttempt(raw=raw, parsed=parsed)
