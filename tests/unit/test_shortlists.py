import json

from lolo_lead_management.application.use_cases import get_shortlist, get_shortlist_option, select_shortlist_option
from lolo_lead_management.domain.enums import MatchType, QualificationOutcome
from lolo_lead_management.domain.models import (
    CloseMatch,
    CommercialBundle,
    EvidenceItem,
    LeadSearchStartRequest,
    NormalizedLeadSearchRequest,
    QualificationDecision,
    SearchRunSnapshot,
    ShortlistOption,
    ShortlistRecord,
)
from tests.helpers import build_test_container, close_match_candidate_fixture, workspace_tmp_dir


def test_shortlist_detail_exposes_close_match_reasons() -> None:
    tmp_path = workspace_tmp_dir("shortlist-detail")
    search_index, pages = close_match_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages)
    response = container.engine.start(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )

    shortlist = get_shortlist(container, response.shortlist_id)
    option = get_shortlist_option(container, response.shortlist_id, 1)

    assert shortlist is not None
    assert len(shortlist.options) == 1
    assert option is not None
    assert option.company_name == "Bravo Dev"
    assert option.close_match.summary
    assert option.close_match.reasons
    assert option.evidence


def test_selecting_shortlist_option_persists_to_crm() -> None:
    tmp_path = workspace_tmp_dir("shortlist-crm")
    search_index, pages = close_match_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages)
    response = container.engine.start(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )

    promoted = select_shortlist_option(container, response.shortlist_id, 1)

    assert promoted is not None
    assert len(promoted.accepted_leads) == 1
    assert promoted.accepted_leads[0].company_name == "Bravo Dev"
    assert promoted.accepted_leads[0].evidence
    assert container.shortlist_store.get_pending_shortlist(response.shortlist_id) is None

    with container.database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM crm_records WHERE run_id = ? ORDER BY row_id DESC LIMIT 1",
            (promoted.run_id,),
        ).fetchone()

    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["company_name"] == "Bravo Dev"
    assert payload["qualification"]["outcome"] == QualificationOutcome.REJECT_CLOSE_MATCH.value
    assert payload["commercial"]["email_body"]
    assert payload["evidence"]


def test_selecting_one_shortlist_option_keeps_remaining_options_available() -> None:
    tmp_path = workspace_tmp_dir("shortlist-partial-select")
    container = build_test_container(tmp_path)

    request = NormalizedLeadSearchRequest(user_text="busca 2 leads en espana")
    run = SearchRunSnapshot(request=request)
    evidence = [
        EvidenceItem(
            url="https://alpha.dev/about",
            title="Alpha leadership",
            snippet="Alpha CTO",
            source_type="fixture",
        )
    ]
    commercial = CommercialBundle(
        source_notes="Evidence-based source notes.",
        hooks=["Hook 1"],
        fit_summary="Fit summary.",
        connection_note_draft="Connection note.",
        dm_draft="DM draft.",
        email_subject="Subject",
        email_body="Body",
    )
    qualification = QualificationDecision(
        outcome=QualificationOutcome.REJECT_CLOSE_MATCH,
        match_type=MatchType.CLOSE,
        score=74,
        summary="Close match.",
        reasons=["Relevant technical buyer persona."],
        type="head_of_engineering",
        region="es",
        close_match=CloseMatch(
            summary="Matches Spain and size, but role is slightly off.",
            missed_filters=["persona_exact"],
            reasons=["Engineering Director instead of CTO."],
        ),
    )
    option_one = ShortlistOption(
        option_number=1,
        company_name="Alpha Dev",
        person_name="Ana Ruiz",
        role_title="Engineering Director",
        website="https://alpha.dev",
        country_code="es",
        lead_source_type="speaker_or_event",
        person_confidence="corroborated",
        primary_person_source_url="https://events.example.com/ana-ruiz",
        summary="Good close match.",
        close_match=qualification.close_match,
        qualification=qualification,
        commercial=commercial,
        evidence=evidence,
    )
    option_two = ShortlistOption(
        option_number=2,
        company_name="Beta Dev",
        person_name="Luis Gomez",
        role_title="Head of Engineering",
        website="https://beta.dev",
        country_code="es",
        summary="Another close match.",
        close_match=qualification.close_match,
        qualification=qualification,
        commercial=commercial,
        evidence=evidence,
    )
    shortlist = ShortlistRecord(run_id=run.run_id, options=[option_one, option_two])
    run.shortlist_id = shortlist.shortlist_id
    run.shortlist_options = shortlist.options
    container.run_store.save_run(run)
    container.shortlist_store.save_pending_shortlist(shortlist)

    promoted = select_shortlist_option(container, shortlist.shortlist_id, 1)
    remaining = container.shortlist_store.get_pending_shortlist(shortlist.shortlist_id)

    assert promoted is not None
    assert [item.option_number for item in promoted.shortlist_options] == [2]
    assert remaining is not None
    assert [item.option_number for item in remaining.options] == [2]
    assert promoted.accepted_leads[-1].company_name == "Alpha Dev"
    assert promoted.accepted_leads[-1].website == "https://alpha.dev"
    assert promoted.accepted_leads[-1].lead_source_type == "speaker_or_event"
    assert promoted.accepted_leads[-1].person_confidence == "corroborated"
    assert promoted.accepted_leads[-1].primary_person_source_url == "https://events.example.com/ana-ruiz"
