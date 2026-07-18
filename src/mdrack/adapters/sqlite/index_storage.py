"""SQLite implementation of MDRack storage ports."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mdrack.domain.indexing import PreparedFile, SourceLocator, StoredChunk
from mdrack.domain.profiles import EmbeddingProfile, IncompatibleEmbeddingProfileError
from mdrack.domain.retrieval import RetrievalCandidate
from mdrack.indexing.change_detector import detect_changes
from mdrack.search.text import TextSearchResult, text_search
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack.storage.sqlite.vector import VectorIndex

logger = logging.getLogger(__name__)


def create_sqlite_index_storage(root: Path, config: Any) -> SQLiteIndexStorage:
    """Compose the default SQLite adapter outside the application layer."""
    resolved_root = root.resolve()
    store_path = Path(config.paths.store)
    store_dir = store_path if store_path.is_absolute() else resolved_root / store_path
    store_dir.mkdir(parents=True, exist_ok=True)
    connection = get_connection(store_dir / "knowledge.db")
    apply_migrations(connection, get_migrations_dir())
    return SQLiteIndexStorage(connection, owns_connection=True)


class SQLiteIndexStorage:
    """Default SQLite adapter with one atomic transaction per file."""

    def __init__(self, connection: sqlite3.Connection, *, owns_connection: bool = False) -> None:
        self.connection = connection
        self._owns_connection = owns_connection

    def start_run(
        self,
        *,
        parser_name: str,
        parser_version: str,
        chunk_strategy_name: str,
        chunk_strategy_version: str,
    ) -> str:
        run_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO index_runs (
                id, started_at, status, parser_name, parser_version,
                chunk_strategy_name, chunk_strategy_version
            ) VALUES (?, ?, 'running', ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now(timezone.utc).isoformat(),
                parser_name,
                parser_version,
                chunk_strategy_name,
                chunk_strategy_version,
            ),
        )
        self.connection.commit()
        return run_id

    def plan_changes(self, scanned: list[Path], root: Path):
        return detect_changes(self.connection, scanned, root)

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM files WHERE relative_path = ? AND status = 'active'",
            (relative_path,),
        ).fetchone()
        return dict(row) if row is not None else None

    def find_rename_source(
        self,
        deleted_paths: Sequence[str],
        source_hash: str,
    ) -> dict[str, Any] | None:
        """Return one unambiguous deleted-path identity with matching bytes."""
        if not deleted_paths:
            return None
        placeholders = ",".join("?" for _ in deleted_paths)
        rows = self.connection.execute(
            f"SELECT * FROM files WHERE relative_path IN ({placeholders}) "
            "AND source_hash = ? AND status = 'active'",
            (*deleted_paths, source_hash),
        ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    def get_public_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        row = self.get_file_by_path(relative_path)
        if row is None:
            return None
        logical_id = row["logical_id"] or row["id"]
        return {
            "id": logical_id,
            "logical_id": logical_id,
            "root_id": row["root_id"],
            "relative_path": row["relative_path"],
            "title": row["title"],
            "source_hash": row["source_hash"],
            "indexed_at": row["indexed_at"],
            "status": row["status"],
            "parser_name": row["parser_name"],
            "parser_version": row["parser_version"],
            "chunk_strategy_name": row["chunk_strategy_name"],
            "chunk_strategy_version": row["chunk_strategy_version"],
        }

    def get_chunk_by_logical_id(self, logical_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM chunks WHERE logical_id = ? OR (logical_id IS NULL AND id = ?)",
            (logical_id, logical_id),
        ).fetchone()
        if row is None:
            return None
        locator = self.get_chunk_source_locator(logical_id)
        return {
            "id": locator.chunk_id,
            "logical_id": locator.chunk_id,
            "content": row["content"],
            "content_type": row["content_type"],
            "chunk_index": row["chunk_index"],
            "heading_path": list(locator.heading_path),
            "embedding_text_hash": row["embedding_text_hash"],
            "source_locator": locator.to_dict(),
        }

    def replace_file(self, prepared: PreparedFile) -> None:
        """Replace one file and all derived rows atomically."""
        savepoint = "replace_file"
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            self._delete_derived_rows(prepared.record_id)
            self._write_file(prepared)
            for section in prepared.sections:
                self.connection.execute(
                    """
                    INSERT INTO sections (
                        id, logical_id, file_id, title, heading_path, level,
                        start_line, end_line, parent_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        section.record_id,
                        section.logical_id,
                        prepared.record_id,
                        section.title,
                        json.dumps(section.heading_path, ensure_ascii=False),
                        section.level,
                        section.start_line,
                        section.end_line,
                        section.parent_record_id,
                    ),
                )

            self._ensure_embedding_profile(prepared)
            for index, chunk in enumerate(prepared.chunks):
                self._write_chunk(prepared, chunk)
                if prepared.vectors:
                    if prepared.embedding_profile is None:
                        raise ValueError("embedding profile is required when vectors are present")
                    self._write_vector(chunk.record_id, prepared.embedding_profile, prepared.vectors[index])

            self._validate_file_counts(prepared)
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            self.connection.commit()
        except Exception:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            self.connection.commit()
            raise

    def delete_file(self, relative_path: str) -> None:
        row = self.connection.execute(
            "SELECT id FROM files WHERE relative_path = ? AND status = 'active'",
            (relative_path,),
        ).fetchone()
        if row is None:
            return
        with self.connection:
            self._delete_derived_rows(row["id"])
            self.connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))

    def record_error(self, run_id: str, code: str, *, file_ref: str) -> None:
        self.connection.execute(
            """
            INSERT INTO diagnostics (id, run_id, severity, code, message, details, created_at)
            VALUES (?, ?, 'error', ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                code,
                "File indexing operation failed",
                json.dumps({"file_ref": file_ref}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stats: dict[str, int],
        error_codes: Sequence[str],
    ) -> None:
        self.connection.execute(
            """
            UPDATE index_runs
            SET finished_at = ?, status = ?, files_seen = ?, files_changed = ?,
                files_indexed = ?, files_failed = ?, files_deleted = ?,
                chunks_created = ?, errors_count = ?, error_message = ?
            WHERE id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                status,
                stats["files_seen"],
                stats["files_changed"],
                stats["files_indexed"],
                stats["files_failed"],
                stats["files_deleted"],
                stats["chunks_created"],
                stats["errors_count"],
                json.dumps(sorted(set(error_codes))) if error_codes else None,
                run_id,
            ),
        )
        self.connection.commit()

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        row = self.connection.execute(
            """
            SELECT f.root_id, f.relative_path,
                   COALESCE(c.start_line, s.start_line, 1) AS start_line,
                   COALESCE(c.end_line, s.end_line, c.start_line, s.start_line, 1) AS end_line,
                   c.heading_path,
                   COALESCE(c.block_logical_id, c.logical_id, c.id) AS block_logical_id,
                   COALESCE(c.logical_id, c.id) AS logical_id,
                   c.start_offset, c.end_offset,
                   COALESCE(c.block_kind, 'unknown') AS block_kind,
                   COALESCE(c.chunk_kind, c.content_type, 'unknown') AS chunk_kind
            FROM chunks c
            JOIN files f ON f.id = c.file_id
            LEFT JOIN sections s ON s.id = c.section_id
            WHERE c.id = ? OR c.logical_id = ?
            """,
            (chunk_id, chunk_id),
        ).fetchone()
        if row is None:
            raise KeyError(chunk_id)
        return SourceLocator(
            root_id=row["root_id"],
            relative_path=row["relative_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            heading_path=self._decode_heading_path(row["heading_path"]),
            block_id=row["block_logical_id"],
            chunk_id=row["logical_id"],
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            block_kind=row["block_kind"],
            chunk_kind=row["chunk_kind"],
        )

    def search_text(self, query: str, *, limit: int, offset: int = 0) -> TextSearchResult:
        return text_search(self.connection, query, limit=limit, offset=offset)

    def retrieve_text_candidates(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[RetrievalCandidate]:
        result = text_search(self.connection, query, limit=limit, offset=offset)
        candidates: list[RetrievalCandidate] = []
        for item in result.results:
            if item.source_locator is None:
                continue
            candidates.append(
                RetrievalCandidate(
                    logical_id=item.source_locator.chunk_id,
                    score=item.score,
                    content_preview=item.snippet,
                    source_locator=item.source_locator,
                    metadata={
                        "section_title": item.section_title,
                        "heading_path": item.heading_path,
                    },
                )
            )
        return candidates

    def retrieve_semantic_candidates(
        self,
        query_vector: list[float],
        *,
        profile: str,
        profile_fingerprint: str | None,
        limit: int,
    ) -> list[RetrievalCandidate]:
        scored = VectorIndex(self.connection).search(
            query_vector,
            profile_name=profile,
            profile_fingerprint=profile_fingerprint,
            limit=limit,
        )
        if not scored:
            return []
        record_ids = [str(item["chunk_id"]) for item in scored]
        placeholders = ",".join("?" for _ in record_ids)
        rows = self.connection.execute(
            f"""
            SELECT c.id, c.logical_id, c.block_logical_id, c.content,
                   c.start_line, c.end_line, c.start_offset, c.end_offset,
                   c.block_kind, c.chunk_kind,
                   COALESCE(c.heading_path, s.heading_path) AS heading_path,
                   f.root_id, f.relative_path, s.title AS section_title
            FROM chunks c
            JOIN files f ON f.id = c.file_id
            LEFT JOIN sections s ON s.id = c.section_id
            WHERE c.id IN ({placeholders})
            """,
            record_ids,
        ).fetchall()
        by_record_id = {row["id"]: row for row in rows}
        candidates: list[RetrievalCandidate] = []
        for scored_item in scored:
            record_id = str(scored_item["chunk_id"])
            row = by_record_id.get(record_id)
            if row is None:
                logger.warning("retrieval.semantic.candidate_skipped reason=missing_chunk")
                continue
            logical_id = row["logical_id"] or record_id
            heading_path = self._decode_heading_path(row["heading_path"])
            locator = SourceLocator(
                root_id=row["root_id"] or "default",
                relative_path=row["relative_path"],
                start_line=row["start_line"] or 1,
                end_line=row["end_line"] or row["start_line"] or 1,
                heading_path=heading_path,
                block_id=row["block_logical_id"] or logical_id,
                chunk_id=logical_id,
                start_offset=row["start_offset"],
                end_offset=row["end_offset"],
                block_kind=row["block_kind"] or "unknown",
                chunk_kind=row["chunk_kind"] or "unknown",
            )
            content = row["content"] or ""
            raw_score = scored_item["score"]
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise TypeError("semantic candidate score must be numeric")
            candidates.append(
                RetrievalCandidate(
                    logical_id=logical_id,
                    score=float(raw_score),
                    content_preview=content[:200] + ("..." if len(content) > 200 else ""),
                    source_locator=locator,
                    metadata={
                        "section_title": row["section_title"],
                        "heading_path": row["heading_path"],
                    },
                )
            )
        return candidates

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    @staticmethod
    def _decode_heading_path(value: str | None) -> tuple[str, ...]:
        if not value:
            return ()
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return tuple(part.strip() for part in value.split(">") if part.strip())
        if isinstance(decoded, list):
            return tuple(str(part) for part in decoded)
        return (str(decoded),)

    def _delete_derived_rows(self, file_id: str) -> None:
        chunk_rows = self.connection.execute(
            "SELECT id FROM chunks WHERE file_id = ?",
            (file_id,),
        ).fetchall()
        for row in chunk_rows:
            self.connection.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["id"],))
        self.connection.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        self.connection.execute("DELETE FROM sections WHERE file_id = ?", (file_id,))

    def _write_file(self, prepared: PreparedFile) -> None:
        self.connection.execute(
            """
            INSERT INTO files (
                id, logical_id, root_id, relative_path, title, source_hash,
                indexed_at, status, parser_name, parser_version,
                chunk_strategy_name, chunk_strategy_version, index_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                logical_id = excluded.logical_id,
                root_id = excluded.root_id,
                relative_path = excluded.relative_path,
                title = excluded.title,
                source_hash = excluded.source_hash,
                indexed_at = excluded.indexed_at,
                status = 'active',
                parser_name = excluded.parser_name,
                parser_version = excluded.parser_version,
                chunk_strategy_name = excluded.chunk_strategy_name,
                chunk_strategy_version = excluded.chunk_strategy_version,
                index_run_id = excluded.index_run_id
            """,
            (
                prepared.record_id,
                prepared.logical_id,
                prepared.root_id,
                prepared.relative_path,
                prepared.title,
                prepared.source_hash,
                prepared.indexed_at,
                prepared.parser_name,
                prepared.parser_version,
                prepared.chunk_strategy_name,
                prepared.chunk_strategy_version,
                prepared.index_run_id,
            ),
        )

    def _write_chunk(self, prepared: PreparedFile, chunk: StoredChunk) -> None:
        heading_path = json.dumps(chunk.heading_path, ensure_ascii=False)
        self.connection.execute(
            """
            INSERT INTO chunks (
                id, logical_id, file_id, section_id, content, content_type,
                chunk_index, heading_path, previous_chunk_id, next_chunk_id,
                embedding_text, embedding_text_hash, start_line, end_line,
                block_logical_id, start_offset, end_offset, block_kind, chunk_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.record_id,
                chunk.logical_id,
                prepared.record_id,
                chunk.section_record_id,
                chunk.content,
                chunk.content_type,
                chunk.chunk_index,
                heading_path,
                chunk.previous_record_id,
                chunk.next_record_id,
                chunk.embedding_text,
                chunk.embedding_text_hash,
                chunk.start_line,
                chunk.end_line,
                chunk.block_logical_id,
                chunk.start_offset,
                chunk.end_offset,
                chunk.block_kind,
                chunk.chunk_kind,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path)
            VALUES (?, ?, ?, ?)
            """,
            (chunk.record_id, chunk.content, chunk.content_type, heading_path),
        )

    def _ensure_embedding_profile(self, prepared: PreparedFile) -> None:
        if not prepared.vectors or prepared.embedding_profile is None:
            return
        profile = prepared.embedding_profile
        existing = self.connection.execute(
            "SELECT fingerprint FROM embedding_profiles WHERE name = ?",
            (profile.name,),
        ).fetchone()
        if existing is not None and existing["fingerprint"] != profile.fingerprint:
            raise IncompatibleEmbeddingProfileError(
                "active embedding profile name is bound to an incompatible fingerprint"
            )
        self.connection.execute(
            """
            INSERT INTO embedding_profiles (
                name, model, dimensions, endpoint, fingerprint, provider, runtime,
                model_key, model_family, quantization, query_instruction_hash,
                normalization_mode, endpoint_family
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (
                profile.name,
                prepared.embedding_model or "default",
                profile.output_dimensions,
                prepared.embedding_endpoint,
                profile.fingerprint,
                profile.provider,
                profile.runtime,
                profile.model_key,
                profile.model_family,
                profile.quantization,
                profile.query_instruction_hash,
                profile.normalization_mode,
                profile.endpoint_family,
            ),
        )

    def _write_vector(
        self,
        chunk_id: str,
        profile: EmbeddingProfile,
        vector: tuple[float, ...],
    ) -> None:
        if len(vector) != profile.output_dimensions:
            raise ValueError("embedding vector dimension does not match active profile")
        self.connection.execute(
            """
            INSERT INTO chunk_embeddings (
                chunk_id, profile_name, embedding, embedded_at, profile_fingerprint
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                profile.name,
                json.dumps(vector).encode("utf-8"),
                datetime.now(timezone.utc).isoformat(),
                profile.fingerprint,
            ),
        )

    def _validate_file_counts(self, prepared: PreparedFile) -> None:
        row = self.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM sections WHERE file_id = ?) AS section_count,
                (SELECT COUNT(*) FROM chunks WHERE file_id = ?) AS chunk_count
            """,
            (prepared.record_id, prepared.record_id),
        ).fetchone()
        if row["section_count"] != len(prepared.sections) or row["chunk_count"] != len(prepared.chunks):
            raise RuntimeError("file index validation failed")
