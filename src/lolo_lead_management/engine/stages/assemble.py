from __future__ import annotations

from lolo_lead_management.domain.enums import StageName, SourcingStatus
from lolo_lead_management.domain.models import AssembledLeadDossier
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import sanitize_assembled_dossier
from lolo_lead_management.engine.state import EngineRuntimeState


class AssembleStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor

    def execute(self, state: EngineRuntimeState) -> AssembledLeadDossier:
        source_result = state.current_source_result
        if source_result is None:
            return AssembledLeadDossier(sourcing_status=SourcingStatus.NO_CANDIDATE, notes=["no_source_result_to_assemble"])

        prior_dossier = state.current_dossier
        try:
            generated = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.ASSEMBLE],
                payload={
                    "request": state.run.request.model_dump(mode="json"),
                    "source_result": self._compact_source_result_payload(source_result.model_dump(mode="json")),
                    "prior_dossier": self._compact_dossier_payload(prior_dossier.model_dump(mode="json")) if prior_dossier else None,
                },
                output_model=AssembledLeadDossier,
            )
        except Exception:
            generated = None
        return sanitize_assembled_dossier(
            generated,
            request=state.run.request,
            source_result=source_result,
            prior_dossier=prior_dossier,
        )

    def _compact_source_result_payload(self, payload: dict) -> dict:
        compact = dict(payload)
        compact["documents"] = [self._compact_evidence_item(item) for item in payload.get("documents", [])[:6]]
        return compact

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
