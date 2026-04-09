from fastapi import BackgroundTasks, Response

from lolo_lead_management.api.routes.runs import start_run
from lolo_lead_management.domain.enums import RunStatus, StageName
from lolo_lead_management.domain.models import LeadSearchStartRequest
from tests.helpers import accepted_candidate_fixture, build_test_container, workspace_tmp_dir


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
    container = build_test_container(tmp_path, search_index=search_index, pages=pages)
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
    container = build_test_container(tmp_path, search_index=search_index, pages=pages)
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
    container = build_test_container(tmp_path, search_index=search_index, pages=pages)
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
