"""Text search across chunks using FTS5 with provenance join."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field

from mdrack.domain.indexing import SourceLocator
from mdrack.storage.sqlite.fts import FTSQueryError, search_fts

logger = logging.getLogger(__name__)


@dataclass
class TextSearchItem:
    """A single text search result with provenance."""

    chunk_id: str
    score: float
    snippet: str
    file_relative_path: str
    section_title: str | None
    heading_path: str | None
    source_locator: SourceLocator | None = None


@dataclass
class TextSearchResult:
    """Aggregated result of a text search query."""

    query: str
    results: list[TextSearchItem] = field(default_factory=list)
    total_count: int = 0


def text_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    offset: int = 0,
) -> TextSearchResult:
    """Search chunks via FTS5 and enrich results with file/section provenance.

    Args:
        conn: An open SQLite connection.
        query: FTS5 query string.
        limit: Maximum number of results to return.
        offset: Number of results to skip for pagination.

    Returns:
        TextSearchResult with enriched items.

    Raises:
        FTSQueryError: If the FTS query is invalid.
    """
    if not query.strip():
        raise FTSQueryError("Search query must not be empty")

    try:
        # Use a larger internal limit to cover offset, then slice.
        # FTS5 does not support OFFSET natively, so we fetch limit+offset
        # rows and drop the first `offset` items.
        fetch_limit = limit + offset
        raw = _search_fts_with_offset(conn, query, fetch_limit)
        sliced = raw[offset : offset + limit]
        total = len(raw)
    except FTSQueryError:
        raise

    items: list[TextSearchItem] = []
    if sliced:
        placeholders = ",".join("?" for _ in sliced)
        chunk_ids = [r["chunk_id"] for r in sliced]
        rows = conn.execute(
            f"""
            SELECT
                c.id AS chunk_id,
                c.heading_path,
                c.logical_id,
                c.block_logical_id,
                c.start_line,
                c.end_line,
                c.start_offset,
                c.end_offset,
                c.block_kind,
                c.chunk_kind,
                f.root_id,
                f.relative_path,
                s.title AS section_title
            FROM chunks c
            JOIN files f ON c.file_id = f.id
            LEFT JOIN sections s ON c.section_id = s.id
            WHERE c.id IN ({placeholders})
            """,
            chunk_ids,
        ).fetchall()
        provenance = {row["chunk_id"]: dict(row) for row in rows}

        rank_map = {r["chunk_id"]: r["rank"] for r in sliced}
        snippet_map = {r["chunk_id"]: r["snippet"] for r in sliced}

        for cid in chunk_ids:
            prov = provenance.get(cid, {})
            heading_path = prov.get("heading_path")
            headings = _decode_heading_path(heading_path)
            logical_chunk_id = prov.get("logical_id") or cid
            block_id = prov.get("block_logical_id") or logical_chunk_id
            items.append(
                TextSearchItem(
                    chunk_id=logical_chunk_id,
                    score=rank_map.get(cid, 0.0),
                    snippet=snippet_map.get(cid, ""),
                    file_relative_path=prov.get("relative_path", ""),
                    section_title=prov.get("section_title"),
                    heading_path=heading_path,
                    source_locator=SourceLocator(
                        root_id=prov.get("root_id") or "default",
                        relative_path=prov.get("relative_path", ""),
                        start_line=prov.get("start_line") or 1,
                        end_line=prov.get("end_line") or 1,
                        heading_path=headings,
                        block_id=block_id,
                        chunk_id=logical_chunk_id,
                        start_offset=prov.get("start_offset"),
                        end_offset=prov.get("end_offset"),
                        block_kind=prov.get("block_kind") or "unknown",
                        chunk_kind=prov.get("chunk_kind") or "unknown",
                    ),
                )
            )

    return TextSearchResult(query=query, results=items, total_count=total)


def _decode_heading_path(value: str | None) -> tuple[str, ...]:
    """Read both new JSON heading paths and legacy display strings."""
    if not value:
        return ()
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return tuple(part.strip() for part in value.split(">") if part.strip())
    if isinstance(decoded, list):
        return tuple(str(part) for part in decoded)
    return (str(decoded),)


def _search_fts_with_offset(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
) -> list[dict]:
    """Run FTS5 search returning up to *limit* rows.

    Delegates to the existing ``search_fts`` helper so we stay compatible
    with the single FTS interface.
    """
    return search_fts(conn, query, limit=limit)
