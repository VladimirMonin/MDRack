"""Executable checks for the compact builtin exact benchmark harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_builtin_exact_benchmark_reports_binary_cold_warm_and_counter_metrics(tmp_path: Path) -> None:
    repository = Path(__file__).parents[2]
    output = tmp_path / "benchmark.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/sqlite_envelope_benchmark.py",
            "--cells",
            "8x2",
            "--codecs",
            "f32,f64,legacy-json",
            "--warmups",
            "0",
            "--repetitions",
            "1",
            "--warm-queries",
            "2",
            "--candidate-limit",
            "3",
            "--output",
            str(output),
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["contract"] == "mdrack.builtin-exact-benchmark-v1"
    assert report["evidence_boundary"] == "local components"
    assert report["privacy"] == {
        "network_attempts": 0,
        "source": "synthetic",
        "temporary_catalogs_removed": True,
    }
    assert report["config"]["codecs"] == ["f32", "f64", "legacy-json"]
    assert report["config"]["vector_fixture"] == "deterministic-normalized-finite-real-shaped-v1"
    assert [cell["codec"] for cell in report["cells"]] == ["f32", "f64", "legacy-json"]
    assert len(report["harness_sha256"]) == len(report["runner_sha256"]) == len(report["input_sha256"]) == 64

    for cell in report["cells"]:
        assert cell["binary_payload_bytes"] == cell["units"] * cell["dimensions"] * cell["component_bytes"]
        assert cell["vector_payload_bytes"] > 0
        if cell["codec"] == "legacy-json":
            assert cell["payload_encoding"] == "canonical_legacy_json_readonly"
        else:
            assert cell["payload_encoding"] == "binary"
        for phase in ("cold", "warm"):
            metrics = cell[phase]["metrics"]
            assert metrics["candidate_rows"]["p50"] == 8
            assert metrics["decoded_vectors"]["p50"] == 8
            assert metrics["skipped_vectors"]["p50"] == 0
            assert metrics["decode_cpu_ms"]["p50"] >= 0.0
            assert metrics["score_cpu_ms"]["p50"] >= 0.0
            assert metrics["sort_ms"]["p50"] >= 0.0
            assert metrics["rss_kib"]["p50"] > 0

    parity = report["parity_oracle"]
    assert parity["contract"] == "mdrack.stage8-multimodal-parity-v1"
    assert parity["source_hashes_unchanged"] is True
    assert parity["deterministic_repeats"] is True
    assert parity["legacy_json_equals_compact_f32"] is True
    assert len(parity["input_sha256"]) == 64
    expected = {
        "notes": (
            "stage8-note",
            "stage8-note-unit",
            {"kind": "line_range", "payload": {"end_line": 3, "start_line": 1}},
        ),
        "audio": (
            "stage8-audio",
            "stage8-audio-unit",
            {"kind": "time_segment", "payload": {"end_ms": 1200, "start_ms": 0, "track": "audio"}},
        ),
        "video": (
            "stage8-video",
            "stage8-video-segment-unit",
            {"kind": "time_segment", "payload": {"end_ms": 1600, "start_ms": 0, "track": "video"}},
        ),
        "frames": (
            "stage8-video",
            "stage8-video-frame-unit",
            {"kind": "video_frame", "payload": {"timestamp_ms": 800}},
        ),
        "images": (
            "stage8-image",
            "stage8-image-unit",
            {"kind": "whole_image", "payload": {"source_ref": "stage8-image"}},
        ),
    }
    assert set(parity["scopes"]) == {*expected, "all"}
    for scope, (resource_id, unit_id, evidence) in expected.items():
        result = parity["scopes"][scope]
        assert result == [
            {
                "resource_id": resource_id,
                "unit_id": unit_id,
                "rank": 1,
                "evidence": evidence,
            }
        ]
    all_results = parity["scopes"]["all"]
    assert [item["rank"] for item in all_results] == [1, 2, 3, 4, 5]
    assert [(item["resource_id"], item["unit_id"]) for item in all_results] == [
        ("stage8-video", "stage8-video-frame-unit"),
        ("stage8-video", "stage8-video-segment-unit"),
        ("stage8-audio", "stage8-audio-unit"),
        ("stage8-note", "stage8-note-unit"),
        ("stage8-image", "stage8-image-unit"),
    ]
