from lolo_lead_management.adapters.search.fake import FakeSearchPort
from lolo_lead_management.adapters.search.tavily import TavilySearchPort
from lolo_lead_management.adapters.stores.sqlite import SqliteExplorationMemoryStore
from lolo_lead_management.domain.enums import SourcingStatus
from lolo_lead_management.domain.models import CompanyFocusResolution, CompanyObservation, EvidenceDocument, ExplorationMemoryState, LeadSearchStartRequest, ResearchQuery, ResearchTraceEntry, SearchBudget, SearchRunSnapshot, SourcePassResult
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.rules import (
    build_research_query_plan,
    canonicalize_website,
    candidate_company_names_from_document,
    clean_company_name,
    company_name_matches_anchor,
    derive_anchor_query_name,
    derive_brand_aliases,
    domain_is_directory,
    domain_is_unofficial_website_host,
    enrich_document_metadata,
    extract_employee_size_hint,
    extracted_official_website_from_document,
    is_plausible_company_name,
    resolve_person_signal,
    select_anchor_company,
)
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.load_state import LoadStateStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.stages.source import SourceStage
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.infrastructure.sqlite import SqliteDatabase

from tests.helpers import accepted_candidate_fixture, workspace_tmp_dir


class FlakySearchPort:
    def __init__(self, search_index, failing_queries):
        self._search_index = search_index
        self._failing_queries = set(failing_queries)

    def web_search(self, query, *, max_results):
        if query.query in self._failing_queries:
            raise RuntimeError("HTTP Error 400: Bad Request")
        return self._search_index.get(query.query, [])[:max_results]

    def fetch_page(self, url: str) -> str:
        return ""

    def extract_pages(self, urls, *, extract_depth="advanced"):
        return []


class TrackingContentSearchPort(FakeSearchPort):
    def __init__(
        self,
        *,
        search_index: dict[str, list[EvidenceDocument]] | None = None,
        pages: dict[str, str] | None = None,
        extracted_pages: dict[str, str] | None = None,
    ) -> None:
        super().__init__(search_index=search_index, pages=pages)
        self._extracted_pages = extracted_pages or {}
        self.extract_calls: list[list[str]] = []
        self.fetch_calls: list[str] = []

    def fetch_page(self, url: str) -> str:
        self.fetch_calls.append(url)
        return self._pages.get(url, "")

    def extract_pages(self, urls: list[str], *, extract_depth: str = "advanced") -> list[EvidenceDocument]:
        _ = extract_depth
        self.extract_calls.append(urls[:])
        documents: list[EvidenceDocument] = []
        for url in urls:
            raw_content = self._extracted_pages.get(url, "")
            documents.append(
                EvidenceDocument(
                    url=url,
                    title="",
                    snippet=raw_content[:400],
                    source_type="tavily_extract",
                    raw_content=raw_content,
                )
            )
        return documents


def _lock_focus_company(state, source_stage: SourceStage, assemble_stage: AssembleStage):
    discovery_result = source_stage.execute(state)
    state.current_source_result = discovery_result
    focus_resolution = assemble_stage.select_focus_company(state)
    state.current_focus_company_resolution = focus_resolution
    state.focus_company_locked = bool(focus_resolution.selected_company)
    return discovery_result, focus_resolution


def test_sourcer_collects_documents_and_assembler_resolves_company() -> None:
    tmp_path = workspace_tmp_dir("sourcer")
    search_index, pages = accepted_candidate_fixture()
    search_port = FakeSearchPort(search_index=search_index, pages=pages)
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "sourcer.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    assemble_stage = AssembleStage(StageAgentExecutor(None))

    discovery_result, focus_resolution = _lock_focus_company(state, source_stage, assemble_stage)
    source_result = source_stage.execute(state)
    state.current_source_result = source_result
    dossier = assemble_stage.execute(state)

    assert discovery_result.sourcing_status.value == "FOUND"
    assert focus_resolution.selected_company == "Acme AI"
    assert focus_resolution.query_name == "Acme AI"
    assert source_result.sourcing_status.value == "FOUND"
    assert discovery_result.executed_queries[0].research_phase == "company_discovery"
    assert discovery_result.executed_queries[0].source_role == "entity_validation"
    assert discovery_result.executed_queries[0].expected_field == "company_name"
    assert discovery_result.executed_queries[0].source_tier_target == "tier_b"
    assert discovery_result.executed_queries[0].preferred_domains == ["empresite.eleconomista.es"]
    assert discovery_result.source_trace is not None
    assert discovery_result.source_trace.llm_plan_input is not None
    assert discovery_result.source_trace.sanitized_query_plan is not None
    assert discovery_result.source_trace.merged_query_plan is not None
    assert discovery_result.source_trace.documents_passed_to_assembler
    assert discovery_result.source_trace.batch_traces
    assert discovery_result.source_trace.query_traces[0].raw_results_before_filter
    assert discovery_result.source_trace.query_traces[0].documents_after_enrichment
    assert discovery_result.source_trace.query_traces[0].documents_selected_for_pass
    assert len(source_result.documents) >= 2
    assert dossier.sourcing_status.value == "FOUND"
    assert dossier.company is not None
    assert dossier.company.name == "Acme AI"
    assert len(dossier.evidence) >= 2


def test_sourcer_skips_previously_explored_company_when_anchoring() -> None:
    tmp_path = workspace_tmp_dir("sourcer_exclusions")
    search_index = {
        "empresite empresa IA software espana cif": [
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
            EvidenceDocument(
                url="https://bravo.dev/team",
                title="Bravo Dev engineering team",
                snippet="Company: Bravo Dev | Person: Marta Diaz | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Bravo Dev\nCountry: Spain\nEmployees: 20\nPerson: Marta Diaz\nRole: CTO\nGenAI automation engineering",
            ),
        ],
        "infoempresa empresa IA software espana razon social": [
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
            EvidenceDocument(
                url="https://bravo.dev/team",
                title="Bravo Dev engineering team",
                snippet="Company: Bravo Dev | Person: Marta Diaz | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Bravo Dev\nCountry: Spain\nEmployees: 20\nPerson: Marta Diaz\nRole: CTO\nGenAI automation engineering",
            ),
        ],
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "sourcer.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)
    state.memory.searched_company_names = ["Acme AI"]

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    discovery_result = source_stage.execute(state)
    state.current_source_result = discovery_result
    focus_resolution = AssembleStage(StageAgentExecutor(None)).select_focus_company(state)

    assert discovery_result.sourcing_status.value == "FOUND"
    assert focus_resolution.selected_company == "Bravo Dev"
    assert all("acme.ai/about" != item.url for item in discovery_result.documents)
    assert any(item.url == "https://bravo.dev/team" for item in discovery_result.documents)


def test_discovery_ladder_advances_across_batches_and_candidate_passes() -> None:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con software")
    )
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(workspace_tmp_dir("sourcer-ladder") / "memory.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)
    source_stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)

    first = source_stage.execute(state)
    assert first.source_trace is not None
    assert first.source_trace.discovery_directory_selected == "empresite.eleconomista.es"
    assert first.source_trace.discovery_directories_consumed_in_run == ["empresite.eleconomista.es"]

    state.pending_discovery_queries = first.executed_queries[:]
    state.discovery_attempts_for_current_pass = 1
    second = source_stage.execute(state)
    assert second.source_trace is not None
    assert second.source_trace.discovery_directory_selected == "infoempresa.com"
    assert second.source_trace.discovery_directories_consumed_in_run == [
        "empresite.eleconomista.es",
        "infoempresa.com",
    ]

    state.pending_discovery_queries = []
    state.discovery_attempts_for_current_pass = 0
    third = source_stage.execute(state)
    assert third.source_trace is not None
    assert third.source_trace.discovery_directory_selected == "datoscif.es"

    state.pending_discovery_queries = third.executed_queries[:]
    state.discovery_attempts_for_current_pass = 1
    fourth = source_stage.execute(state)
    assert fourth.source_trace is not None
    assert fourth.source_trace.discovery_directory_selected == "censo.camara.es"

    state.pending_discovery_queries = []
    state.discovery_attempts_for_current_pass = 0
    exhausted = source_stage.execute(state)
    assert exhausted.source_trace is not None
    assert exhausted.source_trace.selected_query_count == 0
    assert state.discovery_ladder_exhausted_in_run is True
    assert exhausted.source_trace.discovery_directories_consumed_in_run == [
        "empresite.eleconomista.es",
        "infoempresa.com",
        "datoscif.es",
        "censo.camara.es",
    ]


