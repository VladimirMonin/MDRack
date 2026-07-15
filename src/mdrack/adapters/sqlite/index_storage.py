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
from mdrack.indexing.change_detector import detect_changes
from mdrack.search.text import TextSearchResult, text_search
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir

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

            self._write_assets(prepared)

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
                   COALESCE(c.logical_id, c.id) AS logical_id
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
            heading_path=tuple(json.loads(row["heading_path"] or "[]")),
            block_id=row["block_logical_id"],
            chunk_id=row["logical_id"],
        )

    def list_assets_for_file(self, relative_path: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT a.asset_id, a.root_id, a.relative_path, a.content_hash,
                            a.mime_type, a.size_bytes, a.width, a.height, a.exists_on_disk
            FROM assets a
            JOIN asset_references ar ON ar.asset_id = a.asset_id
            JOIN files f ON f.id = ar.file_id
            WHERE f.relative_path = ?
            ORDER BY a.asset_id
            """,
            (relative_path,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_asset_references(self, relative_path: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT ar.*
            FROM asset_references ar
            JOIN files f ON f.id = ar.file_id
            WHERE f.relative_path = ?
            ORDER BY ar.start_line, ar.start_offset, ar.reference_id
            """,
            (relative_path,),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_text(self, query: str, *, limit: int, offset: int = 0) -> TextSearchResult:
        return text_search(self.connection, query, limit=limit, offset=offset)

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def _delete_derived_rows(self, file_id: str) -> None:
        self.connection.execute("DELETE FROM asset_references WHERE file_id = ?", (file_id,))
        self.connection.execute(
            "DELETE FROM assets WHERE NOT EXISTS "
            "(SELECT 1 FROM asset_references ar WHERE ar.asset_id = assets.asset_id)"
        )
        chunk_rows = self.connection.execute(
            "SELECT id FROM chunks WHERE file_id = ?",
            (file_id,),
        ).fetchall()
        for row in chunk_rows:
            self.connection.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["id"],))
        self.connection.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        self.connection.execute("DELETE FROM sections WHERE file_id = ?", (file_id,))

    def _write_assets(self, prepared: PreparedFile) -> None:
        for asset in prepared.assets:
            self.connection.execute(
                """
                INSERT INTO assets (
                    asset_id, root_id, relative_path, content_hash, mime_type,
                    size_bytes, width, height, exists_on_disk
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    mime_type = excluded.mime_type,
                    size_bytes = excluded.size_bytes,
                    width = excluded.width,
                    height = excluded.height,
                    exists_on_disk = excluded.exists_on_disk
                """,
                (
                    asset.asset_id,
                    asset.root_id,
                    asset.relative_path,
                    asset.content_hash,
                    asset.mime_type,
                    asset.size_bytes,
                    asset.width,
                    asset.height,
                    int(asset.exists),
                ),
            )
        for reference in prepared.asset_references:
            self.connection.execute(
                """
                INSERT INTO asset_references (
                    reference_id, asset_id, file_id, document_logical_id,
                    document_relative_path, block_logical_id, chunk_logical_id,
                    raw_reference, syntax, start_line, end_line, start_offset,
                    end_offset, alt_text, surrounding_text, resolution_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reference.reference_id,
                    reference.asset_id,
                    prepared.record_id,
                    reference.document_id,
                    reference.document_relative_path,
                    reference.block_id,
                    reference.chunk_id,
                    reference.raw_reference,
                    reference.syntax,
                    reference.source_span.start_line,
                    reference.source_span.end_line,
                    reference.source_span.start_offset,
                    reference.source_span.end_offset,
                    reference.alt_text,
                    reference.surrounding_text,
                    reference.resolution_status,
                ),
            )

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
                block_logical_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
