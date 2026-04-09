from __future__ import annotations

from pydantic import Field

from lolo_lead_management.domain.models import (
    AssembledLeadDossier,
    CompanyFocusResolution,
    CommercialBundle,
    ContinueTrace,
    ExplorationMemoryState,
    QualificationTrace,
    QualificationDecision,
    SourceStageTrace,
    SourcePassResult,
    SearchRunSnapshot,
    StageDecision,
    StrictModel,
)


class EngineRuntimeState(StrictModel):
    run: SearchRunSnapshot
    memory: ExplorationMemoryState = Field(default_factory=ExplorationMemoryState)
    environment: str = "production"
    current_decision: StageDecision | None = None
    current_source_result: SourcePassResult | None = None
    current_source_trace: SourceStageTrace | None = None
    current_enrich_trace: SourceStageTrace | None = None
    current_assembler_trace: dict | None = None
    current_dossier: AssembledLeadDossier | None = None
    current_qualification: QualificationDecision | None = None
    current_qualification_trace: QualificationTrace | None = None
    current_commercial: CommercialBundle | None = None
    current_continue_trace: ContinueTrace | None = None
    current_query: str | None = None
    current_focus_company_resolution: CompanyFocusResolution | None = None
    current_discovery_source_trace: SourceStageTrace | None = None
    current_anchored_source_trace: SourceStageTrace | None = None
    focus_company_locked: bool = False
    pending_discovery_documents: list = Field(default_factory=list)
    pending_discovery_traces: list[SourceStageTrace] = Field(default_factory=list)
    pending_discovery_research_trace: list = Field(default_factory=list)
    pending_discovery_queries: list = Field(default_factory=list)
    discovery_attempts_for_current_pass: int = Field(default=0, ge=0)
    discovery_directories_consumed_in_run: list[str] = Field(default_factory=list)
    discovery_ladder_exhausted_in_run: bool = False
    visited_urls_run_scoped: list[str] = Field(default_factory=list)
    should_continue: bool = True
