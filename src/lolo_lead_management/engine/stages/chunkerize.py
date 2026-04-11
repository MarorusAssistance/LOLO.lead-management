from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

from lolo_lead_management.domain.models import (
    ChunkerDocumentTrace,
    ChunkerStageTrace,
    DocumentBlock,
    EvidenceDocument,
    LogicalSegment,
    SourcePassResult,
    SourceStageTrace,
    SourceTraceDocumentSnapshot,
)
from lolo_lead_management.domain.enums import SourcingStatus
from lolo_lead_management.engine.rules import dedupe_preserve_order, domain_from_url, normalize_text
from lolo_lead_management.engine.state import EngineRuntimeState
from lolo_lead_management.infrastructure.run_archive import ExecutionArchiveWriter


CHUNKER_VERSION = "logical-chunker-v1"
SEGMENT_TYPES = {"identity", "contact", "website", "employees", "governance", "fit", "legal", "faq", "noise", "unknown"}
HEADING_CUE_TOKENS = {
    "about",
    "actividad",
    "administradores",
    "cargos",
    "cif",
    "contacto",
    "cnae",
    "datos",
    "directivos",
    "direccion",
    "domicilio",
    "email",
    "empleados",
    "faq",
    "general",
    "governance",
    "informacion",
    "leadership",
    "legal",
    "mercantil",
    "nombramientos",
    "objeto",
    "overview",
    "pagina",
    "preguntas",
    "profile",
    "rankings",
    "ranking",
    "razon",
    "registro",
    "sector",
    "social",
    "team",
    "telefono",
    "web",
    "website",
}


@dataclass(frozen=True)
class ChunkAdapterProfile:
    name: str
    domains: tuple[str, ...]
    segment_rules: tuple[tuple[str, str], ...]
    noise_keywords: tuple[str, ...] = ()

    def supports(self, domain: str | None) -> bool:
        normalized = normalize_text(domain or "")
        return bool(normalized and any(normalized == item or normalized.endswith(f".{item}") for item in self.domains))


GENERIC_SEGMENT_RULES = (
    ("empresas similares", "noise"),
    ("other companies", "noise"),
    ("vinculaciones", "noise"),
    ("ranking", "noise"),
    ("publicidad", "noise"),
    ("advertisement", "noise"),
    ("faq", "faq"),
    ("preguntas frecuentes", "faq"),
    ("borme", "legal"),
    ("boe", "legal"),
    ("registro mercantil", "legal"),
    ("datos identificativos", "identity"),
    ("informacion general", "identity"),
    ("datos de empresa", "identity"),
    ("razon social", "identity"),
    ("cif", "identity"),
    ("nif", "identity"),
    ("direccion y contacto", "contact"),
    ("contacto", "contact"),
    ("direccion", "contact"),
    ("telefono", "contact"),
    ("email", "contact"),
    ("sitio web", "website"),
    ("pagina web", "website"),
    ("web", "website"),
    ("dominio", "website"),
    ("empleados", "employees"),
    ("plantilla", "employees"),
    ("datos comerciales", "employees"),
    ("consejo de administracion", "governance"),
    ("administradores", "governance"),
    ("directivos", "governance"),
    ("cargos", "governance"),
    ("organo administracion", "governance"),
    ("objeto social", "fit"),
    ("actividad", "fit"),
    ("cnae", "fit"),
    ("sector", "fit"),
    ("overview", "fit"),
    ("about", "fit"),
)


ADAPTER_PROFILES = (
    ChunkAdapterProfile(
        name="empresite",
        domains=("empresite.eleconomista.es",),
        segment_rules=GENERIC_SEGMENT_RULES,
        noise_keywords=("empresas similares", "rankings", "quienes somos", "anade tu empresa"),
    ),
    ChunkAdapterProfile(
        name="datoscif",
        domains=("datoscif.es",),
        segment_rules=GENERIC_SEGMENT_RULES,
        noise_keywords=("vinculaciones", "empresas similares"),
    ),
    ChunkAdapterProfile(
        name="infoempresa",
        domains=("infoempresa.com",),
        segment_rules=GENERIC_SEGMENT_RULES,
        noise_keywords=("otras empresas", "productos", "soluciones"),
    ),
    ChunkAdapterProfile(
        name="einforma",
        domains=("einforma.com",),
        segment_rules=GENERIC_SEGMENT_RULES,
        noise_keywords=("quiero mi informe gratuito", "advertisement", "regstrate", "registrate"),
    ),
    ChunkAdapterProfile(
        name="iberinform",
        domains=("iberinform.es",),
        segment_rules=GENERIC_SEGMENT_RULES,
        noise_keywords=("productos", "soluciones", "blog"),
    ),
    ChunkAdapterProfile(
        name="axesor",
        domains=("axesor.es",),
        segment_rules=GENERIC_SEGMENT_RULES,
        noise_keywords=("testimonios", "opiniones", "productos"),
    ),
    ChunkAdapterProfile(
        name="borme_text",
        domains=("boe.es",),
        segment_rules=GENERIC_SEGMENT_RULES,
        noise_keywords=(),
    ),
)


