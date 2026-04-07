from __future__ import annotations

from pydantic import Field

from lolo_lead_management.domain.models import (
    AssembledLeadDossier,
    CommercialBundle,
    ExplorationMemoryState,
    QualificationDecision,
    SourcePassResult,
    SearchRunSnapshot,
    StageDecision,
    StrictModel,
)


class EngineRuntimeState(StrictModel):
    run: SearchRunSnapshot
    memory: ExplorationMemoryState = Field(default_factory=ExplorationMemoryState)
    current_decision: StageDecision | None = None
    current_source_result: SourcePassResult | None = None
    current_assembler_trace: dict | None = None
    current_dossier: AssembledLeadDossier | None = None
    current_qualification: QualificationDecision | None = None
    current_commercial: CommercialBundle | None = None
    current_query: str | None = None
    should_continue: bool = True
