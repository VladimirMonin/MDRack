"""CLI-independent indexing application service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.chunking import StructuralChunker, StructuralChunkingConfig
from mdrack.domain.blocks import BlockType
from mdrack.domain.chunks import RetrievalChunk
from mdrack.domain.documents import Document
from mdrack.domain.identifiers import (
    content_fingerprint,
    logical_id,
    normalize_heading_path,
    safe_file_ref,
)
from mdrack.domain.indexing import IndexingResult, PreparedFile, StoredChunk, StoredSection
from mdrack.domain.profiles import EmbeddingProfile
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.indexing.scanner import CorpusScanError, scan_markdown_files
from mdrack.markdown.chunk_builder import build_chunks
from mdrack.markdown.embedding_text import build_embedding_text
from mdrack.markdown.parser import parse_markdown
from mdrack.markdown.section_builder import build_sections
from mdrack.ports.parser import MarkdownParser
from mdrack.ports.storage import IndexStorage

logger = logging.getLogger(__name__)

LEGACY_PARSER_NAME = "legacy_markdown"
LEGACY_PARSER_VERSION = "1"
LEGACY_CHUNK_STRATEGY_NAME = "buffered_blocks"
LEGACY_CHUNK_STRATEGY_VERSION = "1"


class IndexingService:
    """Orchestrate scanning and indexing against an injected storage port."""

    def __init__(
        self,
        root: Path,
        config: Any,
        storage: IndexStorage,
        *,
        provider: Any | None = None,
        profile: str = "default",
        root_id: str = "default",
        parser_backend: str | None = None,
        parser: MarkdownParser | None = None,
        chunker: StructuralChunker | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config
        self.storage = storage
        self.provider = provider
        self.profile = profile
        self.root_id = root_id
        self.parser_backend = parser_backend or self.config.parsing.backend
        if self.parser_backend not in {"markdown_it", "legacy"}:
            raise ValueError("parser_backend must be 'markdown_it' or 'legacy'")
        self.parser = parser or (MarkdownItParser() if self.parser_backend == "markdown_it" else None)
        self.chunker = chunker or StructuralChunker(
            StructuralChunkingConfig(
                min_chars=self.config.chunking.min_chunk_chars,
                target_chars=self.config.chunking.target_chunk_chars,
                hard_limit_chars=self.config.chunking.hard_limit_chars,
                max_tokens=self.config.chunking.max_chunk_tokens,
                overlap_chars=self.config.chunking.overlap_chars,
                code_window_lines=self.config.chunking.code_window_lines,
                table_rows_per_chunk=self.config.chunking.table_rows_per_chunk,
                mermaid_window_lines=self.config.chunking.mermaid_window_lines,
            )
        )


    def scan(self, *, force_reindex: bool = False) -> IndexingResult:
        parser_name, parser_version, chunk_strategy_name, chunk_strategy_version = self._strategy_identity()
        run_id = self.storage.start_run(
            parser_name=parser_name,
            parser_version=parser_version,
            chunk_strategy_name=chunk_strategy_name,
            chunk_strategy_version=chunk_strategy_version,
        )
        stats = {
            "files_seen": 0,
            "files_changed": 0,
            "files_indexed": 0,
            "files_failed": 0,
            "files_deleted": 0,
            "chunks_created": 0,
            "errors_count": 0,
        }
        error_codes: list[str] = []
        logger.info("index.run.started", extra={"run_id": run_id})

        try:
            scanned = scan_markdown_files(
                self.root,
                self.config.scan.include,
                self.config.scan.exclude,
            )
        except CorpusScanError as exc:
            stats["errors_count"] = 1
            error_codes.append(exc.code)
            self.storage.finish_run(run_id, status="failed", stats=stats, error_codes=error_codes)
            logger.error(
                "index.run.failed",
                extra={"run_id": run_id, "status": "failed", "reason": exc.code},
            )
            return IndexingResult(
                run_id=run_id,
                status="failed",
                error_codes=tuple(error_codes),
                **stats,
            )
        stats["files_seen"] = len(scanned)
        change_plan = self.storage.plan_changes(scanned, self.root)
        files_to_process = scanned if force_reindex else change_plan.new_files + change_plan.changed_files
        deleted_files = list(change_plan.deleted_files)
        rename_sources: dict[str, dict[str, Any]] = {}
        find_rename_source = getattr(self.storage, "find_rename_source", None)
        if not force_reindex and callable(find_rename_source):
            for relative_path in change_plan.new_files:
                try:
                    source_hash = hashlib.sha256(
                        (self.root / relative_path).read_text(encoding="utf-8").encode("utf-8")
                    ).hexdigest()
                except (OSError, UnicodeError):
                    continue
                source = find_rename_source(deleted_files, source_hash)
                if isinstance(source, dict):
                    rename_sources[relative_path.as_posix()] = source
                    deleted_files.remove(str(source["relative_path"]))
        stats["files_changed"] = len(files_to_process)

        for relative_path in files_to_process:
            file_ref = safe_file_ref(self.root_id, relative_path.as_posix())
            logger.info("index.file.started", extra={"run_id": run_id, "file_ref": file_ref})
            try:
                prepared = self._prepare_file(
                    relative_path,
                    run_id,
                    identity_source=rename_sources.get(relative_path.as_posix()),
                )
                self.storage.replace_file(prepared)
                stats["files_indexed"] += 1
                stats["chunks_created"] += len(prepared.chunks)
                logger.info(
                    "index.file.finished",
                    extra={
                        "run_id": run_id,
                        "file_ref": file_ref,
                        "chunk_count": len(prepared.chunks),
                        "status": "success",
                    },
                )
            except Exception as exc:
                code = self._error_code(exc, operation="index")
                stats["files_failed"] += 1
                stats["errors_count"] += 1
                error_codes.append(code)
                logger.error(
                    "index.file.failed",
                    extra={
                        "run_id": run_id,
                        "file_ref": file_ref,
                        "status": "failed",
                        "reason": code,
                    },
                )
                self.storage.record_error(run_id, code, file_ref=file_ref)

        for relative_path in deleted_files:
            file_ref = safe_file_ref(self.root_id, relative_path)
            try:
                self.storage.delete_file(relative_path)
                stats["files_deleted"] += 1
                logger.info(
                    "index.file.deleted",
                    extra={"run_id": run_id, "file_ref": file_ref, "status": "success"},
                )
            except Exception as exc:
                code = self._error_code(exc, operation="delete")
                stats["files_failed"] += 1
                stats["errors_count"] += 1
                error_codes.append(code)
                self.storage.record_error(run_id, code, file_ref=file_ref)
                logger.error(
                    "index.file.delete_failed",
                    extra={"run_id": run_id, "file_ref": file_ref, "reason": code},
                )

        status = self._status(stats)
        self.storage.finish_run(run_id, status=status, stats=stats, error_codes=error_codes)
        logger.info(
            "index.run.finished",
            extra={
                "run_id": run_id,
                "status": status,
                "file_count": stats["files_seen"],
                "files_indexed": stats["files_indexed"],
                "files_failed": stats["files_failed"],
                "chunk_count": stats["chunks_created"],
            },
        )
        return IndexingResult(run_id=run_id, status=status, error_codes=tuple(error_codes), **stats)

    def close(self) -> None:
        self.storage.close()

    def _prepare_file(
        self,
        relative_path: Path,
        run_id: str,
        *,
        identity_source: dict[str, Any] | None = None,
    ) -> PreparedFile:
        if self.parser_backend == "legacy":
            return self._prepare_legacy_file(
                relative_path,
                run_id,
                identity_source=identity_source,
            )
        return self._prepare_structural_file(
            relative_path,
            run_id,
            identity_source=identity_source,
        )

    def _prepare_legacy_file(
        self,
        relative_path: Path,
        run_id: str,
        *,
        identity_source: dict[str, Any] | None = None,
    ) -> PreparedFile:
        relative = relative_path.as_posix()
        existing = self.storage.get_file_by_path(relative) or identity_source
        file_record_id = str(existing["id"]) if existing is not None else str(uuid.uuid4())
        parsed = parse_markdown(self.root / relative_path)
        sections = build_sections(parsed.blocks, file_id=file_record_id)
        chunks = build_chunks(
            parsed.blocks,
            sections,
            file_id=file_record_id,
            config={
                "min_chunk_chars": self.config.chunking.min_chunk_chars,
                "target_chunk_chars": self.config.chunking.target_chunk_chars,
                "hard_limit_chars": self.config.chunking.hard_limit_chars,
                "overlap_chars": self.config.chunking.overlap_chars,
            },
        )

        document_logical_id = (
            str(existing["logical_id"])
            if existing is not None and existing.get("logical_id")
            else logical_id("doc", self.root_id, relative)
        )
        section_ordinals: dict[tuple[object, ...], int] = {}
        stored_section_rows: list[StoredSection] = []
        for section in sections:
            section_key = (
                normalize_heading_path(section.heading_path),
                content_fingerprint(section.title),
            )
            section_ordinal = section_ordinals.get(section_key, 0)
            section_ordinals[section_key] = section_ordinal + 1
            stored_section_rows.append(
                StoredSection(
                    record_id=section.id,
                    logical_id=logical_id(
                        "section",
                        document_logical_id,
                        *section_key,
                        section_ordinal,
                        LEGACY_PARSER_VERSION,
                    ),
                    title=section.title,
                    heading_path=tuple(section.heading_path),
                    level=section.level,
                    start_line=section.start_line,
                    end_line=section.end_line,
                    parent_record_id=section.parent_id,
                )
            )
        stored_sections = tuple(stored_section_rows)
        sections_by_id = {section.record_id: section for section in stored_sections}

        embedding_texts: list[str] = []
        stored_chunks: list[StoredChunk] = []
        duplicate_ordinals: dict[tuple[str, str], int] = {}
        for chunk in chunks:
            section = sections_by_id[chunk.section_id]
            fingerprint = content_fingerprint(chunk.content)
            duplicate_key = (section.logical_id, fingerprint)
            duplicate_ordinal = duplicate_ordinals.get(duplicate_key, 0)
            duplicate_ordinals[duplicate_key] = duplicate_ordinal + 1
            start_line, end_line = self._chunk_source_span(
                parsed.blocks,
                section.start_line,
                section.end_line,
                chunk.content,
                duplicate_ordinal,
            )
            block_id = logical_id(
                "block",
                document_logical_id,
                normalize_heading_path(chunk.heading_path),
                fingerprint,
                LEGACY_PARSER_VERSION,
                duplicate_ordinal,
            )
            chunk_logical_id = logical_id(
                "chunk",
                document_logical_id,
                normalize_heading_path(chunk.heading_path),
                fingerprint,
                LEGACY_CHUNK_STRATEGY_VERSION,
                duplicate_ordinal,
            )
            joined_path = " > ".join(chunk.heading_path)
            embedding_text = build_embedding_text(chunk, parsed.title, relative, joined_path)
            stored_chunks.append(
                StoredChunk(
                    record_id=chunk.id,
                    logical_id=chunk_logical_id,
                    section_record_id=chunk.section_id,
                    content=chunk.content,
                    content_type=chunk.content_type.value,
                    chunk_index=chunk.chunk_index,
                    heading_path=tuple(chunk.heading_path),
                    previous_record_id=chunk.previous_chunk_id,
                    next_record_id=chunk.next_chunk_id,
                    embedding_text=embedding_text,
                    embedding_text_hash=content_fingerprint(embedding_text),
                    start_line=start_line,
                    end_line=end_line,
                    block_logical_id=block_id,
                    block_kind=chunk.content_type.value,
                    chunk_kind=chunk.content_type.value,
                )
            )
            embedding_texts.append(embedding_text)

        vectors: tuple[tuple[float, ...], ...] = ()
        if embedding_texts and self.provider is not None:
            embedded = asyncio.run(self.provider.embed(embedding_texts, profile=self.profile))
            if len(embedded) != len(stored_chunks):
                raise RuntimeError("embedding count mismatch")
            vectors = tuple(tuple(float(value) for value in vector) for vector in embedded)

        return PreparedFile(
            record_id=file_record_id,
            logical_id=document_logical_id,
            root_id=self.root_id,
            relative_path=relative,
            title=parsed.title,
            source_hash=parsed.source_hash,
            indexed_at=datetime.now(timezone.utc).isoformat(),
            parser_name=LEGACY_PARSER_NAME,
            parser_version=LEGACY_PARSER_VERSION,
            chunk_strategy_name=LEGACY_CHUNK_STRATEGY_NAME,
            chunk_strategy_version=LEGACY_CHUNK_STRATEGY_VERSION,
            index_run_id=run_id,
            sections=stored_sections,
            chunks=tuple(stored_chunks),
            vectors=vectors,
            embedding_profile=self._embedding_profile() if vectors else None,
            embedding_model=self._provider_attr("model_name", "_model_name", default="default") if vectors else None,
            embedding_dimensions=int(self._provider_attr("dimensions", default=0)) if vectors else None,
            embedding_endpoint=self._provider_attr("endpoint", "_endpoint", default=None) if vectors else None,
        )

    def _prepare_structural_file(
        self,
        relative_path: Path,
        run_id: str,
        *,
        identity_source: dict[str, Any] | None = None,
    ) -> PreparedFile:
        relative = relative_path.as_posix()
        existing = self.storage.get_file_by_path(relative) or identity_source
        file_record_id = str(existing["id"]) if existing is not None else str(uuid.uuid4())
        document_logical_id = (
            str(existing["logical_id"])
            if existing is not None and existing.get("logical_id")
            else logical_id("doc", self.root_id, relative)
        )
        if self.parser is None:
            raise RuntimeError("markdown-it parser is not configured")

        parsed = self.parser.parse(
            self.root / relative_path,
            document_id=document_logical_id,
            relative_path=relative,
        )
        parsed = self._stable_document_identities(parsed)
        file_ref = safe_file_ref(self.root_id, relative)
        logger.info(
            "markdown.parse.finished",
            extra={
                "run_id": run_id,
                "file_ref": file_ref,
                "block_count": len(parsed.blocks),
                "parser": parsed.parser_name,
                "status": "success",
            },
        )
        chunks = self._stable_chunk_identities(parsed, self.chunker.build(parsed))
        logger.info(
            "chunk.build.finished",
            extra={
                "run_id": run_id,
                "file_ref": file_ref,
                "block_count": len(parsed.blocks),
                "chunk_count": len(chunks),
                "chunk_strategy": self.chunker.name,
                "status": "success",
            },
        )

        heading_blocks = [block for block in parsed.blocks if block.block_type == BlockType.HEADING]
        section_keys: list[str] = [block.block_id for block in heading_blocks]
        chunk_section_keys: list[str] = []
        section_blocks: dict[str, Any] = {block.block_id: block for block in heading_blocks}
        for chunk in chunks:
            candidates = [
                block
                for block in heading_blocks
                if block.heading_path == chunk.heading_path
                and block.source_span.start_line <= chunk.source_span.start_line
            ]
            heading = max(candidates, key=lambda block: block.source_span.start_line) if candidates else None
            key = heading.block_id if heading is not None else "root"
            chunk_section_keys.append(key)
            if key not in section_keys:
                section_keys.append(key)
                if heading is not None:
                    section_blocks[key] = heading
        section_record_ids = {key: str(uuid.uuid4()) for key in section_keys}
        stored_sections: list[StoredSection] = []
        for key in section_keys:
            related = [chunk for index, chunk in enumerate(chunks) if chunk_section_keys[index] == key]
            heading = section_blocks.get(key)
            path = heading.heading_path if heading is not None else ()
            if not related and heading is not None:
                following_boundaries = [
                    block.source_span.start_line
                    for block in heading_blocks
                    if block.source_span.start_line > heading.source_span.start_line
                    and len(block.heading_path) <= len(path)
                ]
                boundary = min(following_boundaries, default=None)
                related = [
                    chunk
                    for chunk in chunks
                    if chunk.source_span.start_line >= heading.source_span.start_line
                    and (boundary is None or chunk.source_span.start_line < boundary)
                    and chunk.heading_path[: len(path)] == path
                ]
            start_line = min(
                [chunk.source_span.start_line for chunk in related]
                + ([heading.source_span.start_line] if heading is not None else [])
            )
            end_line = max(
                [chunk.source_span.end_line for chunk in related]
                + ([heading.source_span.end_line] if heading is not None else [])
            )
            title = (heading.plain_text or "") if heading is not None else (parsed.title or Path(relative).stem)
            parent_key = None
            if heading is not None and len(path) > 1:
                parents = [
                    block
                    for block in heading_blocks
                    if block.heading_path == path[:-1]
                    and block.source_span.start_line < heading.source_span.start_line
                ]
                if parents:
                    parent_key = max(parents, key=lambda block: block.source_span.start_line).block_id
            stored_sections.append(
                StoredSection(
                    record_id=section_record_ids[key],
                    logical_id=logical_id("section", document_logical_id, key),
                    title=title,
                    heading_path=path,
                    level=min(6, max(1, len(path))),
                    start_line=start_line,
                    end_line=end_line,
                    parent_record_id=section_record_ids.get(parent_key) if parent_key is not None else None,
                )
            )

        chunk_record_ids = [str(uuid.uuid4()) for _ in chunks]
        stored_chunks = tuple(
            StoredChunk(
                record_id=chunk_record_ids[index],
                logical_id=chunk.chunk_id,
                section_record_id=section_record_ids[chunk_section_keys[index]],
                content=chunk.display_content,
                content_type=chunk.content_type.value,
                chunk_index=chunk.chunk_index,
                heading_path=chunk.heading_path,
                previous_record_id=chunk_record_ids[index - 1] if index > 0 else None,
                next_record_id=chunk_record_ids[index + 1] if index + 1 < len(chunks) else None,
                embedding_text=chunk.embedding_text,
                embedding_text_hash=content_fingerprint(chunk.embedding_text),
                start_line=chunk.source_span.start_line,
                end_line=chunk.source_span.end_line,
                block_logical_id=chunk.parent_block_ids[0],
                start_offset=chunk.source_span.start_offset,
                end_offset=chunk.source_span.end_offset,
                block_kind=next(
                    block.block_type.value
                    for block in parsed.blocks
                    if block.block_id == chunk.parent_block_ids[0]
                ),
                chunk_kind=chunk.content_type.value,
            )
            for index, chunk in enumerate(chunks)
        )
        vectors: tuple[tuple[float, ...], ...] = ()
        if stored_chunks and self.provider is not None:
            embedded = asyncio.run(
                self.provider.embed(
                    [chunk.embedding_text for chunk in stored_chunks],
                    profile=self.profile,
                )
            )
            if len(embedded) != len(stored_chunks):
                raise RuntimeError("embedding count mismatch")
            vectors = tuple(tuple(float(value) for value in vector) for vector in embedded)

        return PreparedFile(
            record_id=file_record_id,
            logical_id=document_logical_id,
            root_id=self.root_id,
            relative_path=relative,
            title=parsed.title,
            source_hash=parsed.source_hash,
            indexed_at=datetime.now(timezone.utc).isoformat(),
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            chunk_strategy_name=self.chunker.name,
            chunk_strategy_version=self.chunker.version,
            index_run_id=run_id,
            sections=tuple(stored_sections),
            chunks=stored_chunks,
            vectors=vectors,
            embedding_profile=self._embedding_profile() if vectors else None,
            embedding_model=self._provider_attr("model_name", "_model_name", default="default") if vectors else None,
            embedding_dimensions=int(self._provider_attr("dimensions", default=0)) if vectors else None,
            embedding_endpoint=self._provider_attr("endpoint", "_endpoint", default=None) if vectors else None,
        )

    @staticmethod
    def _stable_document_identities(document: Document) -> Document:
        ordinals: dict[tuple[object, ...], int] = {}
        blocks = []
        for block in document.blocks:
            key = (
                block.block_type.value,
                normalize_heading_path(block.heading_path),
                content_fingerprint(block.raw_markdown),
            )
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            blocks.append(
                replace(
                    block,
                    block_id=logical_id(
                        "block",
                        document.document_id,
                        *key,
                        ordinal,
                        document.parser_version,
                    ),
                )
            )
        return replace(document, blocks=tuple(blocks))

    def _stable_chunk_identities(
        self,
        document: Document,
        chunks: tuple[RetrievalChunk, ...],
    ) -> tuple[RetrievalChunk, ...]:
        ordinals: dict[tuple[object, ...], int] = {}
        stable = []
        for chunk in chunks:
            key = (
                chunk.parent_block_ids,
                chunk.content_type.value,
                content_fingerprint(chunk.display_content),
            )
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            stable.append(
                replace(
                    chunk,
                    chunk_id=logical_id(
                        "chunk",
                        document.document_id,
                        *key,
                        ordinal,
                        self.chunker.version,
                    ),
                )
            )
        return tuple(stable)

    @staticmethod
    def _section_paths(paths: Iterable[tuple[str, ...]]) -> list[tuple[str, ...]]:
        ordered: list[tuple[str, ...]] = []
        seen: set[tuple[str, ...]] = set()
        for path in paths:
            prefixes = [path[:depth] for depth in range(1, len(path) + 1)] if path else [()]
            for prefix in prefixes:
                if prefix not in seen:
                    ordered.append(prefix)
                    seen.add(prefix)
        return ordered

    def _strategy_identity(self) -> tuple[str, str, str, str]:
        if self.parser_backend == "legacy":
            return (
                LEGACY_PARSER_NAME,
                LEGACY_PARSER_VERSION,
                LEGACY_CHUNK_STRATEGY_NAME,
                LEGACY_CHUNK_STRATEGY_VERSION,
            )
        if self.parser is None:
            raise RuntimeError("markdown-it parser is not configured")
        return self.parser.name, self.parser.version, self.chunker.name, self.chunker.version

    @staticmethod
    def _chunk_source_span(
        blocks: list[Any],
        section_start: int,
        section_end: int,
        chunk_content: str,
        duplicate_ordinal: int,
    ) -> tuple[int, int]:
        """Recover the narrowest source-block span carried by a legacy chunk."""
        content = chunk_content.strip()
        eligible = [
            block
            for block in blocks
            if section_start <= block.start_line <= block.end_line <= section_end
            and block.content.strip()
        ]
        matches = [
            block
            for block in eligible
            if block.content.strip() in content or content in block.content.strip()
        ]
        if not matches:
            return section_start, section_end
        normalized = {block.content.strip() for block in matches}
        if len(matches) > 1 and len(normalized) == 1:
            selected = matches[duplicate_ordinal % len(matches)]
            return selected.start_line, selected.end_line
        return min(block.start_line for block in matches), max(block.end_line for block in matches)

    def _provider_attr(self, *names: str, default: Any) -> Any:
        for name in names:
            value = getattr(self.provider, name, None)
            if value is not None:
                return value
        return default

    def _embedding_profile(self) -> EmbeddingProfile:
        return embedding_profile_from_config(self.config, self.provider, self.profile)

    @staticmethod
    def _status(stats: dict[str, int]):
        if stats["errors_count"] == 0:
            return "success"
        if stats["files_indexed"] > 0 or stats["files_deleted"] > 0:
            return "partial_success"
        return "failed"

    @staticmethod
    def _error_code(exc: Exception, *, operation: str) -> str:
        if isinstance(exc, UnicodeError):
            return "FILE_DECODE_ERROR"
        if isinstance(exc, OSError):
            return "FILE_IO_ERROR"
        return "FILE_DELETE_ERROR" if operation == "delete" else "FILE_INDEX_ERROR"
