from __future__ import annotations

from pathlib import Path

import pytest

from lolo_lead_management.adapters.reranker.lm_studio import LmStudioAwareRerankerPort
from lolo_lead_management.domain.enums import FieldEvidenceStatus, SourcingStatus
from lolo_lead_management.domain.errors import RerankerUnavailableError
from lolo_lead_management.domain.models import (
    AssembledFieldEvidence,
    AssembledLeadDossier,
    CompanyCandidate,
    CompanyFocusResolution,
    EvidenceDocument,
    LeadSearchStartRequest,
    PersonCandidate,
    ResearchQuery,
    SearchBudget,
    SearchRunSnapshot,
    SourcePassResult,
    SourceStageTrace,
)
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.enrich import EnrichStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.ports.reranker import RerankCandidate, RerankResult
class FakeRerankerPort:
    def __init__(self, ordered_urls: list[str]) -> None:
        self._ordered_urls = ordered_urls
        self.last_call_trace: dict | None = None
        self.calls: list[dict] = []

    def rerank(self, *, query: str, candidates: list[RerankCandidate], top_k: int) -> list[RerankResult]:
        self.calls.append({"query": query, "candidate_urls": [item.url for item in candidates], "top_k": top_k})
        by_url = {item.url: item for item in candidates}
        results = []
        for rank, url in enumerate(self._ordered_urls, start=1):
            candidate = by_url.get(url)
            if candidate is None:
                continue
            results.append(
                RerankResult(
                    id=candidate.id,
                    url=candidate.url,
                    score=float(len(self._ordered_urls) - rank + 1),
                    rank=rank,
                    field_target=candidate.field_target,
                )
            )
        self.last_call_trace = {
            "reranker_provider_attempts": [{"provider": "fake", "url": "memory://fake", "status": "ok"}],
            "lmstudio_probe_status": "fake",
            "resolved_model_path": None,
            "sidecar_bootstrap_status": "not_needed",
            "rerank_query": query,
            "rerank_candidates_count": len(candidates),
            "rerank_top_results": [
                {"id": item.id, "url": item.url, "score": item.score, "rank": item.rank, "field_target": item.field_target}
                for item in results[:top_k]
            ],
            "rerank_retry_performed": False,
            "reranker_failure_reason": None,
        }
        return results[:top_k]


class DocumentAwareAssemblerLlmPort:
    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, schema)
        url = (input_payload.get("document") or {}).get("url", "")
        focus_company = input_payload.get("focus_company") or "Aplicaciones En Informatica Avanzada Sl"
        if "regina" in url:
            return {
                "segment_company_name": focus_company,
                "field_assertions": [
                    {"field_name": "company_name", "company_name": focus_company, "value": focus_company, "status": "satisfied", "support_type": "explicit", "reasoning_note": "registry company"},
                    {"field_name": "country", "company_name": focus_company, "value": "es", "status": "satisfied", "support_type": "explicit", "reasoning_note": "registry country"},
                    {"field_name": "employee_estimate", "company_name": focus_company, "value": 40, "status": "satisfied", "support_type": "explicit", "reasoning_note": "registry size", "employee_count_type": "exact"},
                ],
                "contact_assertions": [
                    {
                        "person_name": "LLOPIS RIVAS REGINA MARIA",
                        "role_title": "Administrador Unico",
                        "company_name": focus_company,
                        "status": "satisfied",
                        "support_type": "explicit",
                        "reasoning_note": "registry contact",
                    }
                ],
                "fit_signals": ["software"],
                "contradictions": [],
                "notes": ["contact_found"],
            }
        return {
            "segment_company_name": focus_company,
            "field_assertions": [
                {"field_name": "company_name", "company_name": focus_company, "value": focus_company, "status": "satisfied", "support_type": "explicit", "reasoning_note": "about company"},
                {"field_name": "website", "company_name": focus_company, "value": "https://aia.es", "status": "satisfied", "support_type": "explicit", "reasoning_note": "about website"},
                {"field_name": "country", "company_name": focus_company, "value": "es", "status": "satisfied", "support_type": "explicit", "reasoning_note": "about country"},
                {"field_name": "employee_estimate", "company_name": focus_company, "value": 40, "status": "satisfied", "support_type": "explicit", "reasoning_note": "about size", "employee_count_type": "exact"},
            ],
            "contact_assertions": [],
            "fit_signals": ["software", "genai"],
            "contradictions": [],
            "notes": ["no_contact"],
        }


