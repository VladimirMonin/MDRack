"""Offline tests for evidence-based LM Studio capability reporting."""

from __future__ import annotations

import json
import socket
import sys

import pytest

from scripts.live_lmstudio_eval import _run_live_matrix, build_capability_report
from scripts.live_lmstudio_eval import main as live_eval_main

pytestmark = [pytest.mark.unit, pytest.mark.no_live_default]


def test_live_evaluator_requires_confirmation_before_any_live_stage() -> None:
    original_argv = sys.argv
    sys.argv = ["live_lmstudio_eval.py"]
    try:
        assert live_eval_main() == 2
    finally:
        sys.argv = original_argv


def test_live_evaluator_default_response_is_provider_free(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("default live evaluator path attempted network access")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    original_argv = sys.argv
    sys.argv = ["live_lmstudio_eval.py"]
    try:
        assert live_eval_main() == 2
    finally:
        sys.argv = original_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"calls_attempted": 0, "status": "live_confirmation_required"}


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


@pytest.mark.asyncio
async def test_live_matrix_requires_a_separate_load_permission_and_never_exposes_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mdrack.integrations.lmstudio.client import LMStudioModelInfo

    class UnloadedClient:
        async def list_models(self) -> list[LMStudioModelInfo]:
            return [LMStudioModelInfo(key="safe-model", state="idle", loaded=False)]

        async def close(self) -> None:
            return None

    monkeypatch.setattr("scripts.live_lmstudio_eval.LMStudioControlClient", lambda endpoint: UnloadedClient())

    report = await _run_live_matrix(
        endpoint="http://HOST_SENTINEL:43123/private-api",
        model="safe-model",
        requested_dimensions=(1024, 512),
        allow_model_load=False,
        config_source="test",
    )

    assert report["status"] == "blocked_runtime_capability"
    assert report["reason_code"] == "model_not_loaded"
    assert report["calls_attempted"] == 1
    assert "HOST_SENTINEL" not in json.dumps(report)


@pytest.mark.asyncio
async def test_live_matrix_records_requested_and_returned_dimensions_and_unloads_own_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mdrack.integrations.lmstudio.client import LMStudioLoadResult, LMStudioModelInfo

    class ControlledClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object | None]] = []

        async def list_models(self) -> list[LMStudioModelInfo]:
            self.calls.append(("list", None))
            return [LMStudioModelInfo(key="safe-model", state="idle", loaded=False)]

        async def load_model(self, model: str) -> LMStudioLoadResult:
            self.calls.append(("load", model))
            return LMStudioLoadResult(key=model, state="loaded", instance_id="runner-instance")

        async def probe_embedding_dimensions(self, model: str, *, dimensions: int | None = None) -> int:
            self.calls.append(("probe", dimensions))
            assert model == "safe-model"
            return 1024 if dimensions is None else dimensions

        async def unload_model(self, instance_id: str) -> None:
            self.calls.append(("unload", instance_id))

        async def close(self) -> None:
            self.calls.append(("close", None))

    client = ControlledClient()
    monkeypatch.setattr("scripts.live_lmstudio_eval.LMStudioControlClient", lambda endpoint: client)

    report = await _run_live_matrix(
        endpoint="http://HOST_SENTINEL:43123/private-api",
        model="safe-model",
        requested_dimensions=(1024, 512),
        allow_model_load=True,
        config_source="test",
    )

    assert report["status"] == "completed"
    assert report["calls_attempted"] == 7
    assert report["native_dimensions"] == 1024
    assert report["all_reduced_dimensions_supported"] is True
    assert report["cleanup"] == {
        "model_loaded_by_runner": True,
        "unload_status": "unloaded",
        "loaded_models_after_cleanup": 0,
    }
    assert client.calls == [
        ("list", None),
        ("load", "safe-model"),
        ("probe", None),
        ("probe", 1024),
        ("probe", 512),
        ("unload", "runner-instance"),
        ("list", None),
        ("close", None),
    ]
    assert "HOST_SENTINEL" not in json.dumps(report)
