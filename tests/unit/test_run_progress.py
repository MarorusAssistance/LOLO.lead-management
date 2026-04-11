from fastapi import BackgroundTasks, Response

from lolo_lead_management.api.routes.runs import start_run
from lolo_lead_management.domain.enums import RunStatus, SourceQuality, SourcingStatus, StageName
from lolo_lead_management.domain.models import DocumentBlock, EvidenceDocument, LeadSearchStartRequest, LogicalSegment, SourcePassResult, WebsiteCandidateHint
from tests.helpers import FixtureLeadLlmPort, accepted_candidate_fixture, build_test_container, workspace_tmp_dir


def test_initialize_run_exposes_progress_snapshot() -> None:
    tmp_path = workspace_tmp_dir("progress-initialize")
    container = build_test_container(tmp_path)

    run = container.engine.initialize_run(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )

    loaded = container.engine.get_run(run.run_id)

    assert loaded is not None
    assert loaded.status == RunStatus.RUNNING
    assert loaded.current_stage == StageName.NORMALIZE
    assert loaded.progress_message == "Request normalized. Preparing durable search state."
    assert loaded.last_heartbeat_at is not None


def test_async_route_returns_running_response_and_background_task() -> None:
    tmp_path = workspace_tmp_dir("progress-async-route")
    search_index, pages = accepted_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages, llm_port=FixtureLeadLlmPort())
    background_tasks = BackgroundTasks()
    response = Response()

    payload = LeadSearchStartRequest(
        user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai",
        wait_for_completion=False,
    )

    started = start_run(payload=payload, background_tasks=background_tasks, response=response, container=container)

    assert response.status_code == 202
    assert started.status == RunStatus.RUNNING
    assert started.current_stage == StageName.NORMALIZE
    assert started.progress_message == "Request normalized. Preparing durable search state."
    assert len(background_tasks.tasks) == 1


def test_completed_run_keeps_final_progress_message() -> None:
    tmp_path = workspace_tmp_dir("progress-final")
    search_index, pages = accepted_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages, llm_port=FixtureLeadLlmPort())
    run = container.engine.initialize_run(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )

    finished = container.engine.run_to_completion(run.run_id)

    assert finished.status == RunStatus.COMPLETED
    assert finished.current_stage == StageName.CONTINUE_OR_FINISH
    assert finished.progress_message == "Search completed with 1 accepted leads."


def test_completed_run_persists_stage_and_iteration_traces() -> None:
    tmp_path = workspace_tmp_dir("progress-traces")
    search_index, pages = accepted_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages, llm_port=FixtureLeadLlmPort())
    run = container.engine.initialize_run(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )

    finished = container.engine.run_to_completion(run.run_id)

    assert finished.stage_events
    assert finished.stage_events[0].stage == StageName.NORMALIZE
    assert finished.iterations
    iteration = finished.iterations[0]
    assert iteration.source_trace is not None
    assert iteration.source_trace.query_traces
    assert iteration.qualification_trace is not None
    assert iteration.continue_trace is not None


def test_run_budget_exposes_search_call_budget() -> None:
    tmp_path = workspace_tmp_dir("progress-search-budget")
    container = build_test_container(tmp_path)

    run = container.engine.initialize_run(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )

    assert run.budget.search_call_budget == 10


def test_merge_focus_locked_results_preserves_anchored_documents_when_enrich_delta_is_empty() -> None:
    tmp_path = workspace_tmp_dir("progress-merge-enrich-delta")
    container = build_test_container(tmp_path)

    anchored = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Contestio Spain Sl.",
        documents=[
            EvidenceDocument(
                url="https://www.einforma.com/informacion-empresa/contestio-spain",
                title="Contestio Spain SL",
                snippet="Administrador único: CONTESTIO SAS REPRES PJ OLIVIER KILLIAN",
                source_type="fixture",
                source_quality=SourceQuality.MEDIUM,
                raw_content="CONTESTIO SPAIN SL. Administrador único: CONTESTIO SAS REPRES PJ OLIVIER KILLIAN",
            )
        ],
        website_candidates=[
            WebsiteCandidateHint(
                candidate_website="https://contestio.com",
                evidence_urls=["https://www.einforma.com/informacion-empresa/contestio-spain"],
                signals=["brand_domain_candidate"],
                score=0.6,
            )
        ],
        notes=["anchored_source_found"],
    )
    enrich_delta = SourcePassResult(
        sourcing_status=SourcingStatus.NO_CANDIDATE,
        anchored_company_name="Contestio Spain Sl.",
        notes=["enrichment_queries_executed=3"],
    )

    merged = container.engine._merge_focus_locked_results(anchored, enrich_delta)

    assert merged is not None
    assert merged.sourcing_status == SourcingStatus.FOUND
    assert [item.url for item in merged.documents] == [
        "https://www.einforma.com/informacion-empresa/contestio-spain"
    ]
    assert [item.candidate_website for item in merged.website_candidates] == ["https://contestio.com"]
    assert merged.anchored_company_name == "Contestio Spain Sl."
    assert merged.notes == ["anchored_source_found", "enrichment_queries_executed=3"]


def test_merge_focus_locked_results_preserves_chunker_metadata_from_existing_documents() -> None:
    tmp_path = workspace_tmp_dir("progress-merge-chunker-metadata")
    container = build_test_container(tmp_path)

    anchored = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Contestio Spain Sl.",
        documents=[
            EvidenceDocument(
                url="https://www.einforma.com/informacion-empresa/contestio-spain",
                title="Contestio Spain SL",
                snippet="Company profile",
                source_type="fixture",
                raw_content="Contestio Spain SL. Website: https://contestio.com",
                raw_html="<html><body><h1>Contestio Spain SL</h1></body></html>",
                content_format="html",
                normalized_blocks=[DocumentBlock(index=1, block_type="heading", text="Contestio Spain SL", heading_level=1)],
                logical_segments=[
                    LogicalSegment(
                        segment_id="seg_1",
                        segment_type="identity",
                        start_block=1,
                        end_block=1,
                        heading_path=["Contestio Spain SL"],
                        text="# Contestio Spain SL",
                    )
                ],
                chunker_version="logical-chunker-v1",
                content_fingerprint="abc123",
                chunker_adapter="einforma",
                debug_markdown_artifact_path=str(tmp_path / "contestio.md"),
            )
        ],
    )
    enrich_delta = SourcePassResult(
        sourcing_status=SourcingStatus.FOUND,
        anchored_company_name="Contestio Spain Sl.",
        documents=[
            EvidenceDocument(
                url="https://www.einforma.com/informacion-empresa/contestio-spain",
                title="Contestio Spain SL",
                snippet="Updated snippet",
                source_type="fixture",
                raw_content="Contestio Spain SL. Administrador unico.",
            )
        ],
    )

    merged = container.engine._merge_focus_locked_results(anchored, enrich_delta)

    assert merged is not None
    assert len(merged.documents) == 1
    document = merged.documents[0]
    assert document.raw_html == "<html><body><h1>Contestio Spain SL</h1></body></html>"
    assert document.chunker_adapter == "einforma"
    assert document.logical_segments
    assert document.debug_markdown_artifact_path == str(tmp_path / "contestio.md")
