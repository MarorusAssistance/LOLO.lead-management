from __future__ import annotations

import shutil
from pathlib import Path
import re

from lolo_lead_management.adapters.crm.sqlite import SqliteCrmWriter
from lolo_lead_management.adapters.search.fake import FakeSearchPort
from lolo_lead_management.adapters.stores.sqlite import (
    SqliteExplorationMemoryStore,
    SqliteLeadStore,
    SqliteSearchRunStore,
    SqliteShortlistStore,
)
from lolo_lead_management.application.container import ServiceContainer
from lolo_lead_management.config.settings import Settings
from lolo_lead_management.domain.models import EvidenceDocument
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.main import LeadManagementEngine
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.continue_or_finish import ContinueOrFinishStage
from lolo_lead_management.engine.stages.crm_write import CrmWriteStage
from lolo_lead_management.engine.stages.draft import DraftStage
from lolo_lead_management.engine.stages.chunkerize import ChunkerizeStage
from lolo_lead_management.engine.stages.enrich import EnrichStage
from lolo_lead_management.engine.stages.load_state import LoadStateStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.stages.plan import PlanStage
from lolo_lead_management.engine.stages.qualify import QualifyStage
from lolo_lead_management.engine.stages.source import SourceStage
from lolo_lead_management.infrastructure.run_archive import ExecutionArchiveWriter
from lolo_lead_management.infrastructure.sqlite import SqliteDatabase


def workspace_tmp_dir(name: str) -> Path:
    base = Path("test-output") / name
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    return base


