"""Offline tests for evidence-based LM Studio capability reporting."""

from __future__ import annotations

from scripts.live_lmstudio_eval import build_capability_report


def test_report_resolves_real_catalog_key_variants_for_all_targets() -> None:
    report = build_capability_report(
        discovered_model_keys={
            "Qwen/Qwen3-Embedding-0.6B-GGUF",
            "lmstudio-community/Qwen3-Embedding-4B-GGUF",
            "Qwen3-Embedding-8B-Q4_K_M.gguf",
        },
    )

    assert [item["status"] for item in report["models"]] == [
        "not_tested",
        "not_tested",
        "not_tested",
    ]


def test_report_resolves_variant_keys_for_tested_and_unsupported_evidence() -> None:
    report = build_capability_report(
        discovered_model_keys={
            "Qwen/Qwen3-Embedding-0.6B-GGUF",
            "lmstudio-community/Qwen3-Embedding-4B-GGUF",
            "Qwen3-Embedding-8B-Q4_K_M.gguf",
        },
        tested_dimensions={
            "Qwen/Qwen3-Embedding-0.6B-GGUF": (1024, 256, 256),
        },
        unsupported_model_keys={"lmstudio-community/Qwen3-Embedding-4B-GGUF"},
    )

    by_model = {item["model_id"]: item for item in report["models"]}
    assert by_model["qwen3-embedding-0.6b"]["status"] == "tested"
    assert by_model["qwen3-embedding-4b"]["status"] == "unsupported"
    assert by_model["qwen3-embedding-8b"]["status"] == "not_tested"


def test_report_fails_closed_when_one_catalog_key_matches_multiple_targets() -> None:
    import pytest

    with pytest.raises(ValueError, match="ambiguous"):
        build_capability_report(
            discovered_model_keys={
                "Qwen3-Embedding-0.6B-and-Qwen3-Embedding-4B-GGUF",
            },
        )


def test_report_distinguishes_discovered_from_missing_models_without_live_claims() -> None:
    report = build_capability_report(
        discovered_model_keys={"qwen3-embedding-0.6b", "qwen3-embedding-4b"},
    )

    by_model = {item["model_id"]: item for item in report["models"]}
    assert by_model["qwen3-embedding-0.6b"]["status"] == "not_tested"
    assert by_model["qwen3-embedding-4b"]["status"] == "not_tested"
    assert by_model["qwen3-embedding-8b"]["status"] == "not_installed"
    assert {item["status"] for item in report["models"]} <= {
        "tested",
        "not_installed",
        "unsupported",
        "not_tested",
    }
    assert all(item["mrl_status"] == "unsupported_by_runtime" for item in report["models"])


def test_report_marks_mrl_tested_only_for_explicit_matching_runtime_evidence() -> None:
    report = build_capability_report(
        discovered_model_keys={"qwen3-embedding-0.6b"},
        tested_dimensions={"qwen3-embedding-0.6b": (1024, 256, 256)},
    )

    item = report["models"][0]
    assert item == {
        "model_id": "qwen3-embedding-0.6b",
        "status": "tested",
        "native_dimensions": 1024,
        "requested_dimensions": 256,
        "returned_dimensions": 256,
        "vector_length_valid": True,
        "mrl_status": "tested",
    }
