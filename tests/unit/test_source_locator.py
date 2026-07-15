"""Validation contracts for portable source locators."""

from __future__ import annotations

import pytest

from mdrack.domain.indexing import SourceLocator


def _locator(**overrides: object) -> SourceLocator:
    values: dict[str, object] = {
        "root_id": "default",
        "relative_path": "notes/example.md",
        "start_line": 1,
        "end_line": 2,
        "heading_path": ("Example",),
        "block_id": "block_123",
        "chunk_id": "chunk_123",
    }
    values.update(overrides)
    return SourceLocator(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/private/note.md",
        "../escape.md",
        "notes/../escape.md",
        "./note.md",
        "notes/./note.md",
        "notes/",
        "note.md/",
        "notes//note.md",
        ".",
        "C:/private.md",
        "C:\\private.md",
        "//server/share.md",
        "\\\\server\\share.md",
    ],
)
def test_source_locator_rejects_unsafe_paths(relative_path: str) -> None:
    with pytest.raises(ValueError):
        _locator(relative_path=relative_path)


@pytest.mark.parametrize("root_id", ["", "../root", "root/path", "C:", " white space "])
def test_source_locator_rejects_invalid_root_ids(root_id: str) -> None:
    with pytest.raises(ValueError):
        _locator(root_id=root_id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_line": 0},
        {"end_line": 0},
        {"start_line": 3, "end_line": 2},
        {"block_id": ""},
        {"chunk_id": ""},
    ],
)
def test_source_locator_rejects_invalid_spans_and_identifiers(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _locator(**overrides)
