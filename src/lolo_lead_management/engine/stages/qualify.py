from __future__ import annotations

from lolo_lead_management.domain.enums import StageName
from lolo_lead_management.domain.models import AssembledLeadDossier, NormalizedLeadSearchRequest, QualificationDecision
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import evaluate_dossier, merge_qualification_decisions


class QualifyStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor

    def execute(self, *, request_payload: dict, dossier_payload: dict) -> QualificationDecision:
        request = NormalizedLeadSearchRequest.model_validate(request_payload)
        dossier = AssembledLeadDossier.model_validate(dossier_payload)
        deterministic = evaluate_dossier(dossier, request)
        try:
            generated = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.QUALIFY],
                payload={
                    "request": request_payload,
                    "assembled_dossier": self._compact_dossier_payload(dossier_payload),
                    "deterministic_decision": deterministic.model_dump(mode="json"),
                },
                output_model=QualificationDecision,
            )
        except Exception:
            generated = None
        return merge_qualification_decisions(deterministic, generated)

    def _compact_dossier_payload(self, payload: dict) -> dict:
        compact = dict(payload)
        compact["evidence"] = [self._compact_evidence_item(item) for item in payload.get("evidence", [])[:6]]
        compact["field_evidence"] = [
            {
                **item,
                "supporting_evidence": [self._compact_evidence_item(doc) for doc in item.get("supporting_evidence", [])[:3]],
                "contradicting_evidence": [self._compact_evidence_item(doc) for doc in item.get("contradicting_evidence", [])[:2]],
            }
            for item in payload.get("field_evidence", [])
        ]
        return compact

    def _compact_evidence_item(self, payload: dict) -> dict:
        compact = dict(payload)
        compact["snippet"] = (compact.get("snippet") or "")[:400]
        compact["raw_content"] = (compact.get("raw_content") or "")[:1800]
        return compact