class _HtmlResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._text.encode("utf-8")


def _request_and_state() -> EngineRuntimeState:
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    run = SearchRunSnapshot(request=request, budget=SearchBudget(search_call_budget=10, source_attempt_budget=6, enrich_attempt_budget=1))
    return EngineRuntimeState(run=run)


def test_lm_studio_aware_reranker_uses_lm_studio_when_probe_succeeds(monkeypatch) -> None:
    port = LmStudioAwareRerankerPort(
        model_key="text-embedding-bge-reranker-v2-m3",
        model_path=None,
        lm_studio_base_url="http://127.0.0.1:1234",
        engine_base_url="http://127.0.0.1:8081",
        bootstrap_enabled=True,
        runtime_cache_dir="data/runtime/reranker",
        timeout_seconds=15,
    )

    def fake_post_rerank(*, base_url, endpoints, query, candidates, top_k, provider, trace):
        assert base_url == "http://127.0.0.1:1234"
        trace["reranker_provider_attempts"].append({"provider": provider, "url": f"{base_url}{endpoints[0]}", "status": "ok"})
        return [
            RerankResult(id=candidates[1].id, url=candidates[1].url, score=0.9, rank=1, field_target=candidates[1].field_target),
            RerankResult(id=candidates[0].id, url=candidates[0].url, score=0.2, rank=2, field_target=candidates[0].field_target),
        ][:top_k]

    monkeypatch.setattr(port, "_post_rerank", fake_post_rerank)
    monkeypatch.setattr(port, "_resolve_model_path", lambda: (_ for _ in ()).throw(AssertionError("bootstrap should not run")))
    monkeypatch.setattr(port, "_ensure_sidecar_ready", lambda model_path: (_ for _ in ()).throw(AssertionError("bootstrap should not run")))

    results = port.rerank(
        query="Focus company: AIA. Urgent field: person_name.",
        candidates=[
            RerankCandidate(id="0", url="https://aia.es/about", field_target="person_name", text="about"),
            RerankCandidate(id="1", url="https://datoscif.es/directivo/regina", field_target="person_name", text="regina"),
        ],
        top_k=2,
    )

    assert [item.url for item in results] == ["https://datoscif.es/directivo/regina", "https://aia.es/about"]
    assert port.last_call_trace is not None
    assert port.last_call_trace["lmstudio_probe_status"] == "ok"
    assert port.last_call_trace["rerank_retry_performed"] is False


def test_lm_studio_aware_reranker_bootstraps_sidecar_after_probe_failure(monkeypatch) -> None:
    port = LmStudioAwareRerankerPort(
        model_key="text-embedding-bge-reranker-v2-m3",
        model_path=None,
        lm_studio_base_url="http://127.0.0.1:1234",
        engine_base_url="http://127.0.0.1:8081",
        bootstrap_enabled=True,
        runtime_cache_dir="data/runtime/reranker",
        timeout_seconds=15,
    )

    def fake_post_rerank(*, base_url, endpoints, query, candidates, top_k, provider, trace):
        if provider == "lm_studio":
            raise RerankerUnavailableError("HTTP 404: missing endpoint")
        trace["reranker_provider_attempts"].append({"provider": provider, "url": f"{base_url}{endpoints[0]}", "status": "ok"})
        return [RerankResult(id=candidates[0].id, url=candidates[0].url, score=0.8, rank=1, field_target=candidates[0].field_target)]

    monkeypatch.setattr(port, "_post_rerank", fake_post_rerank)
    monkeypatch.setattr(port, "_resolve_model_path", lambda: Path("C:/Users/maror/.lmstudio/models/gpustack/bge-reranker-v2-m3-Q8_0.gguf"))
    monkeypatch.setattr(port, "_ensure_sidecar_ready", lambda model_path: "spawned")

    results = port.rerank(
        query="Focus company: AIA. Urgent field: person_name.",
        candidates=[RerankCandidate(id="0", url="https://datoscif.es/directivo/regina", field_target="person_name", text="regina")],
        top_k=1,
    )

    assert [item.url for item in results] == ["https://datoscif.es/directivo/regina"]
    assert port.last_call_trace is not None
    assert port.last_call_trace["rerank_retry_performed"] is True
    assert port.last_call_trace["resolved_model_path"].endswith("bge-reranker-v2-m3-Q8_0.gguf")
    assert port.last_call_trace["sidecar_bootstrap_status"] == "spawned"


