"""Local SQLite preservation of Markdown source metadata."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.compatibility import prepared_file_to_resource_batch
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack_sqlite import SQLiteCatalog


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def test_markdown_metadata_round_trips_without_source_or_text_mutation(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    source = (
        "---\n"
        "title: Metadata example\n"
        "tags: [python, retrieval]\n"
        "draft: false\n"
        "priority: 3\n"
        "nested: {project: MDRack}\n"
        "secret: PRIVATE_METADATA_SENTINEL\n"
        "first_invalid: .nan\n"
        "second_invalid: .inf\n"
        "---\n"
        "# Metadata example\n\n"
        "Visible retrieval body.\n"
    )
    note.write_text(source, encoding="utf-8", newline="")
    before = note.read_bytes()
    document = MarkdownItParser().parse(
        note,
        document_id="document-metadata",
        relative_path="note.md",
    )
    body_block = next(block for block in document.blocks if block.plain_text == "Visible retrieval body.")
    section = StoredSection(
        "section-row",
        "section-logical",
        "Metadata example",
        ("Metadata example",),
        1,
        body_block.source_span.start_line,
        body_block.source_span.end_line,
        None,
    )
    chunk = StoredChunk(
        "chunk-row",
        "chunk-logical",
        "section-row",
        body_block.plain_text or "",
        "text",
        0,
        body_block.heading_path,
        None,
        None,
        body_block.plain_text or "",
        hashlib.sha256((body_block.plain_text or "").encode()).hexdigest(),
        body_block.source_span.start_line,
        body_block.source_span.end_line,
        body_block.block_id,
        body_block.source_span.start_offset,
        body_block.source_span.end_offset,
        body_block.block_type.value,
        "text",
    )
    prepared = PreparedFile(
        record_id="file-row",
        logical_id=document.document_id,
        root_id="vault",
        relative_path=document.relative_path,
        title=document.title,
        source_hash=document.source_hash,
        indexed_at="2026-07-20T00:00:00+00:00",
        parser_name=document.parser_name,
        parser_version=document.parser_version,
        chunk_strategy_name="structural",
        chunk_strategy_version="2",
        index_run_id="run-row",
        sections=(section,),
        chunks=(chunk,),
        source_metadata=document.frontmatter,
        metadata_diagnostics=document.metadata_diagnostics,
        metadata_fingerprint=document.metadata_fingerprint,
        metadata_policy_fingerprint=document.metadata_policy_fingerprint,
        metadata_normalizer_version=document.metadata_normalizer_version,
    )
    batch = prepared_file_to_resource_batch(prepared)

    database = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        catalog.replace_resource(batch)
        stored = catalog.read_resource(document.document_id)

    assert stored is not None
    assert _plain(stored.metadata["source"]) == dict(document.frontmatter)
    assert stored.metadata["ingestion"]["metadata_fingerprint"] == document.metadata_fingerprint
    assert stored.metadata["derived"]["metadata_key_count"] == len(document.frontmatter)
    assert stored.metadata["derived"]["diagnostic_count"] == 2
    assert _plain(stored.metadata["derived"]["diagnostic_categories"]) == [
        "METADATA_NON_FINITE_NUMBER"
    ]
    assert stored.metadata["derived"]["diagnostic_counts"] == {
        "METADATA_NON_FINITE_NUMBER": 2
    }
    assert batch.representations[0].text == "Visible retrieval body."
    assert batch.units[0].text == "Visible retrieval body."
    assert "PRIVATE_METADATA_SENTINEL" not in (batch.representations[0].text or "")
    assert "PRIVATE_METADATA_SENTINEL" not in (batch.units[0].text or "")
    assert note.read_bytes() == before