def test_development_ignores_searched_company_names_for_recall() -> None:
    tmp_path = workspace_tmp_dir("sourcer-dev-searched-company")
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con software")
    )
    first_query = build_research_query_plan(request, relaxation_stage=0).planned_queries[0].query
    search_index = {
        first_query: [
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain | Employees: 25",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nSoftware automation company",
            )
        ]
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "memory.sqlite3")))
    state = LoadStateStage(memory_store, environment="development").execute(run)
    state.memory.searched_company_names = ["Acme AI"]

    result = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5).execute(state)
    state.current_source_result = result
    resolution = AssembleStage(StageAgentExecutor(None)).select_focus_company(state)

    assert result.source_trace is not None
    assert "Acme AI" not in result.source_trace.excluded_companies
    assert any(item.url == "https://acme.ai/about" for item in result.documents)
    assert resolution.selected_company == "Acme AI"


def test_development_ignores_request_scoped_company_exclusions() -> None:
    tmp_path = workspace_tmp_dir("sourcer-dev-request-exclusions")
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con software")
    )
    first_query = build_research_query_plan(request, relaxation_stage=0).planned_queries[0].query
    search_index = {
        first_query: [
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/megasoft-spain-sl",
                    title="MEGASOFT SPAIN SL - Infoempresa",
                    snippet="Country: Spain | Software platform",
                    source_type="fixture",
                    raw_content="Company: Megasoft Spain SL\nCountry: Spain\nSoftware platform",
                )
            ),
        ]
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "memory.sqlite3")))
    state = LoadStateStage(memory_store, environment="development").execute(run)
    state.memory.company_observations = [
        CompanyObservation(
            company_name="Megasoft Spain SL",
            country_code="es",
            employee_count_exact=2000,
            operational_status="active",
        )
    ]

    result = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5).execute(state)

    assert result.source_trace is not None
    assert result.source_trace.request_scoped_company_exclusions == []
    assert any(item.url == "https://www.infoempresa.com/es-es/es/empresa/megasoft-spain-sl" for item in result.documents)


def test_development_ignores_blocked_official_domains_for_recall() -> None:
    tmp_path = workspace_tmp_dir("sourcer-dev-blocked-domains")
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con software")
    )
    first_query = build_research_query_plan(request, relaxation_stage=0).planned_queries[0].query
    search_index = {
        first_query: [
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain | Employees: 25",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nSoftware automation company",
            )
        ]
    }
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "memory.sqlite3")))
    state = LoadStateStage(memory_store, environment="development").execute(run)
    state.memory.blocked_official_domains = ["acme.ai"]

    result = SourceStage(search_port=FakeSearchPort(search_index=search_index, pages={}), agent_executor=StageAgentExecutor(None), max_results=5).execute(state)

    assert any(item.url == "https://acme.ai/about" for item in result.documents)


def test_production_blocks_official_domains_for_recall() -> None:
    tmp_path = workspace_tmp_dir("sourcer-prod-blocked-domains")
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con software")
    )
    first_query = build_research_query_plan(request, relaxation_stage=0).planned_queries[0].query
    search_index = {
        first_query: [
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Country: Spain | Employees: 25",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nSoftware automation company",
            )
        ]
    }
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "memory.sqlite3")))
    state = LoadStateStage(memory_store, environment="production").execute(run)
    state.memory.blocked_official_domains = ["acme.ai"]

    result = SourceStage(search_port=FakeSearchPort(search_index=search_index, pages={}), agent_executor=StageAgentExecutor(None), max_results=5).execute(state)

    assert not result.documents


def test_ambiguous_discovery_document_is_not_excluded_too_early() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://directory.example.com/listing/software-companies",
            title="Software companies and providers",
            snippet="Acme AI and Bravo Dev appear in this list",
            source_type="fixture",
            raw_content="Acme AI\nBravo Dev\nSpanish software companies",
        )
    )
    source_stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)

    assert source_stage._document_subject_matches_excluded_company(document, ["Acme AI"]) is False


def test_sourcer_does_not_plan_person_lookup_before_anchor() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(workspace_tmp_dir("sourcer_plan") / "plan.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)
    source_stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)

    result = source_stage.execute(state)

    assert result.query_plan is not None
    assert all(query.expected_field == "company_name" for query in result.query_plan.planned_queries if query.research_phase == "company_discovery")
    assert all(query.expected_field not in {"person_name", "role_title"} for query in result.query_plan.planned_queries if query.research_phase == "company_discovery")


def test_sourcer_uses_size_sensitive_discovery_queries_for_gt_50_employee_requests() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de IA o software con mas de 50 empleados")
    )

    plan = build_research_query_plan(request, relaxation_stage=0)
    discovery_queries = [query.query for query in plan.planned_queries if query.research_phase == "company_discovery"]
    discovery_min_scores = [query.min_score for query in plan.planned_queries if query.research_phase == "company_discovery"]
    discovery_domains = [query.preferred_domains for query in plan.planned_queries if query.research_phase == "company_discovery"]

    assert any("empresa" in query.lower() for query in discovery_queries)
    assert any("cif" in query.lower() for query in discovery_queries)
    assert any("razon social" in query.lower() for query in discovery_queries)
    assert all("startup directory" not in query.lower() for query in discovery_queries)
    assert all("seedtable.com" not in domains for domains in discovery_domains)
    assert discovery_domains[:4] == [["empresite.eleconomista.es"], ["infoempresa.com"], ["datoscif.es"], ["censo.camara.es"]]
    assert min(discovery_min_scores) <= 0.45


def test_spain_anchor_queries_prioritize_identity_size_and_persona_before_website() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de IA o software con mas de 50 empleados")
    )

    plan = build_research_query_plan(request, relaxation_stage=0, anchor_company="BitBrain", mode="source_anchor_followup")
    company_queries = [query for query in plan.planned_queries if query.expected_field == "company_name"]
    website_queries = [query for query in plan.planned_queries if query.source_role == "website_resolution"]
    employee_queries = [query for query in plan.planned_queries if query.source_role == "employee_count_resolution"]
    governance_queries = [query for query in plan.planned_queries if query.source_role == "governance_resolution"]

    assert plan.planned_queries[0].expected_field == "company_name"
    assert plan.planned_queries[1].expected_field == "employee_estimate"
    assert company_queries[0].preferred_domains == ["infoempresa.com"]
    assert website_queries
    assert [query.preferred_domains for query in website_queries[:4]] == [
        ["empresite.eleconomista.es"],
        ["datoscif.es"],
        ["iberinform.es"],
    ] or [query.preferred_domains for query in website_queries[:4]] == [
        ["empresite.eleconomista.es"],
        ["datoscif.es"],
        ["iberinform.es"],
    ]
    assert [query.preferred_domains for query in employee_queries[:4]] == [
        ["einforma.com"],
        ["infoempresa.com"],
        ["iberinform.es", "axesor.es"],
        ["empresite.eleconomista.es"],
    ]
    assert governance_queries[0].preferred_domains == ["infoempresa.com", "datoscif.es", "einforma.com"]
    assert all("boe.es" not in query.preferred_domains for query in website_queries + employee_queries)
    assert website_queries[0].query == 'empresite "BitBrain" sitio web pagina web'
    assert governance_queries[0].query == '"BitBrain" administradores cargos directivos'


