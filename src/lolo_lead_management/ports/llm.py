from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LlmPort(ABC):
    @abstractmethod
    def generate_json(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        input_payload: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
