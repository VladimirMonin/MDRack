"""Contract tests for the pre-result MDRack 1.1 evaluation freeze."""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "tests/evaluation/v1_1/freeze-manifest.json"
MAPPING = ROOT / "tests/evaluation/v1_1/capability-dod-map.json"
CONFIG = ROOT / "configs/eval-v11.toml"
QUERIES = ROOT / "tests/evaluation/queries-v1/queries.json"
SCRIPT = ROOT / "scripts/check_v11_input_freeze.py"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_freeze_generator_check_is_stable_and_result_free() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)

    assert summary["status"] == "ok"
    assert summary["phase"] == "input_freeze"
    assert summary["candidate_results_allowed"] is False
    assert summary["resources"] == 50
    assert summary["queries"] == 170


def test_judgments_and_all_input_bytes_are_frozen_before_candidate_results() -> None:
    manifest = _load(MANIFEST)
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    queries = _load(QUERIES)

    assert manifest["phase"] == "input_freeze"
    assert manifest["candidate_output_policy"] == {
        "allowed": False,
        "required_consumer_check": "freeze contract and byte digests must match before evaluation",
    }
    assert config["candidate_results_allowed"] is False
    assert all(case["judgments"] for case in queries["cases"])
    assert not ({"results", "metrics", "scores", "candidate_output"} & set(manifest))

    for relative_path, expected in manifest["input_sha256"].items():
        actual = "sha256:" + hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected


@pytest.fixture
def freeze_view(tmp_path: Path) -> Path:
    manifest = _load(MANIFEST)
    additions = runpy.run_path(str(SCRIPT))["V2_ADDITIONS"]
    for name in (*manifest["input_sha256"], *additions, MANIFEST.relative_to(ROOT).as_posix()):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    return tmp_path


@pytest.mark.parametrize("mutation", ["changed", "missing", "unknown", "manifest", "escape", "duplicate"])
def test_freeze_adapter_refuses_input_drift(freeze_view: Path, mutation: str) -> None:
    checker = runpy.run_path(str(SCRIPT))
    manifest_path = freeze_view / MANIFEST.relative_to(ROOT)
    manifest = _load(manifest_path)
    first = next(iter(manifest["input_sha256"]))
    if mutation == "changed":
        (freeze_view / first).write_bytes(b"changed")
    elif mutation == "missing":
        (freeze_view / first).unlink()
    elif mutation == "unknown":
        (freeze_view / "tests/evaluation/schemas/unknown.schema.json").write_text("{}")
    elif mutation == "manifest":
        manifest["phase"] = "results"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "escape":
        manifest["input_sha256"]["../outside"] = manifest["input_sha256"].pop(first)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        text = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(text.replace('"phase": "input_freeze"',
                                             '"phase": "input_freeze", "phase": "input_freeze"'), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        checker["check"](freeze_view)


def test_freeze_adapter_no_source_writes_and_failure_cleanup(freeze_view: Path, tmp_path: Path, monkeypatch) -> None:
    checker = runpy.run_path(str(SCRIPT))
    before = {p.relative_to(freeze_view): p.read_bytes() for p in freeze_view.rglob("*") if p.is_file()}
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    assert checker["check"](freeze_view, temporary_parent=temporary)["resources"] == 50
    assert list(temporary.iterdir()) == []
    monkeypatch.setattr(checker["subprocess"], "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", ""))
    with pytest.raises(ValueError, match="original_generator_failed"):
        checker["check"](freeze_view, temporary_parent=temporary)
    assert list(temporary.iterdir()) == []
    assert before == {p.relative_to(freeze_view): p.read_bytes() for p in freeze_view.rglob("*") if p.is_file()}


def test_capability_and_definition_of_done_mapping_is_complete_but_not_evidence() -> None:
    mapping = _load(MAPPING)
    capability_ids = [item["id"] for item in mapping["capabilities"]]
    dod_ids = [item["id"] for item in mapping["dod_sections"]]

    assert capability_ids == [f"C{index:02d}" for index in range(1, 20)]
    assert dod_ids == [f"29.{index}" for index in range(1, 9)]
    assert all(item["fixture_support"] for item in mapping["dod_sections"])
    assert mapping["evidence_boundary"] == "input fixtures only; mapping is not completion evidence"