def test_domain_validation_queries_are_domain_centric_and_not_exact_match() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)

    queries = stage._official_domain_queries("Software Ag", "softwareag.es", [])

    assert len(queries) == 1
    assert queries[0].query == "softwareag.es contacto aviso legal cif"
    assert queries[0].preferred_domains == ["softwareag.es"]
    assert queries[0].exact_match is False


def test_spain_website_anchor_queries_are_not_exact_match() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software con menos de 50 empleados")
    )

    plan = build_research_query_plan(request, relaxation_stage=0, anchor_company="BitBrain", mode="source_focus_locked")
    website_queries = [query for query in plan.planned_queries if query.source_role == "website_resolution" and query.research_phase == "company_anchoring"]

    assert website_queries
    assert all(query.exact_match is False for query in website_queries)


def test_spain_discovery_demotes_startup_directories() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 founder de una empresa espanola de IA"))

    plan = build_research_query_plan(request, relaxation_stage=0)
    discovery_queries = [query for query in plan.planned_queries if query.research_phase == "company_discovery"]

    assert discovery_queries
    assert all(query.source_role == "entity_validation" or query.source_role == "signal_detection" for query in discovery_queries)
    assert all("seedtable.com" not in query.preferred_domains for query in discovery_queries)
    assert all("f6s.com" not in query.preferred_domains for query in discovery_queries)
    assert all("eu-startups.com" not in query.preferred_domains for query in discovery_queries)


def test_sourcer_trace_records_rejected_results_reasons() -> None:
    tmp_path = workspace_tmp_dir("sourcer-trace-rejections")
    search_index = {
        "empresite empresa IA software espana cif": [
            EvidenceDocument(
                url="https://twitter.com/acme_ai",
                title="Acme AI on X",
                snippet="social profile",
                source_type="fixture",
            ),
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
            ),
        ],
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "trace.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_result = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5).execute(state)

    assert source_result.source_trace is not None
    query_traces = [item for item in source_result.source_trace.query_traces if item.query == "empresite empresa IA software espana cif"]
    assert query_traces
    blocked = next(item for item in query_traces[0].results if item.url == "https://twitter.com/acme_ai")
    assert blocked.kept is False
    assert "blocked_host" in blocked.rejection_reasons


def test_sourcer_trace_records_enrichment_strategy_details() -> None:
    tmp_path = workspace_tmp_dir("sourcer-trace-enrichment")
    search_index = {
        "empresite empresa IA software espana cif": [
            EvidenceDocument(
                url="https://empresite.eleconomista.es/ACME-AI.html",
                title="ACME AI SL - Empresite",
                snippet="Empresa de software",
                source_type="fixture",
                raw_content="ACME AI SL. Sitio web: https://acme.ai. " * 20,
            ),
        ],
    }
    search_port = TrackingContentSearchPort(search_index=search_index, pages={}, extracted_pages={})
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "trace.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_result = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5).execute(state)

    assert source_result.source_trace is not None
    query_trace = next(item for item in source_result.source_trace.query_traces if item.query == "empresite empresa IA software espana cif")
    assert "raw_content_from_search" in query_trace.notes
    assert query_trace.documents_after_enrichment[0].enrichment_strategy_used == "search_raw"
    assert query_trace.documents_after_enrichment[0].raw_content_len_after is not None
    assert query_trace.documents_after_enrichment[0].extract_attempted is False
    assert query_trace.documents_after_enrichment[0].fetch_attempted is False


def test_sourcer_fetches_raw_content_for_spanish_directory_discovery() -> None:
    stage = SourceStage(
        search_port=TrackingContentSearchPort(
            search_index={},
            pages={"https://empresite.eleconomista.es/ACME-AI.html": "ACME AI SL. Web: https://acme.ai"},
            extracted_pages={
                "https://empresite.eleconomista.es/ACME-AI.html": (
                    "ACME AI SL. Empresa de software en Madrid. Sitio web: https://acme.ai. "
                    "CIF B12345678. Servicios de automatizacion para empresas en Espana. " * 4
                )
            },
        ),
        agent_executor=StageAgentExecutor(None),
        max_results=5,
    )
    document = EvidenceDocument(
        url="https://empresite.eleconomista.es/ACME-AI.html",
        title="ACME AI SL - Empresite",
        snippet="Empresa de software",
        source_type="fixture",
        research_phase="company_discovery",
    )
    query = ResearchQuery(
        query="empresite empresa software espana cif",
        objective="Discover one plausible Spanish software company ficha.",
        research_phase="company_discovery",
        source_role="entity_validation",
        source_tier_target="tier_b",
        expected_field="company_name",
    )

    enriched, fetched_urls, empty_fetch_urls, enrichment_details = stage._enrich_missing_content([document], query)

    assert enriched[0].raw_content.startswith("ACME AI SL. Empresa de software en Madrid.")
    assert fetched_urls == []
    assert empty_fetch_urls == []
    assert stage._search_port.extract_calls == [["https://empresite.eleconomista.es/ACME-AI.html"]]
    assert stage._search_port.fetch_calls == []
    assert enrichment_details["https://empresite.eleconomista.es/ACME-AI.html"]["enrichment_strategy_used"] == "extract_pages"


def test_tavily_includes_raw_content_for_discovery_and_anchoring() -> None:
    port = TavilySearchPort(api_key="test", base_url="https://example.com/search")
    discovery_query = ResearchQuery(
        query="empresite empresa software espana cif",
        objective="Discover company ficha",
        research_phase="company_discovery",
    )
    anchoring_query = ResearchQuery(
        query='empresite "Acme AI" sitio web pagina web',
        objective="Resolve website",
        research_phase="company_anchoring",
        source_role="website_resolution",
        candidate_company_name="Acme AI",
        expected_field="website",
    )

    assert port._include_raw_content_for_query(discovery_query) == "text"
    assert port._include_raw_content_for_query(anchoring_query) == "text"


def test_sourcer_uses_fetch_page_when_extract_pages_does_not_improve_content() -> None:
    search_port = TrackingContentSearchPort(
        search_index={},
        pages={"https://empresite.eleconomista.es/ACME-AI.html": "ACME AI SL. Web: https://acme.ai. CIF B12345678. Madrid."},
        extracted_pages={"https://empresite.eleconomista.es/ACME-AI.html": "ACME"},
    )
    stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    document = EvidenceDocument(
        url="https://empresite.eleconomista.es/ACME-AI.html",
        title="ACME AI SL - Empresite",
        snippet="Empresa de software",
        source_type="fixture",
        research_phase="company_discovery",
    )
    query = ResearchQuery(
        query="empresite empresa software espana cif",
        objective="Discover one plausible Spanish software company ficha.",
        research_phase="company_discovery",
        source_role="entity_validation",
        source_tier_target="tier_b",
        expected_field="company_name",
    )

    enriched, fetched_urls, empty_fetch_urls, enrichment_details = stage._enrich_missing_content([document], query)

    assert enriched[0].raw_content == "ACME AI SL. Web: https://acme.ai. CIF B12345678. Madrid."
    assert fetched_urls == ["https://empresite.eleconomista.es/ACME-AI.html"]
    assert empty_fetch_urls == []
    assert search_port.extract_calls == [["https://empresite.eleconomista.es/ACME-AI.html"]]
    assert search_port.fetch_calls == ["https://empresite.eleconomista.es/ACME-AI.html"]
    assert enrichment_details["https://empresite.eleconomista.es/ACME-AI.html"]["enrichment_strategy_used"] == "extract_then_fetch"


