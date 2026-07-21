"""Contract tests for the pre-result MDRack 1.1 evaluation freeze."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "tests/evaluation/v1_1/freeze-manifest.json"
MAPPING = ROOT / "tests/evaluation/v1_1/capability-dod-map.json"
CONFIG = ROOT / "configs/eval-v11.toml"
QUERIES = ROOT / "tests/evaluation/queries-v1/queries.json"
SCRIPT = ROOT / "scripts/build_v11_test_assets.py"


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


def test_capability_and_definition_of_done_mapping_is_complete_but_not_evidence() -> None:
    mapping = _load(MAPPING)
    capability_ids = [item["id"] for item in mapping["capabilities"]]
    dod_ids = [item["id"] for item in mapping["dod_sections"]]

    assert capability_ids == [f"C{index:02d}" for index in range(1, 20)]
    assert dod_ids == [f"29.{index}" for index in range(1, 9)]
    assert all(item["fixture_support"] for item in mapping["dod_sections"])
    assert mapping["evidence_boundary"] == "input fixtures only; mapping is not completion evidence"
