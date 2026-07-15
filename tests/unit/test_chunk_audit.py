"""Tests for privacy-safe chunk audit aggregates."""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from mdrack.eval.chunk_audit import audit_markdown_files


def _load_chunk_audit_script() -> ModuleType:
    script_path = Path(__file__).parents[2] / "scripts" / "chunk_audit.py"
    spec = importlib.util.spec_from_file_location("chunk_audit_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chunk_audit_script_rejects_missing_corpus_without_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_chunk_audit_script()
    output = tmp_path / "audit.json"
    missing_root = tmp_path / "missing"
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunk_audit.py", str(missing_root), "--output", str(output)],
    )

    return_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert return_code != 0
    assert payload == {"error_category": "corpus_missing", "ok": False}
    assert not output.exists()


def test_chunk_audit_script_rejects_empty_corpus_without_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_chunk_audit_script()
    root = tmp_path / "empty-corpus"
    root.mkdir()
    output = tmp_path / "audit.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunk_audit.py", str(root), "--output", str(output)],
    )

    return_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert return_code != 0
    assert payload == {"error_category": "corpus_empty", "ok": False}
    assert not output.exists()


@pytest.mark.parametrize(
    ("failure", "expected_category"),
    [
        (PermissionError(errno.EACCES, "denied"), "corpus_inaccessible"),
        (OSError(errno.EIO, "I/O error"), "corpus_io_error"),
    ],
)
def test_chunk_audit_script_rejects_unreadable_corpus_without_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: OSError,
    expected_category: str,
) -> None:
    module = _load_chunk_audit_script()
    root = tmp_path / "corpus"
    root.mkdir()
    output = tmp_path / "audit.json"
    resolved_root = root.resolve()
    original_stat = Path.stat

    def failing_stat(path: Path, *args: object, **kwargs: object) -> object:
        if path == resolved_root:
            raise failure
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunk_audit.py", str(root), "--output", str(output)],
    )

    return_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert return_code != 0
    assert payload == {"error_category": expected_category, "ok": False}
    assert not output.exists()


def test_chunk_audit_script_rejects_traversal_io_error_without_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_chunk_audit_script()
    root = tmp_path / "corpus"
    root.mkdir()
    output = tmp_path / "audit.json"

    def failing_walk(*args: object, **kwargs: object) -> object:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(os, "walk", failing_walk)
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunk_audit.py", str(root), "--output", str(output)],
    )

    return_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert return_code != 0
    assert payload == {"error_category": "corpus_io_error", "ok": False}
    assert not output.exists()


def test_chunk_audit_reports_aggregates_without_names_or_content(tmp_path: Path) -> None:
    first = tmp_path / "private-lesson.md"
    first.write_text("# Course\n\n## Intro\n\nSECRET_NOTE_SENTINEL " + "word " * 300, encoding="utf-8")
    second = tmp_path / "diagram.md"
    second.write_text("# Diagram\n\n## Flow\n\n```mermaid\ngraph TD\nA-->B\n```\n", encoding="utf-8")

    report = audit_markdown_files([first, second], corpus_ref="sha256:test", max_files=2)
    payload = report.to_dict()
    rendered = str(payload)

    assert payload["schema_version"] == 1
    assert payload["parser_name"] == "markdown_it"
    assert payload["chunk_strategy_name"] == "structural_blocks"
    assert payload["corpus_ref"] == "sha256:test"
    assert payload["metrics"]["files_count"] == 2
    assert payload["metrics"]["files_attempted_count"] == 2
    assert payload["metrics"]["files_succeeded_count"] == 2
    assert payload["metrics"]["files_failed_count"] == 0
    assert payload["metrics"]["blocks_count"] >= 4
    assert payload["metrics"]["chunks_count"] >= 2
    assert payload["metrics"]["diagram_count"] == 1
    assert payload["metrics"]["source_span_missing_count"] == 0
    assert payload["metrics"]["orphan_block_count"] == 0
    assert "chunk_length_p50" in payload["metrics"]
    assert "chunk_tokens_p90" in payload["metrics"]
    assert "private-lesson.md" not in rendered
    assert "SECRET_NOTE_SENTINEL" not in rendered


def test_chunk_audit_script_rejects_when_all_selected_files_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_chunk_audit_script()
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "invalid.md").write_bytes(b"\xff\xfe")
    output = tmp_path / "audit.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["chunk_audit.py", str(root), "--output", str(output)],
    )

    return_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert return_code != 0
    assert payload == {"error_category": "corpus_parse_failed", "ok": False}
    assert not output.exists()


def test_chunk_audit_is_deterministic_for_same_inputs(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Title\n\n## Part\n\nBody", encoding="utf-8")

    first = audit_markdown_files([note], corpus_ref="sha256:same", max_files=1).to_dict()
    second = audit_markdown_files([note], corpus_ref="sha256:same", max_files=1).to_dict()

    assert first == second


def test_chunk_audit_reports_attempted_succeeded_and_failed_denominators(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.md"
    valid.write_text("# Valid\n\nBody", encoding="utf-8")
    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff\xfe")

    metrics = audit_markdown_files(
        [valid, invalid], corpus_ref="sha256:mixed", max_files=2
    ).metrics

    assert metrics["files_attempted_count"] == 2
    assert metrics["files_succeeded_count"] == 1
    assert metrics["files_failed_count"] == 1
    assert (
        metrics["files_attempted_count"]
        == metrics["files_succeeded_count"] + metrics["files_failed_count"]
    )
