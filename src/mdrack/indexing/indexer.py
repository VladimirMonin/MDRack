"""Indexer pipeline — orchestrates full indexing of Markdown files.

The run_indexer function is the main entry point that coordinates scanning,
change detection, parsing, chunking, embedding, and database storage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mdrack.embeddings.hashing import hash_embedding_text
from mdrack.indexing.change_detector import detect_changes
from mdrack.indexing.scanner import scan_markdown_files
from mdrack.indexing.transactions import (
    complete_index_run,
    insert_file,
    insert_section,
    record_diagnostic,
    start_index_run,
    upsert_chunk,
)
from mdrack.markdown.chunk_builder import build_chunks
from mdrack.markdown.embedding_text import build_embedding_text
from mdrack.markdown.parser import parse_markdown
from mdrack.markdown.section_builder import build_sections
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import upsert_fts
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack.storage.sqlite.vector import VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class IndexerResult:
    """Result of a completed index run."""

    run_id: str
    files_seen: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    chunks_created: int = 0
    errors_count: int = 0


def run_indexer(
    root: Path,
    config,
    provider: object | None = None,
    profile: str = "default",
    force_reindex: bool = False,
) -> IndexerResult:
    """Run the full indexing pipeline.

    Steps:
    1. Resolve the vault root path.
    2. Create/open the SQLite store.
    3. Apply pending schema migrations.
    4. Start a new index run record.
    5. Scan for Markdown files.
    6. Detect changed, new, unchanged, and deleted files.
    7. Parse, section-build, chunk, embed, and store every changed/new file.
    8. Handle deleted files.
    9. Complete the index run with statistics.
    """
    root = root.resolve()
    store_path = Path(config.paths.store)
    store_dir = store_path if store_path.is_absolute() else root / store_path
    store_dir.mkdir(parents=True, exist_ok=True)
    db_path = store_dir / "knowledge.db"

    conn = get_connection(db_path)
    try:
        apply_migrations(conn, get_migrations_dir())
        run_id = start_index_run(conn)

        stats = _build_stats()
        errors = 0

        scanned = scan_markdown_files(root, config.scan.include, config.scan.exclude)
        stats["files_seen"] = len(scanned)

        change_plan = detect_changes(conn, scanned, root)
        if force_reindex:
            files_to_process = scanned
        else:
            files_to_process = change_plan.new_files + change_plan.changed_files
        stats["files_changed"] = len(files_to_process)

        chunking_cfg = {
            "min_chunk_chars": config.chunking.min_chunk_chars,
            "target_chunk_chars": config.chunking.target_chunk_chars,
            "hard_limit_chars": config.chunking.hard_limit_chars,
            "overlap_chars": config.chunking.overlap_chars,
        }

        # ── index new & changed files ──────────────────────────────────
        for rel_path in files_to_process:
            try:
                abs_path = root / rel_path
                rel_str = rel_path.as_posix()

                existing = _find_file_by_path(conn, rel_str)
                old_file_id = existing["id"] if existing else None
                file_uuid = old_file_id or str(uuid.uuid4())

                if existing:
                    _cleanup_file_data(conn, file_uuid)

                parsed = parse_markdown(abs_path)
                sections = build_sections(parsed.blocks, file_id=file_uuid)
                chunks = build_chunks(
                    parsed.blocks, sections, file_id=file_uuid, config=chunking_cfg
                )

                indexed_at = datetime.now(timezone.utc).isoformat()

                if existing:
                    _update_file_record(conn, file_uuid, parsed.source_hash, indexed_at)
                else:
                    insert_file(
                        conn, file_uuid, rel_str, parsed.title,
                        parsed.source_hash, indexed_at,
                    )

                for section in sections:
                    insert_section(
                        conn,
                        section.id,
                        section.title,
                        json.dumps(section.heading_path),
                        section.level,
                        section.start_line,
                        section.end_line,
                        section.parent_id,
                        file_uuid,
                    )

                embedding_texts: list[str] = []
                heading_path_map: dict[str, str] = {}

                for chunk in chunks:
                    joined_path = " > ".join(chunk.heading_path)
                    etext = build_embedding_text(
                        chunk, parsed.title, rel_str, joined_path,
                    )
                    ehash = hash_embedding_text(etext)
                    heading_path_map[chunk.id] = json.dumps(chunk.heading_path)

                    upsert_chunk(
                        conn,
                        chunk.id,
                        file_uuid,
                        chunk.section_id,
                        chunk.content,
                        chunk.content_type.value,
                        chunk.chunk_index,
                        heading_path_map[chunk.id],
                        chunk.previous_chunk_id,
                        chunk.next_chunk_id,
                        etext,
                        ehash,
                    )
                    embedding_texts.append(etext)

                if embedding_texts and provider is not None:
                    _ensure_embedding_profile(conn, profile, provider)
                    vectors = asyncio.run(provider.embed(embedding_texts, profile=profile))
                    vi = VectorIndex(conn)

                    for chunk, vec in zip(chunks, vectors):
                        vi.upsert(chunk.id, profile, vec)

                for chunk in chunks:
                    upsert_fts(
                        conn,
                        chunk.id,
                        chunk.content,
                        chunk.content_type.value,
                        heading_path_map[chunk.id],
                    )

                stats["chunks_created"] += len(chunks)
                logger.info(
                    "Indexed file: %s (%d chunks)", rel_str, len(chunks),
                )

            except Exception:
                errors += 1
                logger.exception("Failed to index file: %s", rel_path)
                record_diagnostic(
                    conn, run_id, "error", "FILE_INDEX_ERROR",
                    f"Failed to index {rel_path.as_posix()}",
                    details={"file": rel_path.as_posix()},
                )

        # ── handle deleted files ───────────────────────────────────────
        for rel_path_str in change_plan.deleted_files:
            try:
                _delete_file_from_db(conn, rel_path_str)
                logger.info("Deleted file: %s", rel_path_str)
            except Exception:
                errors += 1
                logger.exception("Failed to delete file: %s", rel_path_str)
                record_diagnostic(
                    conn, run_id, "error", "FILE_DELETE_ERROR",
                    f"Failed to delete {rel_path_str}",
                    details={"file": rel_path_str},
                )

        stats["files_deleted"] = len(change_plan.deleted_files)
        complete_index_run(conn, run_id, stats, success=(errors == 0))

        return IndexerResult(
            run_id=run_id,
            files_seen=stats["files_seen"],
            files_changed=stats["files_changed"],
            files_deleted=stats["files_deleted"],
            chunks_created=stats["chunks_created"],
            errors_count=errors,
        )
    finally:
        conn.close()


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _build_stats() -> dict:
    return {
        "files_seen": 0,
        "files_changed": 0,
        "files_deleted": 0,
        "chunks_created": 0,
    }


def _find_file_by_path(conn, rel_path: str) -> dict | None:
    row = conn.execute(
        "SELECT id, source_hash, title FROM files WHERE relative_path = ? AND status = 'active'",
        (rel_path,),
    ).fetchone()
    return dict(row) if row else None


def _update_file_record(
    conn, file_uuid: str, source_hash: str, indexed_at: str,
) -> None:
    conn.execute(
        "UPDATE files SET source_hash = ?, indexed_at = ? WHERE id = ?",
        (source_hash, indexed_at, file_uuid),
    )
    conn.commit()


def _cleanup_file_data(conn, file_uuid: str) -> None:
    chunk_rows = conn.execute(
        "SELECT id FROM chunks WHERE file_id = ?", (file_uuid,),
    ).fetchall()

    for row in chunk_rows:
        conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id = ?", (row["id"],))
        conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["id"],))

    conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_uuid,))
    conn.execute("DELETE FROM sections WHERE file_id = ?", (file_uuid,))
    conn.commit()


def _delete_file_from_db(conn, rel_path: str) -> None:
    existing = conn.execute(
        "SELECT id FROM files WHERE relative_path = ? AND status = 'active'",
        (rel_path,),
    ).fetchone()

    if existing is None:
        return

    file_uuid = existing["id"]
    _cleanup_file_data(conn, file_uuid)
    conn.execute("DELETE FROM files WHERE id = ?", (file_uuid,))
    conn.commit()


def _ensure_embedding_profile(
    conn, profile_name: str, provider: object,
) -> None:
    existing = conn.execute(
        "SELECT name, model, dimensions, endpoint FROM embedding_profiles WHERE name = ?",
        (profile_name,),
    ).fetchone()

    dimensions = getattr(provider, "dimensions", 768)
    model = getattr(provider, "model_name", getattr(provider, "_model_name", "default"))
    endpoint = getattr(provider, "endpoint", getattr(provider, "_endpoint", None))

    if existing is None:
        conn.execute(
            "INSERT INTO embedding_profiles (name, model, dimensions, endpoint) VALUES (?, ?, ?, ?)",
            (profile_name, str(model), dimensions, endpoint),
        )
        conn.commit()
        logger.info("Created embedding profile: %s (dims=%d)", profile_name, dimensions)
        return

    if (
        existing["model"] == str(model)
        and existing["dimensions"] == dimensions
        and existing["endpoint"] == endpoint
    ):
        return

    conn.execute(
        "UPDATE embedding_profiles SET model = ?, dimensions = ?, endpoint = ? WHERE name = ?",
        (str(model), dimensions, endpoint, profile_name),
    )
    conn.commit()
    logger.info("Updated embedding profile metadata: %s (dims=%d)", profile_name, dimensions)
