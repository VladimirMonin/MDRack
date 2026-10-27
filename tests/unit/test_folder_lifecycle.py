"""Private selected-file lifecycle state: deterministic, atomic and fail-closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdrack.application.folder_lifecycle import (
    FOLDER_LIFECYCLE_SCHEMA,
    FolderLifecycle,
    FolderLifecycleError,
    LifecycleFile,
    mark_unavailable,
)


def _entry(source_ref: str, *, namespace_kind: str = "document") -> LifecycleFile:
    return LifecycleFile(
        source_ref,
        "document" if namespace_kind == "document" else "image",
        "text/markdown" if namespace_kind == "document" else "image/png",
        f"resource:{source_ref}",
        "available",
        "sha256:" + "a" * 64,
        12,
    )


def test_lifecycle_round_trip_is_deterministic_and_preserves_last_known_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store" / "folder-lifecycle.json"
    lifecycle = FolderLifecycle.load(path)
    assert lifecycle.files("alpha") == ()

    first = _entry("z.md")
    second = _entry("a.png", namespace_kind="image")
    assert lifecycle.update("beta", first) is True
    assert lifecycle.update("alpha", second) is True
    assert lifecycle.update("alpha", second) is False
    lifecycle.save()

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema"] == FOLDER_LIFECYCLE_SCHEMA
    assert [root["namespace"] for root in raw["roots"]] == ["alpha", "beta"]
    assert [item["source_ref"] for item in raw["roots"][0]["files"]] == ["a.png"]

    reopened = FolderLifecycle.load(path)
    assert reopened.files("alpha") == (second,)
    assert reopened.files("beta") == (first,)

    unavailable = mark_unavailable(first)
    assert unavailable.availability == "unavailable"
    assert unavailable.reason == "source_missing"
    assert unavailable.source_hash == first.source_hash
    assert unavailable.source_bytes == first.source_bytes
    assert reopened.update("beta", unavailable) is True
    reopened.save()
    assert FolderLifecycle.load(path).files("beta") == (unavailable,)


def test_lifecycle_invalid_duplicate_and_unexpected_shapes_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "folder-lifecycle.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FolderLifecycleError, match="folder_lifecycle_invalid"):
        FolderLifecycle.load(path)

    path.write_text(json.dumps({
        "schema": FOLDER_LIFECYCLE_SCHEMA,
        "roots": [{
            "namespace": "alpha",
            "files": [
                _entry("a.md").to_dict(),
                _entry("a.md").to_dict(),
            ],
        }],
    }), encoding="utf-8")
    with pytest.raises(FolderLifecycleError, match="folder_lifecycle_invalid"):
        FolderLifecycle.load(path)

    path.write_text(json.dumps({
        "schema": FOLDER_LIFECYCLE_SCHEMA,
        "roots": [{"namespace": "alpha", "files": [], "unexpected": True}],
    }), encoding="utf-8")
    with pytest.raises(FolderLifecycleError, match="folder_lifecycle_invalid"):
        FolderLifecycle.load(path)


def test_lifecycle_entry_rejects_private_absolute_or_unsafe_references(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        LifecycleFile(
            "../outside.md", "document", "text/markdown", "resource",
            "available", "sha256:" + "a" * 64, 1,
        )
    with pytest.raises(ValueError):
        LifecycleFile(
            "a.md", "document", "text/markdown", "resource",
            "available", "not-a-digest", 1,
        )
    with pytest.raises(ValueError):
        LifecycleFile(
            "a.md", "document", "text/markdown", "resource",
            "unavailable", None, None, None,
        )
    assert not (tmp_path / "folder-lifecycle.json").exists()