def test_sourcer_skips_extract_and_fetch_when_search_raw_content_is_sufficient() -> None:
    search_port = TrackingContentSearchPort(search_index={}, pages={}, extracted_pages={})
    stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    rich_content = (
        "ACME AI SL. Empresa de software en Madrid. "
        "Sitio web: https://acme.ai. CIF B12345678. "
        "Servicios de automatizacion para empresas en Espana. " * 4
    )
    document = EvidenceDocument(
        url="https://empresite.eleconomista.es/ACME-AI.html",
        title="ACME AI SL - Empresite",
        snippet="Empresa de software",
        source_type="fixture",
        raw_content=rich_content,
        research_phase="company_discovery",
    )
    query = ResearchQuery(
        query="empresite empresa software espana cif",
        objective="Discover one plausible Spanish software company ficha.",
        research_phase="company_discovery",
        source_role="entity_validation",
        source_tier_target="tier_b",
        expected_field="company_name",
    )

    enriched, fetched_urls, empty_fetch_urls, enrichment_details = stage._enrich_missing_content([document], query)

    assert enriched[0].raw_content == rich_content
    assert fetched_urls == []
    assert empty_fetch_urls == []
    assert search_port.extract_calls == []
    assert search_port.fetch_calls == []
    assert enrichment_details["https://empresite.eleconomista.es/ACME-AI.html"]["enrichment_strategy_used"] == "search_raw"


def test_sourcer_allows_reusing_anchor_directory_page_for_website_in_same_run() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 empresa espanola de software con menos de 50 empleados"))
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    state = EngineRuntimeState(run=SearchRunSnapshot(request=request), memory=ExplorationMemoryState())
    state.focus_company_locked = True
    state.current_focus_company_resolution = CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI")
    state.visited_urls_run_scoped = ["https://empresite.eleconomista.es/ACME-AI.html"]
    query = ResearchQuery(
        query='empresite "Acme AI" sitio web pagina web',
        objective="Resolve website",
        research_phase="company_anchoring",
        source_role="website_resolution",
        candidate_company_name="Acme AI",
        expected_field="website",
    )
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://empresite.eleconomista.es/ACME-AI.html",
            title="ACME AI SL - Empresite",
            snippet="Web: https://acme.ai",
            source_type="fixture",
            raw_content="ACME AI SL. Web: https://acme.ai.",
        ),
        anchor_company="Acme AI",
    )

    reasons = stage._result_rejection_reasons(document, query, state)

    assert "visited_url_in_run" not in reasons


def test_focus_locked_website_branch_tries_second_candidate_query_before_abort() -> None:
    tmp_path = workspace_tmp_dir("sourcer-second-website-attempt")
    search_index = {
        'empresite "Acme AI" sitio web pagina web': [],
        'datoscif "Acme AI" sitio web pagina web': [
            EvidenceDocument(
                url="https://www.datoscif.es/empresa/acme-ai-sl",
                title="ACME AI SL - DatosCif",
                snippet="Sitio web: https://acme.ai",
                source_type="fixture",
                raw_content="ACME AI SL. Sitio web: https://acme.ai. CIF B12345678.",
            ),
        ],
        'infoempresa "Acme AI" razon social cif': [
            EvidenceDocument(
                url="https://www.infoempresa.com/es-es/es/empresa/acme-ai-sl",
                title="ACME AI SL - Infoempresa",
                snippet="CIF B12345678. Madrid.",
                source_type="fixture",
                raw_content="ACME AI SL. CIF B12345678. Madrid.",
            ),
        ],
        "acme.ai contacto aviso legal cif": [
            EvidenceDocument(
                url="https://acme.ai/contact",
                title="Contact - Acme AI",
                snippet="CIF B12345678",
                source_type="fixture",
                raw_content="Contact page. CIF B12345678.",
            ),
        ],
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 empresa espanola de software con menos de 50 empleados"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "trace.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    state.current_focus_company_resolution = CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI")
    state.focus_company_locked = True
    state.current_source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(
                url="https://empresite.eleconomista.es/ACME-AI.html",
                title="ACME AI SL - Empresite",
                snippet="Empresa de software en Madrid",
                source_type="fixture",
                raw_content="ACME AI SL. Madrid. Actividad: software.",
                company_anchor="Acme AI",
                source_tier="tier_b",
            )
        ],
    )

    source_result = source_stage.execute(state)

    assert source_result.website_candidates
    assert source_result.source_trace is not None
    assert source_result.source_trace.candidate_branch_stop_reason != "no_candidate_website"
    assert 'datoscif "Acme AI" sitio web pagina web' in source_result.source_trace.selected_queries


def test_related_company_page_cannot_seed_focus_website_candidate() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    documents = [
        enrich_document_metadata(
            EvidenceDocument(
                url="https://empresite.eleconomista.es/TECNOLOGIA-TESEO-ESPANA.html",
                title="TECNOLOGIA TESEO ESPAÑA SL - Empresite",
                snippet="Empresa de software en Elche.",
                source_type="fixture",
                raw_content="TECNOLOGIA TESEO ESPAÑA SL. Empresa de software en Elche, Alicante.",
                company_anchor="Tecnologia Teseo España Sl",
            ),
            anchor_company="Tecnologia Teseo España Sl",
        ),
        enrich_document_metadata(
            EvidenceDocument(
                url="https://empresite.eleconomista.es/TESEO-MINOTAURO.html",
                title="TESEO Y EL MINOTAURO SL - Empresite",
                snippet="Web: https://www.hiddentrap.com",
                source_type="fixture",
                raw_content="TESEO Y EL MINOTAURO SL. Denominacion comercial Hidden Trap. Sitio web: https://www.hiddentrap.com.",
                company_anchor="Tecnologia Teseo España Sl",
            ),
            anchor_company="Tecnologia Teseo España Sl",
        ),
    ]

    candidates = stage._website_candidates_for_company(documents, "Tecnologia Teseo España Sl")

    assert candidates == []


def test_employee_estimate_document_is_selected_without_official_domain() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://empresite.eleconomista.es/TECNOLOGIA-TESEO-ESPANA.html",
            title="TECNOLOGIA TESEO ESPAÑA SL - Empresite",
            snippet="Empresa en Elche. Tiene un total de 3 trabajadores.",
            source_type="fixture",
            raw_content=(
                "TECNOLOGIA TESEO ESPAÑA SL. "
                "Empresa en Elche, Alicante, España. "
                "Tiene un total de 3 trabajadores en su plantilla."
            ),
            source_tier="tier_b",
        ),
        anchor_company="Tecnologia Teseo España Sl",
    )
    trace = ResearchTraceEntry(
        query_planned='empresite "Tecnologia Teseo" empleados plantilla',
        query_executed='empresite "Tecnologia Teseo" empleados plantilla',
        research_phase="company_anchoring",
        objective="Find explicit employee count evidence.",
        documents_considered=1,
        documents_selected=1,
        selected_urls=[document.url],
        expected_field="employee_estimate",
        source_role="employee_count_resolution",
    )

    selected, notes = stage._select_documents_for_assembler(
        [document],
        anchored_company="Tecnologia Teseo España Sl",
        official_domain=None,
        research_trace=[trace],
    )

    assert any(item.url == document.url for item in selected)
    assert "promising_missing_fields=employee_estimate" not in notes


