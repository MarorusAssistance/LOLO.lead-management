from __future__ import annotations

from abc import ABC, abstractmethod

from lolo_lead_management.domain.models import ExplorationMemoryState, SearchRunSnapshot, ShortlistRecord, SourcingDossier


class LeadStore(ABC):
    @abstractmethod
    def register_accepted_lead(self, run_id: str, payload: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def register_rejected_candidate(self, run_id: str, payload: dict) -> None:
        raise NotImplementedError


class SearchRunStore(ABC):
    @abstractmethod
    def get_run(self, run_id: str) -> SearchRunSnapshot | None:
        raise NotImplementedError

    @abstractmethod
    def save_run(self, run: SearchRunSnapshot) -> None:
        raise NotImplementedError

    @abstractmethod
    def register_source_trace(self, run_id: str, trace: SourcingDossier) -> None:
        raise NotImplementedError

    @abstractmethod
    def register_search_run_result(self, run: SearchRunSnapshot) -> None:
        raise NotImplementedError


class ShortlistStore(ABC):
    @abstractmethod
    def save_pending_shortlist(self, shortlist: ShortlistRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_pending_shortlist(self, shortlist_id: str) -> ShortlistRecord | None:
        raise NotImplementedError

    @abstractmethod
    def clear_pending_shortlist(self, shortlist_id: str) -> None:
        raise NotImplementedError


class ExplorationMemoryStore(ABC):
    @abstractmethod
    def get_campaign_state(self) -> ExplorationMemoryState:
        raise NotImplementedError

    @abstractmethod
    def save_campaign_state(self, state: ExplorationMemoryState) -> None:
        raise NotImplementedError

    @abstractmethod
    def reset_query_memory(self, reset_fields: list[str], *, include_registered_lead_names: bool) -> None:
        raise NotImplementedError