class FixtureLeadLlmPort:
    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, schema)
        mode = input_payload.get("mode")
        if mode in {"discovery_focus_document_mode", "discovery_focus_chunk_mode"}:
            text = input_payload.get("document_text") or ((input_payload.get("chunk") or {}).get("text") or "")
            company_name = self._extract_company_name(text, (input_payload.get("document") or {}).get("title") or "")
            if not company_name:
                return {"selected_company": None, "selection_mode": "none", "notes": ["fixture_no_candidate"]}
            query_name = self._derive_query_name(company_name)
            return {
                "selected_company": company_name,
                "legal_name": company_name,
                "query_name": query_name,
                "brand_aliases": [company_name],
                "candidate_website": self._extract_website(text),
                "country_code": self._extract_country(text),
                "employee_count_hint_value": self._extract_employee_count(text),
                "employee_count_hint_type": "exact" if self._extract_employee_count(text) is not None else "unknown",
                "selection_mode": "confident",
                "confidence": 0.85,
                "evidence_urls": [(input_payload.get("document") or {}).get("url")],
                "selection_reasons": ["fixture_candidate"],
                "hard_rejections": [],
                "notes": ["fixture_candidate"],
            }
        if mode == "discovery_focus_consolidation_mode":
            for item in input_payload.get("extracted_focuses", []):
                resolution = item.get("resolution") or {}
                if resolution.get("selected_company"):
                    return {
                        "selected_company": resolution.get("selected_company"),
                        "legal_name": resolution.get("legal_name"),
                        "query_name": resolution.get("query_name"),
                        "brand_aliases": resolution.get("brand_aliases", []),
                        "candidate_website": resolution.get("candidate_website"),
                        "country_code": resolution.get("country_code"),
                        "employee_count_hint_value": resolution.get("employee_count_hint_value"),
                        "employee_count_hint_type": resolution.get("employee_count_hint_type", "unknown"),
                        "selection_mode": "confident",
                        "confidence": 0.9,
                        "evidence_urls": resolution.get("evidence_urls", []),
                        "selection_reasons": ["fixture_consolidated_focus"],
                        "hard_rejections": [],
                        "notes": ["fixture_focus_consolidation"],
                    }
            return {"selected_company": None, "selection_mode": "none", "notes": ["fixture_no_focus_candidates"]}
        if mode in {"focus_locked_document_mode", "focus_locked_chunk_mode"}:
            text = input_payload.get("document_text") or ((input_payload.get("chunk") or {}).get("text") or "")
            company_name = input_payload.get("focus_company") or self._extract_company_name(text, (input_payload.get("document") or {}).get("title") or "")
            country = self._extract_country(text)
            employee_count = self._extract_employee_count(text)
            website = self._extract_website(text)
            person_name = self._extract_labeled(text, "Person")
            role_title = self._extract_labeled(text, "Role")
            field_assertions: list[dict] = []
            if company_name:
                field_assertions.append(
                    {
                        "field_name": "company_name",
                        "company_name": company_name,
                        "value": company_name,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "fixture company",
                    }
                )
            if website:
                field_assertions.append(
                    {
                        "field_name": "website",
                        "company_name": company_name,
                        "value": website,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "fixture website",
                    }
                )
            if country:
                field_assertions.append(
                    {
                        "field_name": "country",
                        "company_name": company_name,
                        "value": country,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "fixture country",
                    }
                )
            if employee_count is not None:
                field_assertions.append(
                    {
                        "field_name": "employee_estimate",
                        "company_name": company_name,
                        "value": employee_count,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "fixture employee count",
                        "employee_count_type": "exact",
                    }
                )
            contact_assertions: list[dict] = []
            if company_name and person_name and role_title:
                contact_assertions.append(
                    {
                        "person_name": person_name,
                        "role_title": role_title,
                        "company_name": company_name,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "fixture contact",
                    }
                )
            return {
                "segment_company_name": company_name,
                "field_assertions": field_assertions,
                "contact_assertions": contact_assertions,
                "fit_signals": self._extract_theme_tags(text),
                "contradictions": [],
                "notes": ["fixture_assembly"],
            }
        return {}

    def _extract_company_name(self, text: str, title: str) -> str | None:
        labeled = self._extract_labeled(text, "Company")
        if labeled:
            return labeled
        title_primary = re.split(r"\s+-\s+", title or "", maxsplit=1)[0].strip(" |")
        return title_primary or None

    def _derive_query_name(self, company_name: str) -> str:
        tokens = [
            token
            for token in re.split(r"\s+", company_name)
            if token.upper() not in {"SL", "S.L.", "SA", "S.A.", "SOCIEDAD", "LIMITADA", "INC", "LLC", "SPAIN"}
        ]
        query_name = " ".join(tokens[:3]) or company_name
        if len(tokens) == 1 and query_name.isupper() and len(query_name) > 3:
            return query_name.title()
        return query_name

    def _extract_country(self, text: str) -> str | None:
        lowered = text.lower()
        if "country: spain" in lowered or "spain" in lowered or "espana" in lowered or "españa" in lowered:
            return "es"
        return None

    def _extract_employee_count(self, text: str) -> int | None:
        match = re.search(r"Employees:\s*(\d+)", text, re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _extract_website(self, text: str) -> str | None:
        match = re.search(r"https?://[^\s|)]+", text)
        return match.group(0) if match else None

    def _extract_labeled(self, text: str, label: str) -> str | None:
        match = re.search(
            rf"{label}:\s*(.+?)(?=\b(?:Company|Country|Employees|Person|Role|Website):|$)",
            text,
            re.IGNORECASE,
        )
        return match.group(1).strip(" |") if match else None

    def _extract_theme_tags(self, text: str) -> list[str]:
        lowered = text.lower()
        tags: list[str] = []
        if "software" in lowered or "saas" in lowered:
            tags.append("software")
        if any(token in lowered for token in {"genai", " ai", "ia", "artificial intelligence", "inteligencia artificial"}):
            tags.append("ia")
        if "automation" in lowered or "automat" in lowered:
            tags.append("automation")
        if "data" in lowered or "datos" in lowered:
            tags.append("data")
        return tags


def build_test_container(
    tmp_path: Path,
    *,
    search_index: dict[str, list[EvidenceDocument]] | None = None,
    pages: dict[str, str] | None = None,
    llm_port=None,
) -> ServiceContainer:
    settings = Settings(database_path=str(tmp_path / "lead_management.sqlite3"))
    database = SqliteDatabase(settings.database_path)
    lead_store = SqliteLeadStore(database)
    run_store = SqliteSearchRunStore(database)
    shortlist_store = SqliteShortlistStore(database)
    memory_store = SqliteExplorationMemoryStore(database)
    crm_writer = SqliteCrmWriter(database)
    archive_writer = ExecutionArchiveWriter(str(tmp_path / "execution-results"))
    search_port = FakeSearchPort(search_index=search_index, pages=pages)
    agent_executor = StageAgentExecutor(llm_port)
    engine = LeadManagementEngine(
        normalize_stage=NormalizeStage(agent_executor),
        load_state_stage=LoadStateStage(memory_store),
        plan_stage=PlanStage(agent_executor),
        source_stage=SourceStage(search_port=search_port, agent_executor=agent_executor, max_results=5),
        chunkerize_stage=ChunkerizeStage(archive_writer=archive_writer),
        assemble_stage=AssembleStage(agent_executor),
        qualify_stage=QualifyStage(agent_executor),
        enrich_stage=EnrichStage(search_port=search_port, agent_executor=agent_executor, max_results=5),
        draft_stage=DraftStage(agent_executor),
        crm_write_stage=CrmWriteStage(
            lead_store=lead_store,
            run_store=run_store,
            shortlist_store=shortlist_store,
            memory_store=memory_store,
            crm_writer=crm_writer,
            shortlist_size=5,
        ),
        continue_stage=ContinueOrFinishStage(run_store=run_store, memory_store=memory_store),
        run_store=run_store,
        shortlist_store=shortlist_store,
        search_call_budget=10,
        source_attempt_budget=6,
        enrich_attempt_budget=1,
        archive_writer=archive_writer,
    )
    return ServiceContainer(
        settings=settings,
        database=database,
        engine=engine,
        llm_port=llm_port,
        search_port=search_port,
        lead_store=lead_store,
        run_store=run_store,
        shortlist_store=shortlist_store,
        memory_store=memory_store,
        crm_writer=crm_writer,
        archive_writer=archive_writer,
    )


def accepted_candidate_fixture() -> tuple[dict[str, list[EvidenceDocument]], dict[str, str]]:
    queries = [
        "empresite empresa IA software espana cif",
        "infoempresa empresa IA software espana razon social",
        "datoscif empresa IA software espana cif",
        "camara censo empresa IA software espana actividad",
        "empresite Madrid empresa IA software cif",
        'empresite "Acme AI" sitio web pagina web',
        'infoempresa "Acme AI" razon social cif',
        'datoscif "Acme AI" sitio web pagina web',
        'iberinform "Acme AI" sitio web pagina web',
        "acme.ai contacto aviso legal cif",
        '"Acme AI" administradores cargos directivos',
        '"Acme AI" numero empleados exactos',
    ]
    results = [
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
            EvidenceDocument(
                url="https://acme.ai/blog/agentic-workflows",
                title="Acme AI on agentic workflows",
                snippet="Company: Acme AI | Country: Spain | Employees: 25 | GenAI automation",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nAutomation and GenAI workflows for IT teams",
            ),
        ]
    search_index = {query: results for query in queries}
    pages = {
        "https://acme.ai/about": "Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
        "https://acme.ai/blog/agentic-workflows": "Company: Acme AI\nCountry: Spain\nEmployees: 25\nAutomation and GenAI workflows for IT teams",
    }
    return search_index, pages


def close_match_candidate_fixture() -> tuple[dict[str, list[EvidenceDocument]], dict[str, str]]:
    queries = [
        "empresite empresa IA software espana cif",
        "infoempresa empresa IA software espana razon social",
        "datoscif empresa IA software espana cif",
        "camara censo empresa IA software espana actividad",
        "empresite Madrid empresa IA software cif",
        'empresite "Bravo Dev" sitio web pagina web',
        'infoempresa "Bravo Dev" razon social cif',
        'datoscif "Bravo Dev" sitio web pagina web',
        'iberinform "Bravo Dev" sitio web pagina web',
        "bravo.dev contacto aviso legal cif",
        '"Bravo Dev" administradores cargos directivos',
        '"Bravo Dev" numero empleados exactos',
    ]
    results = [
            EvidenceDocument(
                url="https://bravo.dev/team",
                title="Bravo Dev engineering team",
                snippet="Company: Bravo Dev | Person: Marta Diaz | Role: Engineering Manager | Country: Spain",
                source_type="fixture",
                raw_content="Company: Bravo Dev\nCountry: Spain\nEmployees: 30\nPerson: Marta Diaz\nRole: Engineering Manager\nGenAI automation engineering",
            ),
            EvidenceDocument(
                url="https://bravo.dev/blog/genai",
                title="Bravo Dev exploring GenAI automation",
                snippet="Company: Bravo Dev | Country: Spain | Employees: 30 | GenAI automation",
                source_type="fixture",
                raw_content="Company: Bravo Dev\nCountry: Spain\nEmployees: 30\nAutomation and GenAI workflows for engineering teams",
            ),
        ]
    search_index = {query: results for query in queries}
    pages = {
        "https://bravo.dev/team": "Company: Bravo Dev\nCountry: Spain\nEmployees: 30\nPerson: Marta Diaz\nRole: Engineering Manager\nGenAI automation engineering",
        "https://bravo.dev/blog/genai": "Company: Bravo Dev\nCountry: Spain\nEmployees: 30\nAutomation and GenAI workflows for engineering teams",
    }
    return search_index, pages