def test_person_document_is_selected_without_official_domain() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.infoempresa.com/es-es/es/directivo/bitbrain-juan-perez",
            title="Juan Perez - CTO de BitBrain - Infoempresa",
            snippet="Directivo funcional de BitBrain en Zaragoza.",
            source_type="fixture",
            raw_content=(
                "Company: BitBrain\n"
                "Person: Juan Perez\n"
                "Role: CTO\n"
                "BitBrain software company in Zaragoza, Spain."
            ),
            source_tier="tier_b",
        ),
        anchor_company="BitBrain",
    )
    trace = ResearchTraceEntry(
        query_planned='"BitBrain" directivos funcionales cto founder ceo',
        query_executed='"BitBrain" directivos funcionales cto founder ceo',
        research_phase="field_acquisition",
        objective="Find a named founder or CTO.",
        documents_considered=1,
        documents_selected=1,
        selected_urls=[document.url],
        expected_field="person_name",
        source_role="governance_resolution",
    )

    selected, notes = stage._select_documents_for_assembler(
        [document],
        anchored_company="BitBrain",
        official_domain=None,
        research_trace=[trace],
    )

    assert any(item.url == document.url and item.selected_for_field == "person_name" for item in selected)
    assert "promising_missing_fields=person_name" not in notes


def test_focus_locked_queries_prioritize_identity_and_size_before_website() -> None:
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de software con menos de 50 empleados")
    )
    plan = build_research_query_plan(request, relaxation_stage=0, anchor_company="BitBrain", mode="source_focus_locked")
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)

    selected = stage._choose_focus_locked_queries(
        plan,
        [],
        current_documents=[],
        anchored_company="BitBrain",
    )

    assert [item.expected_field for item in selected] == ["company_name", "employee_estimate"]


def test_sourcer_selects_directory_title_company_not_portal_text() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://startupshub.catalonia.com/startup/barcelona/diipai/7148",
            title="diip artificial intelligence sl - Barcelona & Catalonia STARTUP HUB",
            snippet="The Barcelona & Catalonia Startup Hub. It is a Catalan Government project offering updated information.",
            source_type="fixture",
            raw_content="The Barcelona & Catalonia Startup Hub. It is a Catalan Government project offering updated information. Financial data. Investment. Other information.",
        )
    )

    candidates = candidate_company_names_from_document(document)

    assert candidates[0] == "Diip Artificial Intelligence Sl"


def test_non_official_profile_is_not_marked_company_controlled() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.iberinform.es/empresa/10485136/aima-artificial-intelligence",
            title="AIMA ARTIFICIAL INTELLIGENCE SL: CIF, Dirección y Ventas - Iberinform",
            snippet="Aima Artificial Intelligence Sl usa el código 7311.",
            source_type="fixture",
            raw_content="Aima Artificial Intelligence Sl opera en Barcelona. La página web de Aima Artificial Intelligence Sl es null.",
        ),
        anchor_company="Aima Artificial Intelligence Sl",
    )

    assert document.is_company_controlled_source is False
    assert document.source_tier == "tier_b"


def test_directory_document_extracts_bare_website_candidate_from_text() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.infoempresa.com/es-es/es/empresa/software----documentation-localization-spain-sl",
            title="SOFTWARE DOCUMENTATION LOCALIZATION SPAIN SL - Infoempresa",
            snippet="Datos de empresa. Sitio web: www.sdl.com",
            source_type="fixture",
            raw_content="""
                Datos de empresa
                Activa NIF/CIF: B18365502
                Dirección: Av. de la Constitución, 20, 18012 Granada, España
                Sitio web: www.sdl.com
            """,
        ),
        anchor_company="SOFTWARE DOCUMENTATION LOCALIZATION SPAIN SL",
    )

    assert extracted_official_website_from_document(document, "SOFTWARE DOCUMENTATION LOCALIZATION SPAIN SL") == "https://www.sdl.com"


def test_clean_company_name_preserves_spanish_characters() -> None:
    assert clean_company_name("SOFTWARE AG ESPAÑA SA") == "SOFTWARE AG ESPAÑA SA"


def test_derive_anchor_query_name_prefers_brand_alias_for_spanish_legal_name() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.datoscif.es/empresa/software-ag-espana-sa",
            title="SOFTWARE AG ESPAÑA SA - DatosCif",
            snippet="Sitio web: www.softwareag.es",
            source_type="fixture",
            raw_content="SOFTWARE AG ESPAÑA SA. Sitio web: www.softwareag.es. CIF A12345678.",
        ),
        anchor_company="SOFTWARE AG ESPAÑA SA",
    )

    aliases = derive_brand_aliases("SOFTWARE AG ESPAÑA SA", [document], "https://www.softwareag.es")
    query_name = derive_anchor_query_name("SOFTWARE AG ESPAÑA SA", [document], "https://www.softwareag.es")

    assert "Software Ag" in aliases
    assert query_name == "Software Ag"


def test_canonicalize_website_rejects_truncated_candidate_domains() -> None:
    assert canonicalize_website("https://www.infoempresa.c") is None


def test_directory_explicit_website_beats_cookie_or_consent_domains() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://empresite.eleconomista.es/SOLUCIONES-TECNOLOGICAS-AI-APPS.html",
            title="SOLUCIONES TECNOLOGICAS AI APPS SL - Empresite",
            snippet="Web: staiapps.com",
            source_type="fixture",
            raw_content="""
                SOLUCIONES TECNOLOGICAS AI APPS SL
                Sitio web: staiapps.com
                Consent preferences: https://didomi.preferences.show/some/path
                Cookie vendor: https://sdk.privacy-center.org/example
            """,
        ),
        anchor_company="Soluciones Tecnologicas A.i. Apps Sl.",
    )

    assert extracted_official_website_from_document(document, "Soluciones Tecnologicas A.i. Apps Sl.") == "https://staiapps.com"


def test_app_store_page_cannot_seed_website_candidate_on_its_own() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://apps.apple.com/us/app/estimateapp/id1141320662",
            title="EstimateApp on the App Store",
            snippet="Developer website: staiapps.com",
            source_type="fixture",
            raw_content="""
                EstimateApp on the App Store.
                Developer website: staiapps.com
            """,
        ),
        anchor_company="Soluciones Tecnologicas A.i. Apps Sl.",
    )

    assert stage._website_candidates_for_company([document], "Soluciones Tecnologicas A.i. Apps Sl.") == []


def test_select_anchor_company_prefers_real_company_over_portal_fragments() -> None:
    bitbrain_doc = enrich_document_metadata(
        EvidenceDocument(
            url="https://startupshub.catalonia.com/startup/barcelona/bitbrain/1111",
            title="BitBrain - Barcelona & Catalonia STARTUP HUB",
            snippet="BitBrain develops neurotechnology and AI products.",
            source_type="fixture",
            raw_content="BitBrain develops neurotechnology and AI products. Website: https://bitbrain.com/",
        )
    )
    noisy_doc = enrich_document_metadata(
        EvidenceDocument(
            url="https://startupshub.catalonia.com/page/investment",
            title="Investment - Barcelona & Catalonia STARTUP HUB",
            snippet="The Barcelona & Catalonia Startup Hub. It is a Catalan Government project.",
            source_type="fixture",
            raw_content="The Barcelona & Catalonia Startup Hub. It is a Catalan Government project offering updated information.",
        )
    )

    assert select_anchor_company([bitbrain_doc, noisy_doc]) == "BitBrain"