def test_lm_studio_aware_reranker_fails_fast_when_model_cannot_be_resolved(monkeypatch) -> None:
    port = LmStudioAwareRerankerPort(
        model_key="text-embedding-bge-reranker-v2-m3",
        model_path=None,
        lm_studio_base_url="http://127.0.0.1:1234",
        engine_base_url="http://127.0.0.1:8081",
        bootstrap_enabled=True,
        runtime_cache_dir="data/runtime/reranker",
        timeout_seconds=15,
    )

    monkeypatch.setattr(port, "_post_rerank", lambda **kwargs: (_ for _ in ()).throw(RerankerUnavailableError("HTTP 404: missing endpoint")))
    monkeypatch.setattr(port, "_resolve_model_path", lambda: (_ for _ in ()).throw(RerankerUnavailableError("reranker_model_not_found")))

    with pytest.raises(RerankerUnavailableError, match="reranker_model_not_found"):
        port.rerank(
            query="Focus company: AIA. Urgent field: person_name.",
            candidates=[RerankCandidate(id="0", url="https://datoscif.es/directivo/regina", field_target="person_name", text="regina")],
            top_k=1,
        )


def test_lm_studio_aware_reranker_autodiscovers_lm_studio_model_with_embedding_prefix_removed(monkeypatch, tmp_path) -> None:
    model_dir = tmp_path / ".lmstudio" / "models" / "gpustack" / "bge-reranker-v2-m3-GGUF"
    model_dir.mkdir(parents=True)
    model_path = model_dir / "bge-reranker-v2-m3-Q8_0.gguf"
    model_path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    port = LmStudioAwareRerankerPort(
        model_key="text-embedding-bge-reranker-v2-m3",
        model_path=None,
        lm_studio_base_url="http://127.0.0.1:1234",
        engine_base_url="http://127.0.0.1:8081",
        bootstrap_enabled=True,
        runtime_cache_dir="data/runtime/reranker",
        timeout_seconds=15,
    )

    assert port._resolve_model_path() == model_path


def test_lm_studio_aware_reranker_resolves_absolute_github_release_asset_url(monkeypatch) -> None:
    port = LmStudioAwareRerankerPort(
        model_key="text-embedding-bge-reranker-v2-m3",
        model_path=None,
        lm_studio_base_url="http://127.0.0.1:1234",
        engine_base_url="http://127.0.0.1:8081",
        bootstrap_enabled=True,
        runtime_cache_dir="data/runtime/reranker",
        timeout_seconds=15,
    )
    html = '<a href="https://github.com/ggml-org/llama.cpp/releases/download/b8850/llama-b8850-bin-win-cpu-x64.zip">Windows x64 (CPU)</a>'

    monkeypatch.setattr(
        "lolo_lead_management.adapters.reranker.lm_studio.request.urlopen",
        lambda req, timeout: _HtmlResponse(html),
    )

    assert (
        port._resolve_windows_cpu_asset_url()
        == "https://github.com/ggml-org/llama.cpp/releases/download/b8850/llama-b8850-bin-win-cpu-x64.zip"
    )


