from __future__ import annotations

from abc import ABC, abstractmethod

from lolo_lead_management.domain.models import AcceptedLeadRecord, SearchRunSnapshot, ShortlistRecord


class CrmWriterPort(ABC):
    @abstractmethod
    def upsert_accepted_lead(self, run: SearchRunSnapshot, lead: AcceptedLeadRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_shortlist(self, shortlist: ShortlistRecord) -> None:
        raise NotImplementedError