def test_sourcer_prioritizes_field_targeted_documents_for_assembler() -> None:
    tmp_path = workspace_tmp_dir("sourcer_field_batch")
    search_index = {
        "empresite empresa IA software espana cif": [
            EvidenceDocument(
                url="https://empresite.eleconomista.es/BDEO-SPAIN.html",
                title="BDEO SPAIN SL - Empresite",
                snippet="Web: https://bdeo.io | Actividad software de inteligencia artificial",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. Web: https://bdeo.io. Actividad: software de inteligencia artificial.",
            ),
        ],
        "infoempresa empresa IA software espana razon social": [
            EvidenceDocument(
                url="https://www.infoempresa.com/es-es/es/empresa/bdeo-spain-sl",
                title="BDEO SPAIN SL - Infoempresa",
                snippet="CIF B12345678. Domicilio en Madrid. Plantilla: Entre 50 y 249 empleados.",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. CIF B12345678. Domicilio Madrid. Plantilla: Entre 50 y 249 empleados.",
            ),
        ],
        'empresite "Bdeo" sitio web pagina web': [
            EvidenceDocument(
                url="https://empresite.eleconomista.es/BDEO-SPAIN.html",
                title="BDEO SPAIN SL - Empresite",
                snippet="Web: https://bdeo.io | Telefono: 910000000 | CIF B12345678",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. Web: https://bdeo.io. Direccion: Madrid. CIF: B12345678.",
            ),
        ],
        'infoempresa "Bdeo" razon social cif': [
            EvidenceDocument(
                url="https://www.infoempresa.com/es-es/es/empresa/bdeo-spain-sl",
                title="BDEO SPAIN SL - Infoempresa",
                snippet="CIF B12345678. Domicilio en Madrid. Plantilla: Entre 50 y 249 empleados.",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. CIF B12345678. Domicilio Madrid. Plantilla: Entre 50 y 249 empleados.",
            ),
        ],
        "bdeo.io contacto aviso legal cif": [
            EvidenceDocument(
                url="https://bdeo.io/contact",
                title="Contact - Bdeo",
                snippet="Contacta con Bdeo. CIF B12345678.",
                source_type="fixture",
                raw_content="Contacta con Bdeo. CIF B12345678. Madrid.",
            ),
        ],
        '"Bdeo" administradores cargos directivos': [
            EvidenceDocument(
                url="https://www.infoempresa.com/es-es/es/empresa/bdeo-spain-sl-directivos",
                title="Administradores BDEO SPAIN SL - Infoempresa",
                snippet="Julio Pernia, CEO y cofundador.",
                source_type="fixture",
                raw_content="Julio Pernia. Cargo: CEO y cofundador. Empresa: BDEO SPAIN SL.",
            ),
        ],
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead founder en espana con empresas de mas de 50 empleados"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "sourcer.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    assemble_stage = AssembleStage(StageAgentExecutor(None))
    discovery_result, focus_resolution = _lock_focus_company(state, source_stage, assemble_stage)
    assert discovery_result.sourcing_status.value == "FOUND"
    assert focus_resolution.selected_company == "BDEO SPAIN SL"

    source_result = source_stage.execute(state)

    selected_fields = [item.selected_for_field for item in source_result.documents]

    assert source_result.anchored_company_name == "BDEO SPAIN SL"
    assert selected_fields[0] == "company_name"
    assert "person_name" in selected_fields
    assert "promising_missing_fields=employee_estimate" in source_result.notes
    assert all(item.why_selected for item in source_result.documents)


def test_resolve_person_signal_rejects_generic_editorial_cto_page() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://openwebinars.net/blog/funciones-del-director-de-tecnologia-en-la-actualidad/",
            title="Funciones del director de tecnologia en la actualidad",
            snippet="Articulo editorial sobre el rol del CTO en las empresas tecnologicas.",
            source_type="fixture",
            raw_content="Las funciones del CTO en la actualidad incluyen liderazgo tecnico y coordinacion de equipos.",
        ),
        anchor_company="Acme AI",
    )

    resolved = resolve_person_signal([document], company_name="Acme AI")

    assert resolved["person_name"] is None
    assert resolved["role_title"] is None
    assert resolved["lead_source_type"] == "unknown"


def test_resolve_person_signal_accepts_speaker_page_with_explicit_company_role_link() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.4yfn.com/speakers/laura-martin-acme-ai",
            title="Laura Martin - CTO at Acme AI",
            snippet="Laura Martin, CTO at Acme AI, joins 4YFN as a speaker.",
            source_type="fixture",
            raw_content="Speaker: Laura Martin. Role: CTO. Company: Acme AI. Event: 4YFN Barcelona.",
        ),
        anchor_company="Acme AI",
    )

    resolved = resolve_person_signal([document], company_name="Acme AI")

    assert resolved["person_name"] == "Laura Martin"
    assert resolved["role_title"] == "CTO"
    assert resolved["lead_source_type"] == "speaker_or_event"
    assert resolved["primary_person_source_url"] == document.url


def test_resolve_person_signal_accepts_mercantile_directory_legal_fallback() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.infoempresa.com/es-es/es/empresa/bdeo-spain-sl-directivos",
            title="Administradores BDEO SPAIN SL - Infoempresa",
            snippet="Julio Pernia, administrador unico.",
            source_type="fixture",
            raw_content="Julio Pernia. Cargo: Administrador unico. Empresa: BDEO SPAIN SL.",
        ),
        anchor_company="BDEO SPAIN SL",
    )

    resolved = resolve_person_signal([document], company_name="BDEO SPAIN SL")

    assert resolved["person_name"] == "Julio Pernia"
    assert resolved["role_title"] == "Administrador unico"
    assert resolved["lead_source_type"] == "mercantile_directory"
    assert resolved["person_confidence"] == "strong"


def test_partner_directory_is_not_treated_as_official_website() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://awinpartnerdirectory.builtfirst.com/awin-perks-blueknow",
            title="Awin Perks Blueknow",
            snippet="Blueknow partner profile on Awin Perks.",
            source_type="fixture",
            raw_content="Blueknow partner profile on Awin Perks. Learn more about the offer.",
        ),
        anchor_company="Blueknow",
    )

    assert extracted_official_website_from_document(document, "Blueknow") is None


def test_job_title_page_does_not_become_company_anchor() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.eu-startups.com/job/plc-automation-engineer/",
            title="PLC Automation Engineer",
            snippet="Join Idneo in Spain to work on automation and embedded systems.",
            source_type="fixture",
            raw_content="Idneo is hiring a PLC Automation Engineer in Spain. Company: Idneo. Country: Spain. Website: https://www.idneo.com/",
        )
    )

    candidates = candidate_company_names_from_document(document)

    assert "Idneo" in candidates
    assert "PLC Automation Engineer" not in candidates


def test_directory_subdomains_are_treated_as_directory_hosts() -> None:
    assert domain_is_directory("rocketreach.co") is True
    assert domain_is_directory("static.rocketreach.co") is True
    assert domain_is_directory("www.crunchbase.com") is True


def test_investor_like_or_category_names_are_not_plausible_company_anchors() -> None:
    assert is_plausible_company_name("Madrid Ventures") is False
    assert is_plausible_company_name("Scaleup Finance") is False


def test_company_discovery_filters_investor_profiles_from_results() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    query = build_research_query_plan(
        NormalizeStage(StageAgentExecutor(None)).execute(
            LeadSearchStartRequest(user_text="busca 1 founder en espana con mas de 50 empleados")
        ),
        relaxation_stage=1,
    ).planned_queries[0]
    investor_doc = EvidenceDocument(
        url="https://www.eu-startups.com/investor/juno-capital-partners/",
        title="Juno Capital Partners | EU-Startups",
        snippet="Investor profile for a venture capital firm",
        source_type="fixture",
    )
    company_doc = EvidenceDocument(
        url="https://empresite.eleconomista.es/DATASLAYER-AI.html",
        title="DATASLAYER AI SL - Empresite",
        snippet="Empresa espanola de software e inteligencia artificial",
        source_type="fixture",
    )

    assert stage._query_allows_result(investor_doc, query) is False
    assert stage._query_allows_result(company_doc, query) is True


