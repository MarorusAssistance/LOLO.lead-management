from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from lolo_lead_management.domain.enums import StageName


@dataclass(frozen=True)
class StageAgentSpec:
    stage_name: StageName
    role_name: str
    prompt_file: str
    uses_llm: bool

    @property
    def system_prompt(self) -> str:
        prompt_path = files("lolo_lead_management.engine.agents").joinpath("prompts", self.prompt_file)
        return prompt_path.read_text(encoding="utf-8").strip()


STAGE_AGENT_SPECS: dict[StageName, StageAgentSpec] = {
    StageName.NORMALIZE: StageAgentSpec(StageName.NORMALIZE, "NormalizerAgent", "normalizer.md", True),
    StageName.LOAD_STATE: StageAgentSpec(StageName.LOAD_STATE, "StateLoaderAgent", "state_loader.md", False),
    StageName.PLAN: StageAgentSpec(StageName.PLAN, "PlannerAgent", "planner.md", True),
    StageName.SOURCE: StageAgentSpec(StageName.SOURCE, "SourcerAgent", "sourcer.md", True),
    StageName.QUALIFY: StageAgentSpec(StageName.QUALIFY, "QualifierAgent", "qualifier.md", True),
    StageName.ENRICH: StageAgentSpec(StageName.ENRICH, "EnrichmentAgent", "enricher.md", True),
    StageName.REQUALIFY: StageAgentSpec(StageName.REQUALIFY, "QualifierAgent", "qualifier.md", True),
    StageName.DRAFT: StageAgentSpec(StageName.DRAFT, "CommercialAgent", "commercial.md", True),
    StageName.CRM_WRITE: StageAgentSpec(StageName.CRM_WRITE, "CrmAgent", "crm.md", False),
    StageName.CONTINUE_OR_FINISH: StageAgentSpec(StageName.CONTINUE_OR_FINISH, "RunControlAgent", "run_control.md", False),
}
