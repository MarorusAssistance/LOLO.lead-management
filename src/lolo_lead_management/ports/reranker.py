from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


FieldTarget = Literal[
    "company_name",
    "website",
    "country",
    "employee_estimate",
    "person_name",
    "role_title",
    "fit_signals",
    "multi",
]


@dataclass(frozen=True)
class RerankCandidate:
    id: str
    url: str
    field_target: FieldTarget | None
    text: str
    source_tier: str = "unknown"
    is_company_controlled_source: bool = False


@dataclass(frozen=True)
class RerankResult:
    id: str
    url: str
    score: float
    rank: int
    field_target: FieldTarget | None = None


class RerankerPort(ABC):
    @abstractmethod
    def rerank(self, *, query: str, candidates: list[RerankCandidate], top_k: int) -> list[RerankResult]:
        raise NotImplementedError
