from pathlib import Path

from lolo_lead_management.domain.enums import SourcingStatus
from lolo_lead_management.domain.models import (
    EvidenceDocument,
    LeadSearchStartRequest,
    LogicalSegment,
    SearchRunSnapshot,
    SourcePassResult,
)
from lolo_lead_management.engine.agents.executor import StageAgentExecutor
from lolo_lead_management.engine.stages.assemble import AssembleStage
from lolo_lead_management.engine.stages.chunkerize import ChunkerizeStage
from lolo_lead_management.engine.stages.normalize import NormalizeStage
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.infrastructure.run_archive import ExecutionArchiveWriter
from tests.helpers import workspace_tmp_dir


class CaptureChunkPayloadLlmPort:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def generate_json(self, *, agent_name: str, system_prompt: str, input_payload: dict, schema: dict) -> dict:
        _ = (agent_name, system_prompt, schema)
        self.payloads.append(input_payload)
        if input_payload["mode"] == "focus_locked_chunk_mode":
            return {
                "segment_company_name": "Saas Level Up 2019 Sociedad Limitada",
                "field_assertions": [],
                "contact_assertions": [],
                "fit_signals": [],
                "contradictions": [],
                "notes": [],
            }
        raise AssertionError(f"unexpected mode {input_payload['mode']}")


def _normalized_request():
    return NormalizeStage(StageAgentExecutor(None)).execute(
        LeadSearchStartRequest(user_text="busca 1 lead CTO en espana entre 5 y 50 empleados con genai")
    )


def test_execution_archive_writer_writes_markdown_artifact() -> None:
    tmp_path = workspace_tmp_dir("chunker-writer")
    writer = ExecutionArchiveWriter(str(tmp_path))

    path = writer.write_text(
        kind="chunkerize-debug",
        run_id="run_test123",
        slug="empresite-saas-level-up",
        text="# Chunker Debug\n",
    )

    assert path.suffix == ".md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Chunker Debug\n"


def test_chunkerize_stage_builds_segments_and_debug_markdown_for_empresite_html() -> None:
    tmp_path = workspace_tmp_dir("chunker-empresite")
    writer = ExecutionArchiveWriter(str(tmp_path))
    stage = ChunkerizeStage(archive_writer=writer)
    html = """
    <html>
      <body>
        <h1>Saas Level Up 2019 Sociedad Limitada.</h1>
        <h2>Informacion general</h2>
        <p>Razon social</p>
        <p>Saas Level Up 2019 Sociedad Limitada.</p>
        <p>CIF</p>
        <p>B24723454</p>
        <h2>Direccion y contacto</h2>
        <p>Web</p>
        <p>www.saaslevelup.com</p>
        <h2>Datos comerciales</h2>
        <p>Numero de empleados</p>
        <p>6 (ano 2025)</p>
        <h2>Empresas similares</h2>
        <p>Otra Empresa SL</p>
      </body>
    </html>
    """
    document = EvidenceDocument(
        url="https://empresite.eleconomista.es/SAAS-LEVEL-UP-2019.html",
        title="Saas Level Up 2019 Sociedad Limitada. - Teléfono y dirección | Empresite",
        snippet="Ficha de empresa",
        source_type="fixture",
        raw_content="Saas Level Up 2019 Sociedad Limitada.\nWeb\nwww.saaslevelup.com\nNumero de empleados\n6",
        raw_html=html,
        content_format="html",
    )
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=_normalized_request()),
        current_source_result=SourcePassResult(sourcing_status=SourcingStatus.FOUND, documents=[document]),
    )

    result = stage.execute(state)

    assert len(result.documents) == 1
    chunked = result.documents[0]
    assert chunked.chunker_adapter == "empresite"
    assert len(chunked.normalized_blocks) >= 6
    assert any(segment.segment_type == "identity" for segment in chunked.logical_segments)
    assert any(segment.segment_type == "contact" for segment in chunked.logical_segments)
    assert any(segment.segment_type == "employees" for segment in chunked.logical_segments)
    assert any(segment.segment_type == "noise" for segment in chunked.logical_segments)
    assert chunked.debug_markdown_artifact_path is not None
    artifact = Path(chunked.debug_markdown_artifact_path)
    assert artifact.exists()
    markdown = artifact.read_text(encoding="utf-8")
    assert "# Chunker Debug" in markdown
    assert "## Segment Map" in markdown
    assert "## Normalized Content" in markdown
    assert "Empresas similares" in markdown
    assert result.source_trace is not None
    snapshot = result.source_trace.documents_passed_to_assembler[0]
    assert snapshot.has_raw_html is True
    assert snapshot.logical_segment_count >= 3
    assert snapshot.debug_markdown_artifact_path == chunked.debug_markdown_artifact_path


def test_text_heading_detection_is_conservative_for_value_lines() -> None:
    stage = ChunkerizeStage()

    assert stage._looks_like_heading_line("Informacion general") is True
    assert stage._looks_like_heading_line("Direccion y contacto") is True
    assert stage._looks_like_heading_line("B24723454") is False
    assert stage._looks_like_heading_line("6 (ano 2025)") is False
    assert stage._looks_like_heading_line("Entre 3 y 6 millones €") is False
    assert stage._looks_like_heading_line("www.saaslevelup.com") is False


def test_assemble_uses_logical_segments_before_raw_content() -> None:
    llm = CaptureChunkPayloadLlmPort()
    stage = AssembleStage(StageAgentExecutor(llm))
    document = EvidenceDocument(
        url="https://empresite.eleconomista.es/SAAS-LEVEL-UP-2019.html",
        title="Saas Level Up 2019 Sociedad Limitada.",
        snippet="Ficha de empresa",
        source_type="fixture",
        raw_content="X" * 6000,
        logical_segments=[
            LogicalSegment(
                segment_id="seg_1",
                segment_type="identity",
                start_block=1,
                end_block=2,
                heading_path=["Informacion general"],
                text="Razon social\nSaas Level Up 2019 Sociedad Limitada.\nCIF\nB24723454",
            ),
            LogicalSegment(
                segment_id="seg_2",
                segment_type="contact",
                start_block=3,
                end_block=4,
                heading_path=["Direccion y contacto"],
                text="Web\nwww.saaslevelup.com\nEmail\ninfo@saaslevelup.com",
            ),
        ],
    )
    source_result = SourcePassResult(sourcing_status=SourcingStatus.FOUND, documents=[document])
    state = EngineRuntimeState(
        run=SearchRunSnapshot(request=_normalized_request()),
        current_source_result=source_result,
    )

    stage.execute(state)

    assert llm.payloads
    chunk_payload = llm.payloads[0]["chunk"]
    assert chunk_payload["segment_type"] == "identity"
    assert chunk_payload["heading_path"] == ["Informacion general"]
    assert "Segment type: identity" in chunk_payload["text"]
    assert "Razon social" in chunk_payload["text"]
