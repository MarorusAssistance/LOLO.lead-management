from __future__ import annotations

from lolo_lead_management.domain.enums import StageName
from lolo_lead_management.domain.models import AssembledLeadDossier, NormalizedLeadSearchRequest, QualificationDecision, QualificationTrace
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.agents.specs import STAGE_AGENT_SPECS
from lolo_lead_management.engine.rules import evaluate_dossier


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
            },
            output_model=QualificationDecision,
        )
        generated = attempt.parsed if isinstance(attempt.parsed, QualificationDecision) else None
        merged = generated if generated is not None else deterministic
        notes: list[str] = []
        if attempt.error:
            notes.append("llm_decision_unavailable_fallback_to_deterministic")
        else:
            notes.append("llm_first_decision_used" if generated is not None else "llm_review_empty")
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
        company = payload.get("company") or {}
        person = payload.get("person") or {}
        website_resolution = payload.get("website_resolution") or {}
        compact = {
            "sourcing_status": payload.get("sourcing_status"),
            "company": {
                "name": company.get("name"),
                "website": company.get("website"),
                "country_code": company.get("country_code"),
                "employee_estimate": company.get("employee_estimate"),
            },
            "person": {
                "full_name": person.get("full_name"),
                "role_title": person.get("role_title"),
            },
            "lead_source_type": payload.get("lead_source_type"),
            "person_confidence": payload.get("person_confidence"),
            "primary_person_source_url": payload.get("primary_person_source_url"),
            "fit_signals": payload.get("fit_signals", [])[:5],
            "contradictions": payload.get("contradictions", [])[:5],
            "notes": payload.get("notes", [])[-6:],
            "website_resolution": {
                "candidate_website": website_resolution.get("candidate_website"),
                "officiality": website_resolution.get("officiality"),
                "confidence": website_resolution.get("confidence"),
                "signals": website_resolution.get("signals", [])[:4],
                "risks": website_resolution.get("risks", [])[:4],
                "evidence_urls": website_resolution.get("evidence_urls", [])[:3],
            },
            "evidence": [self._compact_evidence_item(item) for item in payload.get("evidence", [])[:4]],
            "field_evidence": [
                {
                    "field_name": item.get("field_name"),
                    "value": item.get("value"),
                    "status": item.get("status"),
                    "support_type": item.get("support_type"),
                    "reasoning_note": (item.get("reasoning_note") or "")[:180],
                    "supporting_urls": [doc.get("url") for doc in item.get("supporting_evidence", [])[:2] if doc.get("url")],
                    "contradicting_urls": [doc.get("url") for doc in item.get("contradicting_evidence", [])[:1] if doc.get("url")],
                }
                for item in payload.get("field_evidence", [])[:8]
            ],
        }
        return compact

    def _compact_evidence_item(self, payload: dict) -> dict:
        return {
            "url": payload.get("url"),
            "title": (payload.get("title") or "")[:140],
            "snippet": (payload.get("snippet") or "")[:180],
            "raw_content": (payload.get("raw_content") or "")[:180],
            "source_type": payload.get("source_type"),
            "source_tier": payload.get("source_tier"),
            "source_quality": payload.get("source_quality"),
            "company_anchor": payload.get("company_anchor"),
            "is_company_controlled_source": payload.get("is_company_controlled_source"),
        }
