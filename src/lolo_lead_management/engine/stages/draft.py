from __future__ import annotations

from lolo_lead_management.domain.enums import StageName
from lolo_lead_management.domain.models import CommercialBundle, NormalizedLeadSearchRequest, QualificationDecision, SourcingDossier
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import build_fallback_commercial_bundle


class DraftStage:
    def __init__(self, agent_executor: StageAgentExecutor) -> None:
        self._agent_executor = agent_executor

    def execute(self, *, request_payload: dict, dossier_payload: dict, qualification_payload: dict) -> CommercialBundle:
        request = NormalizedLeadSearchRequest.model_validate(request_payload)
        dossier = SourcingDossier.model_validate(dossier_payload)
        qualification = QualificationDecision.model_validate(qualification_payload)
        dossier = self._sanitize_dossier(dossier)
        if dossier.person is None:
            return build_fallback_commercial_bundle(dossier, qualification, request)

        try:
            generated = self._agent_executor.generate_structured(
                spec=STAGE_AGENT_SPECS[StageName.DRAFT],
                payload={
                    "request": request.model_dump(mode="json"),
                    "dossier": dossier.model_dump(mode="json"),
                    "qualification": qualification.model_dump(mode="json"),
                },
                output_model=CommercialBundle,
            )
        except Exception:
            generated = None
        if generated is not None:
            return generated
        return build_fallback_commercial_bundle(dossier, qualification, request)

    def _sanitize_dossier(self, dossier: SourcingDossier) -> SourcingDossier:
        if dossier.person is None:
            return dossier
        full_name = " ".join((dossier.person.full_name or "").split()).strip()
        if not full_name or len(full_name) > 80 or len(full_name.split()) > 6 or "industries:" in full_name.lower():
            dossier = dossier.model_copy(deep=True)
            dossier.person = None
        return dossier
