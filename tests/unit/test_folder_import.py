"""Unit controls for literal text preparation, root registry and scan policy."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mdrack.application import folder_import
from mdrack.application.folder_import import (
    FolderFileResult,
    FolderImportError,
    _bind_registry,
    _load_registry,
    _overall_status,
    _reason_for_exception,
    _scan_root,
    _write_registry,
)
from mdrack.application.indexing import IndexingService
from mdrack.config.models import ChunkingConfig, MDRackConfig
from mdrack.ingestion.image_preprocessing import ImagePreparationError
from mdrack.ingestion.raw_source_provenance import RawSourceError, RawSourceErrorCode


class _PrepareOnlyStorage:
    def get_file_by_path(self, relative_path: str) -> object:
        raise AssertionError(f"unexpected catalog read: {relative_path}")


def test_plain_text_preparation_is_literal_span_exact_and_explicitly_chunked(tmp_path: Path) -> None:
    text = (
        "# literal heading\n\n"
        "- literal list item\n\n"
        "[literal link](missing-target.md)\n\n"
        "![literal image](missing-image.png)\n\n"
        "START_SENTINEL "
        + ("plain literal sentence. " * 80)
        + " END_SENTINEL\n"
    )
    service = IndexingService(
        tmp_path,
        MDRackConfig(chunking=ChunkingConfig(
            min_chunk_chars=1,
            target_chunk_chars=400,
            hard_limit_chars=800,
            overlap_chars=0,
            max_chunk_tokens=1000,
        )),
        _PrepareOnlyStorage(),  # type: ignore[arg-type]
        provider=None,
        profile="native",
        root_id="fixture",
    )
    prepared = service.prepare_selected_plain_text(
        source_ref="docs/literal.txt",
        content=text,
        document_logical_id="doc_literal",
        record_id="record",
        run_id="run",
    )
    assert prepared.parser_name == "plain_text"
    assert prepared.relative_path == "docs/literal.txt"
    assert prepared.source_hash == hashlib.sha256(text.encode()).hexdigest()
    assert prepared.source_metadata == {}
    assert len(prepared.chunks) > 1
    assert all(chunk.heading_path == () for chunk in prepared.chunks)
    assert all(chunk.block_kind == "paragraph" for chunk in prepared.chunks)
    joined = "".join(chunk.content for chunk in prepared.chunks)
    assert "# literal heading" in joined
    assert "- literal list item" in joined
    assert "[literal link](missing-target.md)" in joined
    assert "![literal image](missing-image.png)" in joined
    assert any("START_SENTINEL" in chunk.content for chunk in prepared.chunks)
    assert "END_SENTINEL" in prepared.chunks[-1].content
    for chunk in prepared.chunks:
        assert chunk.start_offset is not None and chunk.end_offset is not None
        assert chunk.start_offset >= 0 and chunk.end_offset <= len(text)
        assert text[chunk.start_offset:chunk.end_offset] == chunk.content


def test_registry_round_trip_and_durable_conflicts(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    registry_path = tmp_path / ".mdrack" / "folders.json"

    entries = _bind_registry((), (("alpha", first),))
    assert entries == (("alpha", str(first.resolve())),)
    _write_registry(registry_path, entries)
    assert _load_registry(registry_path) == entries
    assert _bind_registry(entries, (("alpha", first),)) == entries

    with pytest.raises(FolderImportError, match="namespace_root_conflict"):
        _bind_registry(entries, (("alpha", second),))
    with pytest.raises(FolderImportError, match="root_namespace_conflict"):
        _bind_registry(entries, (("beta", first),))

    registry_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FolderImportError, match="folder_registry_invalid"):
        _load_registry(registry_path)

    registry_path.write_text(json.dumps({
        "schema": "mdrack.folder-roots.v1",
        "roots": [{"namespace": "alpha", "path": "relative/path"}],
    }), encoding="utf-8")
    with pytest.raises(FolderImportError, match="folder_registry_invalid"):
        _load_registry(registry_path)


def test_scan_policy_preserves_exclusions_and_refuses_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "selected"
    (root / "nested").mkdir(parents=True)
    (root / "keep.md").write_text("# Keep\n\nKEEP.\n", encoding="utf-8")
    (root / "nested" / "note.txt").write_text("nested literal\n", encoding="utf-8")
    (root / "private").mkdir()
    (root / "private" / "secret.md").write_text("secret\n", encoding="utf-8")
    (root / "skip.tmp").write_text("temporary\n", encoding="utf-8")
    (root / "unknown.pdf").write_bytes(b"%PDF-1.7")
    (root / "linked.md").write_text("# Linked\n\nnot followed\n", encoding="utf-8")
    (root / "linked_dir").mkdir()
    (root / "linked_dir" / "inside.md").write_text("inside\n", encoding="utf-8")

    original = folder_import._is_link_like

    def fake_link_like(path: Path) -> bool:
        if path.name in {"linked.md", "linked_dir"}:
            return True
        return original(path)

    monkeypatch.setattr(folder_import, "_is_link_like", fake_link_like)
    outcome = _scan_root("fixture", root, exclude=("private/**", "*.tmp"))
    assert [(item.namespace, item.source_ref, item.kind) for item in outcome.candidates] == [
        ("fixture", "keep.md", "document"),
        ("fixture", "nested/note.txt", "document"),
    ]
    assert outcome.excluded == 2
    reasons = {(item.source_ref, item.status, item.reason) for item in outcome.results}
    assert ("linked.md", "skipped", "link_not_followed") in reasons
    assert ("linked_dir", "skipped", "link_not_followed") in reasons
    assert ("unknown.pdf", "skipped", "unsupported_media_type") in reasons
    assert all("inside.md" not in item.source_ref for item in outcome.candidates)
    assert outcome.complete is True
    assert {"private/secret.md", "skip.tmp", "unknown.pdf"} <= outcome.observed_refs
    assert {"linked.md", "linked_dir"} <= outcome.blocked_prefixes


def test_status_and_reason_mapping_are_honest_and_payload_free() -> None:
    def result(status: str, reason: str | None = None) -> FolderFileResult:
        return FolderFileResult("fixture", "a.md", "document", "text/markdown", status, reason)

    assert _overall_status(()) == "success"
    assert _overall_status((result("skipped", "unchanged"),)) == "success"
    assert _overall_status((result("skipped", "unsupported_media_type"),)) == "partial_success"
    assert _overall_status((result("failed", "source_too_large"),)) == "failed"
    assert _overall_status((
        result("added"),
        result("failed", "source_too_large"),
    )) == "partial_success"
    assert _overall_status((
        result("added"),
        result("skipped", "link_not_followed"),
    )) == "partial_success"
    assert _overall_status((result("unavailable", "source_missing"),)) == "success"
    assert _overall_status((
        result("unavailable", "source_missing"),
        result("failed", "source_too_large"),
    )) == "partial_success"

    assert _reason_for_exception(
        RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED), "document",
    ) == "source_not_utf8"
    assert _reason_for_exception(
        RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED), "image",
    ) == "unsupported_image_format"
    assert _reason_for_exception(
        RawSourceError(RawSourceErrorCode.SOURCE_TOO_LARGE), "document",
    ) == "source_too_large"
    assert _reason_for_exception(ImagePreparationError("image_decode_failed"), "image") == "image_decode_failed"
    assert _reason_for_exception(ValueError("embedding_input_too_large"), "document") == "embedding_input_too_large"
    assert _reason_for_exception(ValueError("PRIVATE_SENTINEL"), "document") == "ingest_failed"
    assert _reason_for_exception(RuntimeError("PRIVATE_SENTINEL"), "document") == "ingest_failed"
    assert "PRIVATE_SENTINEL" not in _reason_for_exception(RuntimeError("PRIVATE_SENTINEL"), "document")



def test_scan_reports_directory_errors_as_unprocessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    (root / "keep.md").write_text("# Keep\n\nKEEP.\n", encoding="utf-8")
    (root / "locked").mkdir()
    (root / "locked" / "secret.md").write_text("secret\n", encoding="utf-8")
    original_scandir = os.scandir

    def fake_scandir(path: object) -> object:
        if Path(path).name == "locked":  # type: ignore[arg-type]
            raise PermissionError("PRIVATE_OS_SENTINEL")
        return original_scandir(path)

    monkeypatch.setattr(folder_import.os, "scandir", fake_scandir)
    outcome = _scan_root("fixture", root, exclude=())
    assert [item.source_ref for item in outcome.candidates] == ["keep.md"]
    assert any(
        item.status == "unprocessed"
        and item.reason == "directory_unavailable"
        and item.source_ref == "locked"
        for item in outcome.results
    )
    assert outcome.complete is False
    assert "PRIVATE_OS_SENTINEL" not in repr(outcome)
