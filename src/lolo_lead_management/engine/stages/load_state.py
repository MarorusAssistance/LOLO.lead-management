from __future__ import annotations

from lolo_lead_management.domain.models import SearchRunSnapshot
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.stores import ExplorationMemoryStore


class LoadStateStage:
    def __init__(self, memory_store: ExplorationMemoryStore, *, environment: str = "production") -> None:
        self._memory_store = memory_store
        self._environment = environment

    def execute(self, run: SearchRunSnapshot) -> EngineRuntimeState:
        return EngineRuntimeState(
            run=run,
            memory=self._memory_store.get_campaign_state(),
            environment=self._environment,
        )
