"""Real strict readers, serializers, CLI and privacy tests (no provider)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mdrack.eval.privacy import scan_privacy
from mdrack.eval.quality import QualityEvaluationError
from mdrack.eval.quality_io import (
    dumps_quality_report,
    loads_quality_input,
    loads_quality_report,
    validate_quality_report,
)
from mdrack.eval.quality_v2 import evaluate_quality_v2
from tests.unit.test_eval_quality_v2 import document, temporal_document

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/quality_eval_v2.py"


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    bootstrap = os.environ.get("MDRACK_B2_BOOTSTRAP")
    prefix = ["-I", "-B", "-S", bootstrap, "--script"] if bootstrap else []
    return subprocess.run([sys.executable, *prefix, str(CLI), *args], cwd=ROOT, capture_output=True, text=True)


@pytest.mark.parametrize("temporal", [False, True])
def test_input_report_roundtrip_and_privacy(temporal: bool) -> None:
    value = temporal_document() if temporal else document()
    secret = "PRIVATE_QUERY_CONTENT_SENTINEL"
    value["cases"][0]["case_id"] = secret
    report = evaluate_quality_v2(loads_quality_input(json.dumps(value)))
    rendered = dumps_quality_report(report)
    assert loads_quality_report(rendered) == report
    assert report["input_ref"].startswith("sha256:")
    assert scan_privacy(report, forbidden_values=[secret, "new-recipe", "synthetic-case"]).safe
    assert secret not in rendered
    assert all(not key.endswith("_id") for key in report["results"][0])


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":2,"schema_version":2}',
        "[]",
        "null",
        "{",
        '{"PRIVATE_SENTINEL":NaN}',
        '{"PRIVATE_SENTINEL":Infinity}',
        '{"PRIVATE_SENTINEL":-Infinity}',
    ],
)
def test_invalid_json_is_safe_for_both_readers(text: str) -> None:
    for reader in (loads_quality_input, loads_quality_report):
        with pytest.raises(QualityEvaluationError) as error:
            reader(text)
        assert "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize(
    "mutation", ["version", "unknown", "nan", "boolean", "summary", "miss-zero", "negative-metric"]
)
def test_report_reader_rejects_incompatible_or_fabricated_fields(mutation: str) -> None:
    value = temporal_document()
    value["cases"][0]["ranked"][0]["source_revision"] = "v2"  # genuine miss, nullable errors
    report = evaluate_quality_v2(value)
    row = report["results"][0]["cutoffs"][0]
    if mutation == "version":
        report["schema_version"] = 1
    elif mutation == "unknown":
        report["PRIVATE_SENTINEL"] = "PRIVATE_SENTINEL"
    elif mutation == "nan":
        row["ndcg"] = float("nan")
    elif mutation == "boolean":
        row["relevant_hits"] = False
    elif mutation == "summary":
        report["summary"][0]["means"]["recall"] = 1.0
    elif mutation == "miss-zero":
        row["temporal"]["mean_start_error_ms"] = 0
    else:
        row["negative_returned"] = 0
    with pytest.raises(QualityEvaluationError) as error:
        dumps_quality_report(report)
    assert "PRIVATE_SENTINEL" not in str(error.value)


def test_actual_cli_evaluate_readback_and_refusal_preserves_output(tmp_path: Path) -> None:
    source, destination = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(json.dumps(temporal_document()), encoding="utf-8")
    run = cli("--input", str(source), "--output", str(destination))
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"ok": True, "cases": 1, "schema_version": 2}
    raw = destination.read_bytes()
    report = loads_quality_report(raw.decode())
    assert report["results"][0]["cutoffs"][0]["temporal"]["matches"] == 1
    readback = cli("--report", str(destination))
    assert readback.returncode == 0, readback.stderr
    assert loads_quality_report(readback.stdout) == report
    source.write_text('{"PRIVATE_SENTINEL":NaN}', encoding="utf-8")
    refusal = cli("--input", str(source), "--output", str(destination))
    assert refusal.returncode == 1
    assert refusal.stdout == ""
    assert json.loads(refusal.stderr) == {"ok": False, "error": "quality_evaluation_failed"}
    assert "PRIVATE_SENTINEL" not in refusal.stdout + refusal.stderr
    assert destination.read_bytes() == raw
    assert not list(tmp_path.glob(".quality-*"))


def test_cli_does_not_echo_missing_path_or_unknown_arguments(tmp_path: Path) -> None:
    for args in (("--input", str(tmp_path / "PRIVATE_SENTINEL.json")), ("--PRIVATE_SENTINEL",)):
        run = cli(*args)
        assert run.returncode == 1
        assert "PRIVATE_SENTINEL" not in run.stdout + run.stderr
        assert "Traceback" not in run.stderr


def test_manual_fixture_through_validator_evaluator_and_serializer() -> None:
    fixture = ROOT / "tests/evaluation/manual-v2"
    value = loads_quality_input((fixture / "input.json").read_text(encoding="utf-8"))
    expected = json.loads((fixture / "expected.json").read_text(encoding="utf-8"))
    report = loads_quality_report(dumps_quality_report(evaluate_quality_v2(value)))
    assert len(expected) == len(report["results"])
    for result, wanted in zip(report["results"], expected, strict=True):
        row = result["cutoffs"][-1]
        for key, value in wanted.items():
            if isinstance(value, dict):
                assert all(row[key][k] == v for k, v in value.items())
            else:
                assert row[key] == value


def test_frozen_v1_source_assertions_corpus_and_history_are_preserved() -> None:
    manifest = json.loads((ROOT / "tests/evaluation/manual-v2/v1-preservation.json").read_text(encoding="utf-8"))
    assert manifest["count"] == len(manifest["files"])
    for row in manifest["files"]:
        assert hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"], row["path"]


def test_schema_companions_are_closed_versioned_and_locally_resolved() -> None:
    schemas = ROOT / "tests/evaluation/schemas"
    value = document()
    report = evaluate_quality_v2(value)
    for kind, example in (("input", value), ("report", report)):
        schema = json.loads((schemas / f"quality-{kind}-v2.schema.json").read_text(encoding="utf-8"))
        assert schema["properties"]["schema_version"] == {"const": 2}
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == set(example)

        def walk(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert node["additionalProperties"] is False
                if "$ref" in node:
                    assert node["$ref"].startswith("#/$defs/")
                    assert node["$ref"].split("/")[-1] in schema["$defs"]
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(schema)


@pytest.mark.parametrize("huge", [2**53, 10**400])
def test_huge_coordinate_or_policy_is_safe_contract_error(huge: int) -> None:
    value = temporal_document()
    value["cases"][0]["temporal_policy"]["interval_iou"] = huge
    with pytest.raises(QualityEvaluationError):
        evaluate_quality_v2(value)
    value = temporal_document()
    value["cases"][0]["ranked"][0].update(intervals=[], timestamp_ms=huge)
    with pytest.raises(QualityEvaluationError):
        evaluate_quality_v2(value)


def _report_at_rank(returned: int, first_rank: int | None, hits: int, *, negative: bool = False) -> dict[str, Any]:
    value = document()
    case = value["cases"][0]
    positive = case["judgments"][1]
    ranked_positive = case["ranked"][1]
    case["judgments"] = [
        {**positive, "judgment_id": f"gold-{i}", "unit_id": f"positive-{i}"}
        for i in range(0 if negative else max(1, hits))
    ]
    case["ranked"] = [{**case["ranked"][0], "unit_id": f"unjudged-{i}"} for i in range(returned)]
    if first_rank is not None:
        for i in range(hits):
            case["ranked"][first_rank - 1 + i] = {**ranked_positive, "unit_id": f"positive-{i}"}
    case["case_type"] = "negative" if negative else "positive"
    case["cutoffs"] = [returned]
    return evaluate_quality_v2(value)


def _set_report_mrr(report: dict[str, Any], mrr: float) -> None:
    for row in report["results"][0]["cutoffs"]:
        row["mrr"] = mrr
    for group in report["summary"]:
        group["means"]["mrr"] = mrr  # Deliberately coherent: not a stale-summary rejection.


@pytest.mark.parametrize("boundary", ["validator", "reader", "serializer"])
@pytest.mark.parametrize(
    ("returned", "first_rank", "hits", "impossible_mrr"),
    [
        (1, 1, 1, 0.25),
        (1, 1, 1, 0.5),
        (3, 1, 1, 0.4),
        (3, 2, 2, 1 / 3),
        (3, 1, 1, 0.25),
        (1, 1, 1, 5e-324),
        (3, 3, 1, 0.33333333333333337),
    ],
    ids=[
        "reviewer-rank-four",
        "beyond-returned",
        "not-reciprocal",
        "too-late-for-two-hits",
        "beyond-cutoff",
        "subnormal",
        "near-not-equal-reciprocal",
    ],
)
def test_impossible_mrr_with_coherent_summary_is_rejected(
    boundary: str,
    returned: int,
    first_rank: int,
    hits: int,
    impossible_mrr: float,
) -> None:
    report = _report_at_rank(returned, first_rank, hits)
    _set_report_mrr(report, impossible_mrr)
    with pytest.raises(QualityEvaluationError, match="reciprocal rank"):
        if boundary == "validator":
            validate_quality_report(report)
        elif boundary == "reader":
            loads_quality_report(json.dumps(report))
        else:
            dumps_quality_report(report)


@pytest.mark.parametrize(
    ("returned", "first_rank", "hits", "negative", "expected"),
    [
        (1, 1, 1, False, 1.0),
        (2, 2, 1, False, 0.5),
        (3, 3, 1, False, 1 / 3),
        (4, 3, 2, False, 1 / 3),
        (3, None, 0, False, 0.0),
        (3, None, 0, True, None),
    ],
    ids=["one-result", "rank-two", "rank-three", "latest-first-for-two-hits", "miss", "negative-null"],
)
def test_genuine_mrr_reader_serializer_and_actual_report_cli_controls(
    tmp_path: Path,
    returned: int,
    first_rank: int | None,
    hits: int,
    negative: bool,
    expected: float | None,
) -> None:
    report = _report_at_rank(returned, first_rank, hits, negative=negative)
    assert report["results"][0]["cutoffs"][0]["mrr"] == expected
    assert validate_quality_report(report) == report
    encoded = dumps_quality_report(report)
    assert loads_quality_report(encoded) == report
    source = tmp_path / "genuine.json"
    source.write_text(encoded, encoding="utf-8")
    run = cli("--report", str(source))
    assert run.returncode == 0, run.stderr
    assert loads_quality_report(run.stdout) == report


@pytest.mark.parametrize(
    ("returned", "first_rank", "hits", "impossible_mrr"),
    [(1, 1, 1, 0.25), (3, 1, 1, 0.4), (3, 2, 2, 1 / 3)],
    ids=["reviewer-rank-four", "not-reciprocal", "too-late-for-two-hits"],
)
def test_actual_report_cli_rejects_impossible_mrr_without_output_mutation(
    tmp_path: Path,
    returned: int,
    first_rank: int,
    hits: int,
    impossible_mrr: float,
) -> None:
    report = _report_at_rank(returned, first_rank, hits)
    _set_report_mrr(report, impossible_mrr)
    source = tmp_path / "impossible.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    existing, new = tmp_path / "existing.json", tmp_path / "new.json"
    preimage = b"SYNTHETIC_OUTPUT_MUST_BE_PRESERVED\n"
    existing.write_bytes(preimage)
    runs = [cli("--report", str(source), "--output", str(target)) for target in (existing, new)]
    for run in runs:
        assert run.returncode == 1
        assert run.stdout == ""
        assert json.loads(run.stderr) == {"ok": False, "error": "quality_evaluation_failed"}
        assert "Traceback" not in run.stderr
    assert existing.read_bytes() == preimage
    assert not new.exists()
    assert not list(tmp_path.glob(".quality-*"))


def test_complete_input_validator_rejects_unjudged_and_v1_is_not_migrated() -> None:
    value = document()
    value["cases"][0].update(judgment_policy="complete", unjudged_policy="error")
    value["cases"][0]["ranked"][0]["unit_id"] = "unjudged"
    with pytest.raises(QualityEvaluationError, match="complete judgments"):
        loads_quality_input(json.dumps(value))
    for reader in (loads_quality_input, loads_quality_report):
        with pytest.raises(QualityEvaluationError):
            reader('{"schema_version":1,"report_type":"graded_retrieval_quality"}')