class _HtmlBlockParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg"}
    _HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    _BLOCK_TYPES = {
        "p": "paragraph",
        "li": "list_item",
        "tr": "table_row",
        "article": "paragraph",
        "section": "paragraph",
        "main": "paragraph",
        "aside": "paragraph",
        "div": "paragraph",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[DocumentBlock] = []
        self._skip_depth = 0
        self._current_text: list[str] = []
        self._current_type: str | None = None
        self._current_heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        normalized = tag.lower()
        if normalized in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if normalized == "br":
            self._current_text.append("\n")
            return
        if normalized in self._HEADING_LEVELS:
            self._flush()
            self._current_type = "heading"
            self._current_heading_level = self._HEADING_LEVELS[normalized]
            return
        block_type = self._BLOCK_TYPES.get(normalized)
        if block_type:
            self._flush()
            self._current_type = block_type
            self._current_heading_level = None
            return
        if normalized in {"td", "th"} and self._current_type == "table_row" and self._current_text:
            self._current_text.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if normalized in self._HEADING_LEVELS or normalized in self._BLOCK_TYPES or normalized in {"table", "ul", "ol"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data and data.strip():
            self._current_text.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = self._clean_text("".join(self._current_text))
        if text:
            block_type = self._current_type or "paragraph"
            self.blocks.append(
                DocumentBlock(
                    index=len(self.blocks) + 1,
                    block_type=block_type if block_type in {"heading", "paragraph", "list_item", "table_row"} else "unknown",
                    text=text,
                    heading_level=self._current_heading_level,
                )
            )
        self._current_text = []
        self._current_type = None
        self._current_heading_level = None

    def _clean_text(self, text: str) -> str:
        collapsed = re.sub(r"[ \t\r\f\v]+", " ", text or "")
        collapsed = re.sub(r"\s*\|\s*", " | ", collapsed)
        collapsed = re.sub(r"\s+", " ", collapsed)
        return collapsed.strip(" |")


class ChunkerizeStage:
    def __init__(self, *, archive_writer: ExecutionArchiveWriter | None = None) -> None:
        self._archive_writer = archive_writer
        self.last_trace: ChunkerStageTrace | None = None

    def execute(self, state: EngineRuntimeState) -> SourcePassResult:
        source_result = state.current_source_result
        if source_result is None or not source_result.documents:
            self.last_trace = ChunkerStageTrace(notes=["no_documents_to_chunkerize"])
            return source_result or SourcePassResult(sourcing_status=SourcingStatus.NO_CANDIDATE)

        traces: list[ChunkerDocumentTrace] = []
        chunked_documents: list[EvidenceDocument] = []
        for document in source_result.documents:
            chunked, trace = self._chunk_document(document, run_id=state.run.run_id)
            chunked_documents.append(chunked)
            traces.append(trace)

        updated_trace = self._update_source_trace(source_result.source_trace, chunked_documents)
        notes = dedupe_preserve_order([*source_result.notes, f"chunkerized_documents={len(chunked_documents)}"])
        self.last_trace = ChunkerStageTrace(processed_documents=traces, notes=notes)
        return source_result.model_copy(
            update={
                "documents": chunked_documents,
                "source_trace": updated_trace,
                "notes": notes,
            }
        )

    def _chunk_document(self, document: EvidenceDocument, *, run_id: str) -> tuple[EvidenceDocument, ChunkerDocumentTrace]:
        fingerprint = self._content_fingerprint(document)
        if (
            document.chunker_version == CHUNKER_VERSION
            and document.content_fingerprint == fingerprint
            and document.normalized_blocks
            and document.logical_segments
        ):
            return document, ChunkerDocumentTrace(
                url=document.url,
                domain=document.domain or domain_from_url(document.url),
                adapter=document.chunker_adapter,
                had_raw_html=bool(document.raw_html),
                normalized_block_count=len(document.normalized_blocks),
                logical_segment_count=len(document.logical_segments),
                debug_markdown_artifact_path=document.debug_markdown_artifact_path,
                debug_markdown_preview=document.debug_markdown_preview,
                notes=["chunker_cache_hit"],
            )

        domain = document.domain or domain_from_url(document.url)
        adapter = self._adapter_for(document)
        blocks = self._normalize_blocks(document, adapter=adapter)
        segments = self._build_segments(blocks, adapter=adapter, url=document.url)
        markdown = self._render_debug_markdown(document=document, adapter=adapter.name, blocks=blocks, segments=segments, fingerprint=fingerprint)
        artifact_path, artifact_error = self._persist_debug_markdown(run_id=run_id, document=document, markdown=markdown)
        preview = markdown[:500]
        updated = document.model_copy(
            update={
                "content_format": "html" if document.raw_html else ("text" if (document.raw_content or document.snippet or document.title) else "unknown"),
                "normalized_blocks": blocks,
                "logical_segments": segments,
                "chunker_version": CHUNKER_VERSION,
                "content_fingerprint": fingerprint,
                "chunker_adapter": adapter.name,
                "debug_markdown_artifact_path": artifact_path,
                "debug_markdown_preview": preview,
            }
        )
        notes = []
        if artifact_error:
            notes.append("chunker_debug_archive_failed")
        if not blocks:
            notes.append("no_normalized_blocks")
        if not segments:
            notes.append("no_logical_segments")
        trace = ChunkerDocumentTrace(
            url=document.url,
            domain=domain,
            adapter=adapter.name,
            had_raw_html=bool(document.raw_html),
            normalized_block_count=len(blocks),
            logical_segment_count=len(segments),
            debug_markdown_artifact_path=artifact_path,
            debug_markdown_preview=preview,
            notes=notes,
        )
        return updated, trace

    def _adapter_for(self, document: EvidenceDocument) -> ChunkAdapterProfile:
        domain = document.domain or domain_from_url(document.url)
        for profile in ADAPTER_PROFILES:
            if profile.name == "borme_text" and profile.supports(domain) and "borme" in normalize_text(document.url):
                return profile
            if profile.supports(domain) and profile.name != "borme_text":
                return profile
        return ChunkAdapterProfile(name="generic", domains=(), segment_rules=GENERIC_SEGMENT_RULES)

    def _normalize_blocks(self, document: EvidenceDocument, *, adapter: ChunkAdapterProfile) -> list[DocumentBlock]:
        if document.raw_html:
            blocks = self._html_to_blocks(document.raw_html)
            if blocks:
                return self._attach_heading_paths(blocks)
        text = document.raw_content or document.snippet or document.title or ""
        if adapter.name == "borme_text":
            return self._attach_heading_paths(self._borme_text_to_blocks(text))
        return self._attach_heading_paths(self._text_to_blocks(text))

    def _html_to_blocks(self, html: str) -> list[DocumentBlock]:
        parser = _HtmlBlockParser()
        parser.feed(html)
        parser.close()
        return parser.blocks

    def _text_to_blocks(self, text: str) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        paragraph_lines: list[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph_lines
            if paragraph_lines:
                blocks.append(
                    DocumentBlock(
                        index=len(blocks) + 1,
                        block_type="paragraph",
                        text=" ".join(line.strip() for line in paragraph_lines if line.strip()),
                    )
                )
                paragraph_lines = []

        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line:
                flush_paragraph()
                continue
            if self._looks_like_heading_line(line):
                flush_paragraph()
                blocks.append(
                    DocumentBlock(
                        index=len(blocks) + 1,
                        block_type="heading",
                        text=line,
                        heading_level=2,
                    )
                )
                continue
            if re.match(r"^\d+[.)]\s+", line):
                flush_paragraph()
                blocks.append(
                    DocumentBlock(
                        index=len(blocks) + 1,
                        block_type="list_item",
                        text=re.sub(r"^\d+[.)]\s*", "", line),
                    )
                )
                continue
            paragraph_lines.append(line)
        flush_paragraph()
        return blocks

    def _borme_text_to_blocks(self, text: str) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        current_lines: list[str] = []
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line:
                if current_lines:
                    blocks.append(DocumentBlock(index=len(blocks) + 1, block_type="paragraph", text=" ".join(current_lines)))
                    current_lines = []
                continue
            normalized = normalize_text(line)
            if normalized in {"constitucion", "nombramientos", "ceses", "revocaciones", "cambio de domicilio", "datos registrales"}:
                if current_lines:
                    blocks.append(DocumentBlock(index=len(blocks) + 1, block_type="paragraph", text=" ".join(current_lines)))
                    current_lines = []
                blocks.append(DocumentBlock(index=len(blocks) + 1, block_type="heading", text=line, heading_level=2))
            else:
                current_lines.append(line)
        if current_lines:
            blocks.append(DocumentBlock(index=len(blocks) + 1, block_type="paragraph", text=" ".join(current_lines)))
        return blocks

    def _attach_heading_paths(self, blocks: list[DocumentBlock]) -> list[DocumentBlock]:
        path: dict[int, str] = {}
        attached: list[DocumentBlock] = []
        for block in blocks:
            if block.block_type == "heading":
                level = block.heading_level or 2
                path = {item_level: item_text for item_level, item_text in path.items() if item_level < level}
                path[level] = block.text
                attached.append(block.model_copy(update={"heading_path": [path[key] for key in sorted(path)]}))
            else:
                attached.append(block.model_copy(update={"heading_path": [path[key] for key in sorted(path)]}))
        return attached

    def _build_segments(self, blocks: list[DocumentBlock], *, adapter: ChunkAdapterProfile, url: str) -> list[LogicalSegment]:
        if not blocks:
            return []
        grouped: list[list[DocumentBlock]] = []
        current: list[DocumentBlock] = []
        for block in blocks:
            if block.block_type == "heading" and current:
                grouped.append(current)
                current = [block]
            else:
                current.append(block)
        if current:
            grouped.append(current)

        segments: list[LogicalSegment] = []
        for chunk in grouped:
            text = "\n\n".join(self._markdown_for_block(block) for block in chunk).strip()
            if not text:
                continue
            heading_path = chunk[0].heading_path or ([chunk[0].text] if chunk[0].block_type == "heading" else [])
            segment_type = self._segment_type(heading_path, text, adapter=adapter, url=url)
            noise = segment_type == "noise"
            segments.append(
                LogicalSegment(
                    segment_id=f"seg_{len(segments) + 1}",
                    segment_type=segment_type if segment_type in SEGMENT_TYPES else "unknown",
                    start_block=chunk[0].index,
                    end_block=chunk[-1].index,
                    heading_path=heading_path,
                    noise=noise,
                    discard_reason="noise_section" if noise else None,
                    text=text,
                )
            )
        return segments

    def _segment_type(self, heading_path: list[str], text: str, *, adapter: ChunkAdapterProfile, url: str) -> str:
        heading_text = normalize_text(" ".join(heading_path))
        content = normalize_text(text)
        if adapter.name == "borme_text" or "borme" in normalize_text(url):
            if any(token in content for token in ["nombramientos", "administrador", "apoderado", "consejero"]):
                return "governance"
            return "legal"
        for keyword, segment_type in adapter.segment_rules:
            if keyword in heading_text or keyword in content:
                return segment_type
        if any(keyword in heading_text for keyword in adapter.noise_keywords):
            return "noise"
        return "unknown"

    def _persist_debug_markdown(self, *, run_id: str, document: EvidenceDocument, markdown: str) -> tuple[str | None, str | None]:
        if self._archive_writer is None:
            return None, "archive_writer_unavailable"
        slug = (document.title or document.url or "document").replace("https://", "").replace("http://", "")
        try:
            path = self._archive_writer.write_text(
                kind="chunkerize-debug",
                run_id=run_id,
                slug=slug,
                text=markdown,
                extension="md",
            )
            return str(path), None
        except Exception as exc:  # pragma: no cover - best effort debug path
            return None, str(exc)

    def _render_debug_markdown(
        self,
        *,
        document: EvidenceDocument,
        adapter: str,
        blocks: list[DocumentBlock],
        segments: list[LogicalSegment],
        fingerprint: str,
    ) -> str:
        lines = [
            "# Chunker Debug",
            "",
            f"- url: {document.url}",
            f"- domain: {document.domain or domain_from_url(document.url) or ''}",
            f"- adapter: {adapter}",
            f"- content_fingerprint: {fingerprint}",
            f"- chunker_version: {CHUNKER_VERSION}",
            f"- has_raw_html: {str(bool(document.raw_html)).lower()}",
            "",
            "## Segment Map",
        ]
        for segment in segments:
            heading = " > ".join(segment.heading_path) if segment.heading_path else "(no heading)"
            lines.append(
                f"- {segment.segment_id}: type={segment.segment_type} noise={str(segment.noise).lower()} "
                f"blocks={segment.start_block}-{segment.end_block} heading={heading}"
            )
        lines.extend(["", "## Normalized Content", ""])
        for block in blocks:
            lines.append(self._markdown_for_block(block))
            lines.append("")
        noise_segments = [segment for segment in segments if segment.noise]
        if noise_segments:
            lines.extend(["## Noise Segments", ""])
            for segment in noise_segments:
                lines.append(f"### {segment.segment_id} [{segment.segment_type}]")
                lines.append(segment.text)
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _markdown_for_block(self, block: DocumentBlock) -> str:
        if block.block_type == "heading":
            level = block.heading_level or 2
            return f"{'#' * max(1, min(level, 6))} {block.text}"
        if block.block_type == "list_item":
            return f"- {block.text}"
        if block.block_type == "table_row":
            cells = [cell.strip() for cell in block.text.split("|") if cell.strip()]
            if len(cells) >= 2:
                return f"| {' | '.join(cells)} |"
            return f"- {block.text}"
        return block.text

    def _content_fingerprint(self, document: EvidenceDocument) -> str:
        payload = document.raw_html or document.raw_content or document.snippet or document.title or document.url
        return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()

    def _looks_like_heading_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        normalized = normalize_text(stripped)
        if normalized in {item for item, _ in GENERIC_SEGMENT_RULES}:
            return True
        if any(marker in normalized for marker in ("http://", "https://", "www.", "@")):
            return False
        if any(symbol in stripped for symbol in "€$£%|"):
            return False
        if re.search(r"\b\d{4,}\b", stripped):
            return False
        words = [token for token in re.split(r"\s+", normalized) if token]
        if (
            words
            and len(stripped) <= 80
            and stripped[-1] not in ".:;"
            and any(token in HEADING_CUE_TOKENS for token in words)
        ):
            return True
        if stripped.isupper() and len(stripped) <= 80 and len(words) <= 8 and not any(char.isdigit() for char in stripped):
            return True
        return False

    def _update_source_trace(
        self,
        trace: SourceStageTrace | None,
        documents: list[EvidenceDocument],
    ) -> SourceStageTrace | None:
        by_url = {item.url: item for item in documents}

        def enrich_snapshot(snapshot: SourceTraceDocumentSnapshot) -> SourceTraceDocumentSnapshot:
            document = by_url.get(snapshot.url)
            if document is None:
                return snapshot
            return snapshot.model_copy(
                update={
                    "has_raw_html": bool(document.raw_html),
                    "content_format": document.content_format,
                    "chunker_adapter": document.chunker_adapter,
                    "chunker_version": document.chunker_version,
                    "normalized_block_count": len(document.normalized_blocks),
                    "logical_segment_count": len(document.logical_segments),
                    "debug_markdown_artifact_path": document.debug_markdown_artifact_path,
                    "debug_markdown_preview": document.debug_markdown_preview,
                }
            )

        if trace is None:
            return SourceStageTrace(
                documents_passed_to_assembler=[
                    enrich_snapshot(
                        SourceTraceDocumentSnapshot(
                            url=item.url,
                            title=item.title,
                            snippet=item.snippet,
                            raw_content=item.raw_content,
                            source_type=item.source_type,
                            domain=item.domain or domain_from_url(item.url),
                            source_tier=item.source_tier,
                            source_quality=item.source_quality,
                            company_anchor=item.company_anchor,
                            is_company_controlled_source=item.is_company_controlled_source,
                        )
                    )
                    for item in documents
                ],
                notes=["chunker_trace_created_without_source_trace"],
            )

        query_traces = []
        for item in trace.query_traces:
            query_traces.append(
                item.model_copy(
                    update={
                        "raw_results_before_filter": [enrich_snapshot(snapshot) for snapshot in item.raw_results_before_filter],
                        "documents_after_enrichment": [enrich_snapshot(snapshot) for snapshot in item.documents_after_enrichment],
                        "documents_selected_for_pass": [enrich_snapshot(snapshot) for snapshot in item.documents_selected_for_pass],
                    }
                )
            )
        return trace.model_copy(
            update={
                "documents_passed_to_assembler": [enrich_snapshot(snapshot) for snapshot in trace.documents_passed_to_assembler],
                "query_traces": query_traces,
            }
        )
