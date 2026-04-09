from __future__ import annotations

from lolo_lead_management.domain.enums import StageName
from lolo_lead_management.domain.models import AssembledLeadDossier, NormalizedLeadSearchRequest, QualificationDecision, QualificationTrace
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import evaluate_dossier, merge_qualification_decisions


class QualifyStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor
        self.last_trace: QualificationTrace | None = None

    def execute(self, *, request_payload: dict, dossier_payload: dict) -> QualificationDecision:
        request = NormalizedLeadSearchRequest.model_validate(request_payload)
        dossier = AssembledLeadDossier.model_validate(dossier_payload)
        deterministic = evaluate_dossier(dossier, request)
        attempt = self._agent_executor.generate_structured_attempt(
            spec=STAGE_AGENT_SPECS[StageName.QUALIFY],
            payload={
                "request": request_payload,
                "assembled_dossier": self._compact_dossier_payload(dossier_payload),
                "deterministic_decision": deterministic.model_dump(mode="json"),
            },
            output_model=QualificationDecision,
        )
        generated = attempt.parsed if isinstance(attempt.parsed, QualificationDecision) else None
        merged = merge_qualification_decisions(deterministic, generated)
        notes: list[str] = []
        if attempt.error:
            notes.append("llm_review_unavailable")
        else:
            notes.append("llm_review_available" if generated is not None else "llm_review_empty")
        if merged.outcome != deterministic.outcome:
            notes.append("llm_review_changed_outcome")
        else:
            notes.append("deterministic_outcome_preserved")
        self.last_trace = QualificationTrace(
            deterministic_decision=deterministic,
            llm_review=generated,
            llm_raw_output=attempt.raw if isinstance(attempt.raw, dict) else None,
            llm_error=attempt.error,
            merged_decision=merged,
            notes=notes,
        )
        return merged

    def _compact_dossier_payload(self, payload: dict) -> dict:
        compact = dict(payload)
        compact["website_resolution"] = payload.get("website_resolution")
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
