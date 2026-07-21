"""M1 transport contracts across both PreparedFile construction paths."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

import mdrack.application.indexing as indexing_module
from mdrack.application.compatibility import prepared_file_to_resource_batch
from mdrack.application.indexing import IndexingService
from mdrack.application.metadata_normalization import normalize_metadata
from mdrack.config.models import MDRackConfig, ParsingConfig, PathsConfig
from mdrack.domain.indexing import PreparedFile


class _CaptureStorage:
    def __init__(self) -> None:
        self.prepared: list[PreparedFile] = []

    def get_file_by_path(self, relative_path: str) -> None:
        return None

    def start_run(self, **kwargs: Any) -> str:
        return "run"

    def plan_changes(self, scanned: list[Path], root: Path) -> Any:
        return SimpleNamespace(
            new_files=scanned,
            changed_files=[],
            unchanged_files=[],
            deleted_files=[],
        )

    def replace_file(self, prepared: PreparedFile) -> None:
        self.prepared.append(prepared)

    def delete_file(self, relative_path: str) -> None:
        raise AssertionError("unexpected delete")

    def record_error(self, run_id: str, code: str, *, file_ref: str) -> None:
        raise AssertionError(f"unexpected indexing error: {code}")

    def finish_run(self, *args: Any, **kwargs: Any) -> None:
        return None

    def close(self) -> None:
        return None


def _prepare(
    tmp_path: Path,
    backend: Literal["markdown_it", "legacy"],
    content: str,
) -> PreparedFile:
    root = tmp_path / backend
    root.mkdir()
    (root / "note.md").write_text(content, encoding="utf-8", newline="")
    config = MDRackConfig(
        paths=PathsConfig(root=".", store=str(tmp_path / f"store-{backend}")),
        parsing=ParsingConfig(backend=backend),
    )
    storage = _CaptureStorage()
    service = IndexingService(root, config, cast(Any, storage), provider=None)
    prepared = service._prepare_file(Path("note.md"), "run")
    return prepared


@pytest.mark.parametrize("backend", ["markdown_it", "legacy"])
def test_both_prepared_file_paths_carry_metadata_into_resource_only(
    tmp_path: Path,
    backend: Literal["markdown_it", "legacy"],
) -> None:
    prepared = _prepare(
        tmp_path,
        backend,
        "---\ntitle: Stable\nsecret: PRIVATE_METADATA_SENTINEL\n---\n# Stable\n\nVisible body.",
    )
    batch = prepared_file_to_resource_batch(prepared)

    assert prepared.source_metadata == {
        "secret": "PRIVATE_METADATA_SENTINEL",
        "title": "Stable",
    }
    assert batch.resource.metadata["source"] == prepared.source_metadata
    assert batch.resource.metadata["ingestion"]["normalizer_version"] == "metadata-json-v1"
    assert batch.resource.metadata["derived"]["metadata_key_count"] == 2
    assert all(unit.metadata.get("secret") is None for unit in batch.units)
    assert all("PRIVATE_METADATA_SENTINEL" not in (unit.text or "") for unit in batch.units)
    assert all(
        "PRIVATE_METADATA_SENTINEL" not in (representation.text or "")
        for representation in batch.representations
    )


def test_metadata_changes_do_not_change_body_chunks_or_embedding_inputs(tmp_path: Path) -> None:
    first = _prepare(
        tmp_path,
        "markdown_it",
        "---\nsecret: first\n---\n# Stable\n\nVisible body.",
    )
    first_path = tmp_path / "markdown_it" / "note.md"
    first_path.write_text(
        "---\nsecret: second\nnested: {value: 2}\n---\n# Stable\n\nVisible body.",
        encoding="utf-8",
        newline="",
    )
    config = MDRackConfig(
        paths=PathsConfig(root=".", store=str(tmp_path / "store-second")),
        parsing=ParsingConfig(backend="markdown_it"),
    )
    second = IndexingService(
        tmp_path / "markdown_it",
        config,
        cast(Any, _CaptureStorage()),
        provider=None,
    )._prepare_file(Path("note.md"), "run")

    assert first.source_hash != second.source_hash
    assert first.metadata_fingerprint != second.metadata_fingerprint
    assert [chunk.content for chunk in first.chunks] == [chunk.content for chunk in second.chunks]
    assert [chunk.embedding_text for chunk in first.chunks] == [
        chunk.embedding_text for chunk in second.chunks
    ]
    assert [chunk.logical_id for chunk in first.chunks] == [chunk.logical_id for chunk in second.chunks]


def test_safe_parse_diagnostic_reaches_resource_without_source_values(tmp_path: Path) -> None:
    sentinel = "PRIVATE_METADATA_SENTINEL"
    prepared = _prepare(
        tmp_path,
        "markdown_it",
        f"---\nsecret: [{sentinel}\n---\n# Public\n\nVisible body.",
    )
    batch = prepared_file_to_resource_batch(prepared)

    assert prepared.source_metadata == {}
    assert [
        (diagnostic.category, diagnostic.count)
        for diagnostic in prepared.metadata_diagnostics
    ] == [("METADATA_PARSE_FAILED", 1)]
    assert batch.resource.metadata["derived"]["diagnostic_categories"] == (
        "METADATA_PARSE_FAILED",
    )
    assert batch.resource.metadata["derived"]["diagnostic_counts"] == {
        "METADATA_PARSE_FAILED": 1,
    }
    assert batch.resource.metadata["derived"]["diagnostic_count"] == 1
    assert sentinel not in repr(batch.resource.metadata["derived"])
    assert sentinel not in (batch.representations[0].text or "")


def test_repeated_diagnostic_counts_survive_markdown_it_prepared_file_path(
    tmp_path: Path,
) -> None:
    prepared = _prepare(
        tmp_path,
        "markdown_it",
        "---\nfirst: .nan\nsecond: .inf\n---\n# Stable\n\nVisible body.",
    )
    batch = prepared_file_to_resource_batch(prepared)

    assert [
        (diagnostic.category, diagnostic.count)
        for diagnostic in prepared.metadata_diagnostics
    ] == [("METADATA_NON_FINITE_NUMBER", 2)]
    assert batch.resource.metadata["derived"] == {
        "metadata_key_count": 0,
        "diagnostic_count": 2,
        "diagnostic_categories": ("METADATA_NON_FINITE_NUMBER",),
        "diagnostic_counts": {"METADATA_NON_FINITE_NUMBER": 2},
    }
    assert "first" not in repr(batch.resource.metadata["derived"])
    assert "second" not in repr(batch.resource.metadata["derived"])
    assert batch.representations[0].text == "Stable\n\nVisible body."
    assert next(unit.text for unit in batch.units if unit.unit_kind == "text_chunk") == "Visible body."


def test_repeated_diagnostic_counts_survive_legacy_prepared_file_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalize_metadata({"first": float("nan"), "second": float("inf")})
    monkeypatch.setattr(indexing_module, "normalize_metadata", lambda value: normalized)

    prepared = _prepare(tmp_path, "legacy", "# Stable\n\nVisible body.")
    batch = prepared_file_to_resource_batch(prepared)

    assert [(item.category, item.count) for item in prepared.metadata_diagnostics] == [
        ("METADATA_NON_FINITE_NUMBER", 2)
    ]
    assert batch.resource.metadata["derived"]["diagnostic_count"] == 2
    assert batch.resource.metadata["derived"]["diagnostic_counts"] == {
        "METADATA_NON_FINITE_NUMBER": 2
    }


def test_prepared_file_legacy_category_constructor_defaults_to_count_one(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path, "markdown_it", "# Stable\n\nVisible body.")
    legacy = replace(prepared, metadata_diagnostics=cast(Any, ("LEGACY_METADATA_WARNING",)))
    batch = prepared_file_to_resource_batch(legacy)

    assert [(item.category, item.count) for item in legacy.metadata_diagnostics] == [
        ("LEGACY_METADATA_WARNING", 1)
    ]
    assert batch.resource.metadata["derived"]["diagnostic_count"] == 1
