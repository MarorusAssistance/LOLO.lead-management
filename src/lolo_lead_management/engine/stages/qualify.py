from __future__ import annotations

from lolo_lead_management.domain.enums import StageName
from lolo_lead_management.domain.models import NormalizedLeadSearchRequest, QualificationDecision, SourcingDossier
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import evaluate_dossier, merge_qualification_decisions


class QualifyStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor

    def execute(self, *, request_payload: dict, dossier_payload: dict) -> QualificationDecision:
        request = NormalizedLeadSearchRequest.model_validate(request_payload)
        dossier = SourcingDossier.model_validate(dossier_payload)
        deterministic = evaluate_dossier(dossier, request)
        try:
            generated = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.QUALIFY],
                payload={
                    "request": request_payload,
                    "dossier": dossier_payload,
                    "deterministic_decision": deterministic.model_dump(mode="json"),
                },
                output_model=QualificationDecision,
            )
        except Exception:
            generated = None
        return merge_qualification_decisions(deterministic, generated)