def test_unofficial_hosts_are_not_accepted_as_company_websites() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://static.rocketreach.co/company/swanlaab",
            title="Swanlaab profile",
            snippet="Swanlaab profile with CDN assets",
            source_type="fixture",
            raw_content="Website: https://d1hbpr09pwz0sk.cloudfront.net",
        ),
        anchor_company="Swanlaab",
    )

    assert domain_is_unofficial_website_host("d1hbpr09pwz0sk.cloudfront.net") is True
    assert extracted_official_website_from_document(document, "Swanlaab") is None


def test_empresite_cdn_host_is_not_accepted_as_company_website() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://empresite.eleconomista.es/FOCUS-SOFTWARE-SPAIN.html",
            title="FOCUS SOFTWARE SPAIN S.L. - Empresite",
            snippet="Website https://cdn-empresite.eleconomista.es",
            source_type="fixture",
            raw_content="Website: https://cdn-empresite.eleconomista.es",
        ),
        anchor_company="Focus Software Spain S.l.",
    )

    assert domain_is_unofficial_website_host("cdn-empresite.eleconomista.es") is True
    assert extracted_official_website_from_document(document, "Focus Software Spain S.l.") is None


def test_google_maps_host_is_not_accepted_as_company_website() -> None:
    document = enrich_document_metadata(
        EvidenceDocument(
            url="https://www.infoempresa.com/es-es/es/empresa/software-attitude-sl",
            title="SOFTWARE ATTITUDE SL - Infoempresa",
            snippet="Mapa y localizacion de la empresa",
            source_type="fixture",
            raw_content="Website: https://maps.google.es/?cid=123456789",
        ),
        anchor_company="Software Attitude Sl.",
    )

    assert domain_is_unofficial_website_host("maps.google.es") is True
    assert extracted_official_website_from_document(document, "Software Attitude Sl.") is None


def test_spain_company_discovery_filters_directory_category_pages() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    query = build_research_query_plan(
        NormalizeStage(StageAgentExecutor(None)).execute(
            LeadSearchStartRequest(user_text="busca 1 founder de una empresa espanola de IA")
        ),
        relaxation_stage=0,
    ).planned_queries[0]
    category_doc = EvidenceDocument(
        url="https://empresite.eleconomista.es/Actividad/EMPRESAS-DESARROLLO-SOFTWARE/provincia/BARCELONA/",
        title="Empresas desarrollo software en Barcelona - Empresite",
        snippet="Listado de empresas de desarrollo software en Barcelona",
        source_type="fixture",
    )
    company_doc = EvidenceDocument(
        url="https://empresite.eleconomista.es/BITBRAIN-TECHNOLOGIES.html",
        title="BITBRAIN TECHNOLOGIES SL - Empresite",
        snippet="Empresa de tecnologia en Madrid",
        source_type="fixture",
    )

    assert stage._query_allows_result(category_doc, query) is False
    assert stage._query_allows_result(company_doc, query) is True


def test_spain_company_discovery_filters_non_operational_entities() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    query = build_research_query_plan(
        NormalizeStage(StageAgentExecutor(None)).execute(
            LeadSearchStartRequest(user_text="busca 1 founder de una empresa espanola de IA")
        ),
        relaxation_stage=0,
    ).planned_queries[0]
    extinct_doc = EvidenceDocument(
        url="https://empresite.eleconomista.es/BRAINPOWER-IBERIA.html",
        title="BRAINPOWER IBERIA S.L. EXTINGUIDA - Empresite",
        snippet="Empresa extinguida en Madrid",
        source_type="fixture",
    )
    active_doc = EvidenceDocument(
        url="https://empresite.eleconomista.es/BITBRAIN-TECHNOLOGIES.html",
        title="BITBRAIN TECHNOLOGIES SL - Empresite",
        snippet="Empresa de tecnologia en Madrid",
        source_type="fixture",
    )

    assert stage._query_allows_result(extinct_doc, query) is False
    assert stage._query_allows_result(active_doc, query) is True


def test_extract_employee_size_hint_reads_public_range() -> None:
    value, hint_type = extract_employee_size_hint("Plantilla: entre 50 y 249 empleados")

    assert value == 249
    assert hint_type == "range"


def test_lt50_discovery_rejects_large_company_hint() -> None:
    stage = SourceStage(search_port=FakeSearchPort(search_index={}, pages={}), agent_executor=StageAgentExecutor(None), max_results=5)
    query = build_research_query_plan(
        NormalizeStage(StageAgentExecutor(None)).execute(
            LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de IA o software con menos de 50 empleados")
        ),
        relaxation_stage=0,
    ).planned_queries[0]
    large_doc = EvidenceDocument(
        url="https://www.infoempresa.com/es-es/es/empresa/software-ag-espana-sa",
        title="SOFTWARE AG ESPAÑA SA - Infoempresa",
        snippet="Plantilla: Entre 50 y 249 empleados",
        source_type="fixture",
        raw_content="SOFTWARE AG ESPAÑA SA. Plantilla: Entre 50 y 249 empleados.",
    )

    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de IA o software con menos de 50 empleados")
    )

    assert stage._query_allows_result(large_doc, query, request=request) is False


def test_select_anchor_company_skips_non_operational_spanish_company() -> None:
    extinct_doc = enrich_document_metadata(
        EvidenceDocument(
            url="https://empresite.eleconomista.es/BRAINPOWER-IBERIA.html",
            title="BRAINPOWER IBERIA S.L. EXTINGUIDA - Empresite",
            snippet="Empresa extinguida en Madrid",
            source_type="fixture",
            raw_content="BRAINPOWER IBERIA S.L. extinguida. CIF B12345678.",
        )
    )
    active_doc = enrich_document_metadata(
        EvidenceDocument(
            url="https://empresite.eleconomista.es/BITBRAIN-TECHNOLOGIES.html",
            title="BITBRAIN TECHNOLOGIES SL - Empresite",
            snippet="Empresa de tecnologia en Madrid",
            source_type="fixture",
            raw_content="BITBRAIN TECHNOLOGIES SL. Web: https://bitbrain.com. CIF B87654321.",
        )
    )

    assert company_name_matches_anchor(select_anchor_company([extinct_doc, active_doc]), "BITBRAIN TECHNOLOGIES SL")


