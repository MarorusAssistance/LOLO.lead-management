import json

from lolo_lead_management.application.use_cases import get_shortlist, get_shortlist_option, select_shortlist_option
from lolo_lead_management.domain.enums import MatchType, QualificationOutcome
from lolo_lead_management.domain.models import (
    CompanyCandidate,
    CloseMatch,
    CommercialBundle,
    ContactCandidate,
    EvidenceItem,
    LeadSearchStartRequest,
    NormalizedLeadSearchRequest,
    PersonCandidate,
    QualificationDecision,
    SearchRunSnapshot,
    ShortlistOption,
    ShortlistRecord,
    SourcingDossier,
)
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from tests.helpers import FixtureLeadLlmPort, build_test_container, close_match_candidate_fixture, workspace_tmp_dir


def test_shortlist_detail_exposes_close_match_reasons() -> None:
    tmp_path = workspace_tmp_dir("shortlist-detail")
    search_index, pages = close_match_candidate_fixture()
    container = build_test_container(tmp_path, search_index=search_index, pages=pages, llm_port=FixtureLeadLlmPort())
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
    container = build_test_container(tmp_path, search_index=search_index, pages=pages, llm_port=FixtureLeadLlmPort())
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
        alternate_contacts=[
            ContactCandidate(
                full_name="Luis Perez",
                full_name_raw="Perez Luis",
                role_title="CTO",
                primary_person_source_url="https://alpha.dev/team/luis-perez",
                support_urls=["https://alpha.dev/team/luis-perez"],
            )
        ],
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
    assert promoted.accepted_leads[-1].alternate_contacts[0].full_name == "Luis Perez"
    assert promoted.accepted_leads[-1].alternate_contacts[0].primary_person_source_url == "https://alpha.dev/team/luis-perez"


def test_shortlist_uses_normalized_person_name_for_display() -> None:
    tmp_path = workspace_tmp_dir("shortlist-normalized-person-name")
    container = build_test_container(tmp_path)
    request = NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )
    run = SearchRunSnapshot(request=request)
    qualification = QualificationDecision(
        outcome=QualificationOutcome.REJECT_CLOSE_MATCH,
        match_type=MatchType.CLOSE,
        score=90,
        summary="Close match with legal fallback contact.",
        reasons=["preferred buyer persona is still weakly supported or unknown"],
        type="Administrador único",
        region="es",
        close_match=CloseMatch(
            summary="Commercially interesting candidate with exact-match gaps.",
            missed_filters=["preferred buyer persona"],
            reasons=["preferred buyer persona is still weakly supported or unknown"],
        ),
    )
    commercial = CommercialBundle(
        source_notes="Evidence-based source notes.",
        hooks=["Hook 1"],
        fit_summary="Fit summary.",
        connection_note_draft="Connection note.",
        dm_draft="DM draft.",
        email_subject="Subject",
        email_body="Body",
    )
    dossier = SourcingDossier(
        sourcing_status="FOUND",
        person=PersonCandidate(
            full_name="Agustin Iglesias Villacampa",
            full_name_raw="Iglesias Villacampa Agustin",
            role_title="Administrador único",
        ),
        alternate_contacts=[
            ContactCandidate(
                full_name="Jordi Zanca Soler",
                full_name_raw="Zanca Soler Jordi",
                role_title="Administrador Único",
                primary_person_source_url="https://www.datoscif.es/empresa/bee-the-data-sl",
                support_urls=["https://www.datoscif.es/empresa/bee-the-data-sl"],
            )
        ],
        company=CompanyCandidate(
            name="Bee The Data Sl",
            website="http://www.beethedata.com",
            country_code="es",
            employee_estimate=11,
        ),
        lead_source_type="mercantile_directory",
        person_confidence="strong",
        primary_person_source_url="https://www.datoscif.es/empresa/bee-the-data-sl",
        evidence=[
            EvidenceItem(
                url="https://www.datoscif.es/empresa/bee-the-data-sl",
                title="Bee The Data SL - DatosCif",
                snippet="Apoderado Solidario: Iglesias Villacampa Agustin",
                source_type="fixture",
            )
        ],
        field_evidence=[],
        contradictions=[],
        notes=[],
    )
    state = EngineRuntimeState(
        run=run,
        current_dossier=dossier,
        current_qualification=qualification,
        current_commercial=commercial,
    )

    container.engine._crm_write_stage.execute(state)

    assert state.run.shortlist_options
    option = state.run.shortlist_options[0]
    assert option.person_name == "Agustin Iglesias Villacampa"
    assert option.role_title == "Administrador único"
    assert option.lead_source_type == "mercantile_directory"
    assert option.person_confidence == "strong"
    assert option.primary_person_source_url == "https://www.datoscif.es/empresa/bee-the-data-sl"
    assert option.alternate_contacts[0].full_name == "Jordi Zanca Soler"
    assert option.alternate_contacts[0].primary_person_source_url == "https://www.datoscif.es/empresa/bee-the-data-sl"