def test_assemble_uses_reranker_to_promote_governance_doc_for_person_resolution() -> None:
    stage = AssembleStage(
        StageAgentExecutor(DocumentAwareAssemblerLlmPort()),
        reranker=FakeRerankerPort(
            [
                "https://www.datoscif.es/directivo/llopis-rivas-regina-maria",
                "https://aia.es/about",
            ]
        ),
        top_k_initial=1,
        expansion_docs=1,
    )
    state = _request_and_state()
    state.current_focus_company_resolution = CompanyFocusResolution(
        selected_company="Aplicaciones En Informatica Avanzada Sl",
        legal_name="Aplicaciones En Informatica Avanzada Sl",
        query_name="Aplicaciones En Informatica Avanzada",
    )
    state.current_source_trace = SourceStageTrace(
        mode="source",
        missing_fields=["person_name", "role_title", "website"],
        anchored_company="Aplicaciones En Informatica Avanzada Sl",
    )
    state.current_source_result = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        documents=[
            EvidenceDocument(
                url="https://aia.es/about",
                title="AIA about",
                snippet="Company website",
                source_type="fixture",
                raw_content="Company profile without person",
                source_tier="tier_a",
                is_company_controlled_source=True,
            ),
            EvidenceDocument(
                url="https://www.datoscif.es/directivo/llopis-rivas-regina-maria",
                title="Llopis Rivas Regina Maria - Informe de directivo",
                snippet="Administrador Unico de Aplicaciones En Informatica Avanzada Sl",
                source_type="fixture",
                raw_content="LLOPIS RIVAS REGINA MARIA. Administrador Unico. Aplicaciones En Informatica Avanzada Sl.",
                source_tier="tier_b",
                is_company_controlled_source=False,
            ),
        ],
        anchored_company_name="Aplicaciones En Informatica Avanzada Sl",
    )

    dossier = stage.execute(state)

    assert dossier.person is not None
    assert dossier.person.full_name_raw == "LLOPIS RIVAS REGINA MARIA"
    assert "Regina" in (dossier.person.full_name or "")
    assert state.current_assembler_trace is not None
    assert state.current_assembler_trace["document_selection_strategy"] == "reranker"
    assert state.current_assembler_trace["input_documents"][0]["url"] == "https://www.datoscif.es/directivo/llopis-rivas-regina-maria"
    assert state.current_assembler_trace["reranker_trace"]["initial"]["rerank_top_results"][0]["url"] == "https://www.datoscif.es/directivo/llopis-rivas-regina-maria"


def test_enrich_reranks_filtered_results_for_missing_person() -> None:
    reranker = FakeRerankerPort(
        [
            "https://www.datoscif.es/directivo/llopis-rivas-regina-maria",
            "https://aia.es/about",
        ]
    )
    stage = EnrichStage(
        search_port=None,  # unused by the direct helper call
        agent_executor=StageAgentExecutor(None),
        max_results=5,
        reranker=reranker,
    )
    state = _request_and_state()
    dossier = AssembledLeadDossier(
        sourcing_status=SourcingStatus.FOUND,
        company=CompanyCandidate(name="Aplicaciones En Informatica Avanzada Sl", website="https://aia.es", country_code="es", employee_estimate=40),
        person=PersonCandidate(full_name=None, role_title=None),
        field_evidence=[
            AssembledFieldEvidence(field_name="company_name", value="Aplicaciones En Informatica Avanzada Sl", status=FieldEvidenceStatus.SATISFIED, reasoning_note="company"),
            AssembledFieldEvidence(field_name="employee_estimate", value=40, status=FieldEvidenceStatus.SATISFIED, reasoning_note="size"),
            AssembledFieldEvidence(field_name="person_name", value=None, status=FieldEvidenceStatus.UNKNOWN, reasoning_note="missing"),
            AssembledFieldEvidence(field_name="role_title", value=None, status=FieldEvidenceStatus.UNKNOWN, reasoning_note="missing"),
        ],
    )
    query = ResearchQuery(
        query='"Aplicaciones En Informatica Avanzada Sl" administradores cargos directivos',
        objective="Find a named administrator or executive tied to the anchored company.",
        research_phase="contact_resolution",
        source_role="governance_resolution",
        candidate_company_name="Aplicaciones En Informatica Avanzada Sl",
        source_tier_target="tier_b",
        expected_field="person_name",
    )
    documents = [
        EvidenceDocument(
            url="https://aia.es/about",
            title="AIA about",
            snippet="Company website",
            source_type="fixture",
            raw_content="Company profile without person",
            source_tier="tier_a",
            is_company_controlled_source=True,
        ),
        EvidenceDocument(
            url="https://www.datoscif.es/directivo/llopis-rivas-regina-maria",
            title="Llopis Rivas Regina Maria - Informe de directivo",
            snippet="Administrador Unico de Aplicaciones En Informatica Avanzada Sl",
            source_type="fixture",
            raw_content="LLOPIS RIVAS REGINA MARIA. Administrador Unico. Aplicaciones En Informatica Avanzada Sl.",
            source_tier="tier_b",
        ),
    ]

    ordered, reranker_trace = stage._rerank_filtered_results(documents, query=query, state=state, dossier=dossier)

    assert [item.url for item in ordered] == [
        "https://www.datoscif.es/directivo/llopis-rivas-regina-maria",
        "https://aia.es/about",
    ]
    assert reranker_trace is not None
    assert reranker_trace["rerank_candidates_count"] == 2
