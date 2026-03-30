from lolo_lead_management.api.app import create_app
from lolo_lead_management.application.use_cases import select_shortlist_option
from lolo_lead_management.domain.models import LeadSearchStartRequest

from tests.helpers import (
    accepted_candidate_fixture,
    build_test_container,
    close_match_candidate_fixture,
    workspace_tmp_dir,
)


def test_end_to_end_accept_flow() -> None:
    tmp_path = workspace_tmp_dir("smoke-flow")
    search_index, pages = accepted_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages)
    response = container.engine.start(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai")
    )

    assert response.status.value == "completed"
    assert len(response.accepted_leads) == 1
    assert response.accepted_leads[0].commercial.email_body


def test_app_builds_with_container() -> None:
    tmp_path = workspace_tmp_dir("smoke-app")
    app = create_app()
    app.state.container = build_test_container(tmp_path)
    assert app.title == "LOLO Lead Management"
    assert app.docs_url == "/"
    assert app.openapi_url == "/openapi.json"


def test_shortlist_selection_promotes_close_match(tmp_path=None) -> None:
    tmp_path = workspace_tmp_dir("smoke-shortlist")
    search_index, pages = close_match_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages)
    response = container.engine.start(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en españa entre 5 y 50 empleados con genai")
    )

    assert response.shortlist_id is not None
    assert len(response.shortlist_options) == 1

    promoted = select_shortlist_option(container, response.shortlist_id, 1)

    assert promoted is not None
    assert len(promoted.accepted_leads) == 1
    assert promoted.accepted_leads[0].company_name == "Bravo Dev"
