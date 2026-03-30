from __future__ import annotations

from lolo_lead_management.domain.enums import StageName
from lolo_lead_management.domain.models import LeadSearchStartRequest, NormalizedLeadSearchRequest
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import normalize_request_payload, repair_normalized_request


class NormalizeStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor

    def execute(self, payload: LeadSearchStartRequest) -> NormalizedLeadSearchRequest:
        try:
            generated = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.NORMALIZE],
                payload=payload.model_dump(mode="json"),
                output_model=NormalizedLeadSearchRequest,
            )
        except Exception:
            generated = None
        if generated is not None:
            return repair_normalized_request(
                generated,
                user_text=payload.user_text,
                request_id=payload.request_id,
                meta=payload.meta,
            )
        return normalize_request_payload(payload.user_text, payload.request_id, payload.meta)