def test_source_stops_branch_after_failed_domain_validation() -> None:
    tmp_path = workspace_tmp_dir("sourcer_domain_validation_stop")
    search_index = {
        "empresite empresa software SaaS espana cif": [
            EvidenceDocument(
                url="https://empresite.eleconomista.es/BDEO-SPAIN.html",
                title="BDEO SPAIN SL - Empresite",
                snippet="Web: https://bdeo.io | Empresa de software",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. Web: https://bdeo.io. CIF: B12345678.",
            ),
        ],
        "infoempresa empresa software SaaS espana razon social": [
            EvidenceDocument(
                url="https://www.datoscif.es/empresa/bdeo-spain-sl",
                title="BDEO SPAIN SL - DatosCif",
                snippet="Razon social BDEO SPAIN SL. Web https://bdeo.io",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. Razon social: BDEO SPAIN SL. Web: https://bdeo.io.",
            ),
        ],
        'empresite "Bdeo" sitio web pagina web': [
            EvidenceDocument(
                url="https://empresite.eleconomista.es/BDEO-SPAIN.html",
                title="BDEO SPAIN SL - Empresite",
                snippet="Web: https://bdeo.io",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. Sitio web: https://bdeo.io.",
            ),
        ],
        'infoempresa "Bdeo" razon social cif': [
            EvidenceDocument(
                url="https://www.infoempresa.com/es-es/es/empresa/bdeo-spain-sl",
                title="BDEO SPAIN SL - Infoempresa",
                snippet="CIF B12345678",
                source_type="fixture",
                raw_content="BDEO SPAIN SL. CIF B12345678.",
            ),
        ],
        "bdeo.io contacto aviso legal cif": [],
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 founder o CTO de una empresa espanola de IA o software con menos de 50 empleados"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(search_call_budget=10, source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "sourcer.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    assemble_stage = AssembleStage(StageAgentExecutor(None))
    discovery_result, focus_resolution = _lock_focus_company(state, source_stage, assemble_stage)

    assert discovery_result.sourcing_status.value == "FOUND"
    assert focus_resolution.selected_company == "BDEO SPAIN SL"

    source_result = source_stage.execute(state)

    assert source_result.sourcing_status.value == "FOUND"
    assert source_result.source_trace is not None
    assert source_result.source_trace.anchor_raw_name == "BDEO SPAIN SL"
    assert source_result.source_trace.anchor_query_name == "Bdeo"
    assert source_result.source_trace.candidate_branch_stop_reason == "zero_results_on_domain_validation"
    assert state.run.budget.search_calls_used <= 5


def test_sourcer_continues_when_one_search_query_fails() -> None:
    tmp_path = workspace_tmp_dir("sourcer_partial_query_failure")
    search_port = FlakySearchPort(
        search_index={
            "empresite empresa IA software espana cif": [
                EvidenceDocument(
                    url="https://acme.ai/about",
                    title="Acme AI leadership",
                    snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
                )
            ],
            "acme.ai contacto aviso legal cif": [
                EvidenceDocument(
                    url="https://acme.ai/contact",
                    title="Contact - Acme AI",
                    snippet="Contact page for Acme AI",
                    source_type="fixture",
                    raw_content="Contact page for Acme AI. Company: Acme AI. Country: Spain.",
                )
            ],
        },
        failing_queries={'"Acme AI" administradores cargos directivos'},
    )
    normalizer = NormalizeStage(StageAgentExecutor(None))
    request = normalizer.execute(LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai"))
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "sourcer.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    state.current_focus_company_resolution = CompanyFocusResolution(selected_company="Acme AI", query_name="Acme AI")
    state.focus_company_locked = True
    state.current_source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Acme AI",
        documents=[
            EvidenceDocument(
                url="https://acme.ai/about",
                title="Acme AI leadership",
                snippet="Company: Acme AI | Person: Laura Martin | Role: CTO | Country: Spain",
                source_type="fixture",
                raw_content="Company: Acme AI\nCountry: Spain\nEmployees: 25\nPerson: Laura Martin\nRole: CTO\nGenAI automation engineering",
                is_company_controlled_source=True,
                source_tier="tier_a",
                company_anchor="Acme AI",
            )
        ],
    )

    source_result = source_stage.execute(state)

    assert source_result.sourcing_status.value == "FOUND"
    assert any('search_query_failed="Acme AI" administradores cargos directivos' in note for note in source_result.notes)
    assert source_result.anchored_company_name == "Acme AI"


def test_company_observations_exclude_incompatible_large_company_for_small_request() -> None:
    tmp_path = workspace_tmp_dir("sourcer-observation-exclusion-small")
    search_index = {
        "empresite empresa software SaaS espana cif": [
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/megasoft-spain-sl",
                    title="MEGASOFT SPAIN SL - Infoempresa",
                    snippet="Employees: 2000 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Megasoft Spain SL\nCountry: Spain\nEmployees: 2000\nSoftware platform",
                )
            ),
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/bravo-dev-sl",
                    title="BRAVO DEV SL - Infoempresa",
                    snippet="Employees: 30 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Bravo Dev SL\nCountry: Spain\nEmployees: 30\nSoftware automation company",
                )
            ),
        ],
        "empresite empresa IA software espana cif": [
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/megasoft-spain-sl",
                    title="MEGASOFT SPAIN SL - Infoempresa",
                    snippet="Employees: 2000 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Megasoft Spain SL\nCountry: Spain\nEmployees: 2000\nSoftware platform",
                )
            ),
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/bravo-dev-sl",
                    title="BRAVO DEV SL - Infoempresa",
                    snippet="Employees: 30 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Bravo Dev SL\nCountry: Spain\nEmployees: 30\nSoftware automation company",
                )
            ),
        ],
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con software")
    )
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "memory.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)
    state.memory.company_observations = [
        CompanyObservation(
            company_name="Megasoft Spain SL",
            country_code="es",
            employee_count_exact=2000,
            operational_status="active",
        )
    ]
    state.discovery_attempts_for_current_pass = 2

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    discovery_result = source_stage.execute(state)
    state.current_source_result = discovery_result
    resolution = AssembleStage(StageAgentExecutor(None)).select_focus_company(state)

    assert discovery_result.source_trace is not None
    assert "Megasoft Spain SL" in discovery_result.source_trace.request_scoped_company_exclusions
    assert resolution.selected_company == "BRAVO DEV SL"


def test_company_observations_do_not_exclude_large_company_for_large_request() -> None:
    tmp_path = workspace_tmp_dir("sourcer-observation-exclusion-large")
    search_index = {
        "empresite empresa software SaaS espana cif": [
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/megasoft-spain-sl",
                    title="MEGASOFT SPAIN SL - Infoempresa",
                    snippet="Employees: 2000 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Megasoft Spain SL\nCountry: Spain\nEmployees: 2000\nSoftware platform",
                )
            ),
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/bravo-dev-sl",
                    title="BRAVO DEV SL - Infoempresa",
                    snippet="Employees: 30 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Bravo Dev SL\nCountry: Spain\nEmployees: 30\nSoftware automation company",
                )
            ),
        ],
        "empresite empresa IA software espana cif": [
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/megasoft-spain-sl",
                    title="MEGASOFT SPAIN SL - Infoempresa",
                    snippet="Employees: 2000 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Megasoft Spain SL\nCountry: Spain\nEmployees: 2000\nSoftware platform",
                )
            ),
            enrich_document_metadata(
                EvidenceDocument(
                    url="https://www.infoempresa.com/es-es/es/empresa/bravo-dev-sl",
                    title="BRAVO DEV SL - Infoempresa",
                    snippet="Employees: 30 | Country: Spain",
                    source_type="fixture",
                    raw_content="Company: Bravo Dev SL\nCountry: Spain\nEmployees: 30\nSoftware automation company",
                )
            ),
        ],
    }
    search_port = FakeSearchPort(search_index=search_index, pages={})
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana con mas de 1000 empleados y software")
    )
    run = SearchRunSnapshot(request=request, budget=SearchBudget(source_attempt_budget=6, enrich_attempt_budget=1))
    memory_store = SqliteExplorationMemoryStore(SqliteDatabase(str(tmp_path / "memory.sqlite3")))
    state = LoadStateStage(memory_store).execute(run)
    state.memory.company_observations = [
        CompanyObservation(
            company_name="Megasoft Spain SL",
            country_code="es",
            employee_count_exact=2000,
            operational_status="active",
        )
    ]
    state.discovery_attempts_for_current_pass = 2

    source_stage = SourceStage(search_port=search_port, agent_executor=StageAgentExecutor(None), max_results=5)
    discovery_result = source_stage.execute(state)
    state.current_source_result = discovery_result
    resolution = AssembleStage(StageAgentExecutor(None)).select_focus_company(state)

    assert discovery_result.source_trace is not None
    assert "Megasoft Spain SL" not in discovery_result.source_trace.request_scoped_company_exclusions
    assert resolution.selected_company == "MEGASOFT SPAIN SL"
