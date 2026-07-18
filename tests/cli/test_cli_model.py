"""Tests for model management CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import toml
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.embeddings.lmstudio import LMStudioLoadResult, LMStudioModelInfo
from mdrack.output.errors import EmbeddingError

MODEL_SMALL = "Qwen/Qwen3-Embedding-0.6B-GGUF"
MODEL_LARGE = "Qwen/Qwen3-Embedding-4B-GGUF"
SMALL_DIMENSIONS = 8
LARGE_DIMENSIONS = 12


class StubModelControlClient:
    """Patchable stub for model command surface tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.list_result: list[dict[str, Any]] = [
            {"id": "Qwen/Qwen3-Embedding-0.6B-GGUF", "state": "downloaded"}
        ]
        self.loaded_result: dict[str, Any] = {
            "models": [
                {
                    "instance_id": "inst-001",
                    "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
                    "state": "loaded",
                }
            ]
        }
        self.download_result: dict[str, Any] = {
            "model": "Qwen/Qwen3-Embedding-4B-GGUF",
            "status": "queued",
        }
        self.download_status_result: list[dict[str, Any]] = [
            {
                "model": "Qwen/Qwen3-Embedding-4B-GGUF",
                "status": "downloading",
                "progress": 42,
            }
        ]
        self.load_result: dict[str, Any] = {
            "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
            "instance_id": "inst-001",
            "status": "loaded",
        }
        self.unload_result: dict[str, Any] = {
            "instance_id": "inst-001",
            "status": "unloaded",
        }

    def list_models(self) -> list[dict[str, Any]]:
        self.calls.append(("list_models", ()))
        return self.list_result

    def loaded_models(self) -> dict[str, Any]:
        self.calls.append(("loaded_models", ()))
        return self.loaded_result

    def download_model(self, model_name: str) -> dict[str, Any]:
        self.calls.append(("download_model", (model_name,)))
        return self.download_result

    def get_download_status(self) -> list[dict[str, Any]]:
        self.calls.append(("get_download_status", ()))
        return self.download_status_result

    def load_model(self, model_name: str) -> dict[str, Any]:
        self.calls.append(("load_model", (model_name,)))
        return self.load_result

    def unload_model(self, instance_id: str) -> dict[str, Any]:
        self.calls.append(("unload_model", (instance_id,)))
        return self.unload_result


class StubModelSwitchControlClient:
    """Minimal switch client with deterministic model metadata."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def list_models(self) -> list[LMStudioModelInfo]:
        self.calls.append(("list_models", ()))
        return [
            LMStudioModelInfo(
                key=MODEL_SMALL,
                state="downloaded",
                loaded=True,
                instance_ids=("instance-small",),
            ),
            LMStudioModelInfo(key=MODEL_LARGE, state="downloaded", loaded=False),
        ]

    def load_model(self, model_name: str) -> LMStudioLoadResult:
        self.calls.append(("load_model", (model_name,)))
        return LMStudioLoadResult(
            key=model_name,
            state="loaded",
            instance_id=f"instance-{model_name.rsplit('/', 1)[-1]}",
        )

    def unload_model(self, instance_id: str) -> None:
        self.calls.append(("unload_model", (instance_id,)))

    def probe_embedding_dimensions(self, model_name: str) -> int:
        self.calls.append(("probe_embedding_dimensions", (model_name,)))
        if model_name == MODEL_SMALL:
            return SMALL_DIMENSIONS
        if model_name == MODEL_LARGE:
            return LARGE_DIMENSIONS
        raise AssertionError(f"Unexpected model probe: {model_name}")


class AliasModelSwitchControlClient(StubModelSwitchControlClient):
    """Switch client that exposes LM Studio keys different from user aliases."""

    LARGE_KEY = "text-embedding-qwen3-embedding-4b"
    SMALL_KEY = "text-embedding-qwen3-embedding-0.6b"

    def list_models(self) -> list[LMStudioModelInfo]:
        self.calls.append(("list_models", ()))
        return [
            LMStudioModelInfo(
                key=self.SMALL_KEY,
                display_name="Qwen3 Embedding 0.6B",
                state="downloaded",
                loaded=True,
                instance_ids=("instance-small",),
            ),
            LMStudioModelInfo(
                key=self.LARGE_KEY,
                display_name="Qwen3 Embedding 4B",
                state="downloaded",
                loaded=False,
            ),
        ]

    def load_model(self, model_name: str) -> LMStudioLoadResult:
        self.calls.append(("load_model", (model_name,)))
        return LMStudioLoadResult(
            key=model_name,
            state="loaded",
            instance_id=f"instance-{model_name.rsplit('/', 1)[-1]}",
        )

    def probe_embedding_dimensions(self, model_name: str) -> int:
        self.calls.append(("probe_embedding_dimensions", (model_name,)))
        if model_name == self.SMALL_KEY:
            return SMALL_DIMENSIONS
        if model_name == self.LARGE_KEY:
            return LARGE_DIMENSIONS
        raise AssertionError(f"Unexpected model probe: {model_name}")


class AlreadyLoadedControlClient(StubModelControlClient):
    """Client that reports the target model as already loaded."""

    def list_models(self) -> list[LMStudioModelInfo]:
        self.calls.append(("list_models", ()))
        return [
            LMStudioModelInfo(
                key="text-embedding-qwen3-embedding-0.6b",
                display_name="Qwen3 Embedding 0.6B",
                state="loaded",
                loaded=True,
                instance_ids=("inst-existing",),
            )
        ]


def _write_config(root: Path, model_name: str, dimensions: int) -> Path:
    config_path = root / ".mdrack" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[paths]",
                'store = ".mdrack"',
                "",
                "[embedding]",
                'provider = "lmstudio"',
                f'model = "{model_name}"',
                'endpoint = "http://localhost:1234/v1"',
                f"dimensions = {dimensions}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_model_group_help_lists_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["model", "--help"])

    assert result.exit_code == 0
    assert "list" in result.output
    assert "loaded" in result.output
    assert "download" in result.output
    assert "download-status" in result.output
    assert "load" in result.output
    assert "unload" in result.output


@pytest.mark.parametrize(
    ("args", "expected_calls", "expected_command", "expected_data"),
    [
        (
            ["model", "list"],
            [("list_models", ())],
            "model list",
            {
                "models": [
                    {
                        "id": "Qwen/Qwen3-Embedding-0.6B-GGUF",
                        "state": "downloaded",
                    }
                ]
            },
        ),
        (
            ["model", "loaded"],
            [("loaded_models", ())],
            "model loaded",
            {
                "models": [
                    {
                        "instance_id": "inst-001",
                        "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
                        "state": "loaded",
                    }
                ]
            },
        ),
        (
            ["model", "download", "Qwen/Qwen3-Embedding-4B-GGUF"],
            [
                ("list_models", ()),
                ("download_model", ("Qwen/Qwen3-Embedding-4B-GGUF",)),
            ],
            "model download",
            {
                "model": "Qwen/Qwen3-Embedding-4B-GGUF",
                "status": "queued",
            },
        ),
        (
            ["model", "download-status"],
            [("get_download_status", ())],
            "model download-status",
            {
                "downloads": [
                    {
                        "model": "Qwen/Qwen3-Embedding-4B-GGUF",
                        "status": "downloading",
                        "progress": 42,
                    }
                ]
            },
        ),
        (
            ["model", "load", "Qwen/Qwen3-Embedding-0.6B-GGUF"],
            [
                ("list_models", ()),
                ("loaded_models", ()),
                ("load_model", ("Qwen/Qwen3-Embedding-0.6B-GGUF",)),
            ],
            "model load",
            {
                "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
                "instance_id": "inst-001",
                "status": "loaded",
            },
        ),
        (
            ["model", "unload", "inst-001"],
            [("unload_model", ("inst-001",))],
            "model unload",
            {
                "instance_id": "inst-001",
                "status": "unloaded",
            },
        ),
    ],
)
def test_model_commands_return_json_using_stubbed_control_calls(
    args: list[str],
    expected_calls: list[tuple[str, tuple[Any, ...]]],
    expected_command: str,
    expected_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StubModelControlClient()
    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: client,
    )

    runner = CliRunner()
    result = runner.invoke(main, args)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"] == expected_data
    assert payload["meta"]["command"] == expected_command
    assert client.calls == expected_calls


def test_model_command_returns_json_error_on_control_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingModelControlClient(StubModelControlClient):
        def list_models(self) -> list[dict[str, Any]]:
            self.calls.append(("list_models", ()))
            raise EmbeddingError("LM Studio is unavailable")

    client = FailingModelControlClient()
    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: client,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["model", "list"])

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EMBEDDING_ERROR"
    assert payload["error"] == {
        "code": "EMBEDDING_ERROR",
        "message": "LM Studio operation failed",
        "details": {"reason_code": "operation_failed"},
    }
    assert payload["meta"]["command"] == "model list"
    assert client.calls == [("list_models", ())]


def test_model_load_reuses_existing_loaded_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AlreadyLoadedControlClient()
    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: client,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["model", "load", MODEL_SMALL])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"] == {
        "key": "text-embedding-qwen3-embedding-0.6b",
        "state": "already_loaded",
        "instance_id": "inst-existing",
    }
    assert client.calls == [("list_models", ())]


def test_model_switch_persists_config_after_successful_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, MODEL_SMALL, SMALL_DIMENSIONS)
    client = StubModelSwitchControlClient()
    captured: dict[str, Any] = {}

    def fake_rebuild(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "performed": True,
            "mode": kwargs["rebuild_mode"],
            "embedded_count": 3,
            "total_chunks": 3,
            "profile": "default",
        }

    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: client,
    )
    monkeypatch.setattr("mdrack.cli.commands.model._run_switch_rebuild", fake_rebuild)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "model", "switch", MODEL_LARGE])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["old_model"] == MODEL_SMALL
    assert payload["data"]["new_model"] == MODEL_LARGE
    assert payload["data"]["old_dimensions"] == SMALL_DIMENSIONS
    assert payload["data"]["new_dimensions"] == LARGE_DIMENSIONS
    assert payload["data"]["rebuild"] == {
        "performed": True,
        "mode": "embeddings",
        "embedded_count": 3,
        "total_chunks": 3,
        "profile": "default",
    }
    assert "config_path" not in payload["data"]
    assert str(config_path) not in result.output
    assert payload["data"]["load"] == {
        "key": MODEL_LARGE,
        "state": "loaded",
        "instance_id": "instance-Qwen3-Embedding-4B-GGUF",
    }
    assert payload["data"]["unload_previous"] == {
        "attempted": True,
        "model": MODEL_SMALL,
        "status": "unloaded",
        "results": [
            {
                "instance_id": "instance-small",
                "status": "unloaded",
            }
        ],
    }
    assert payload["meta"]["command"] == "model switch"
    assert client.calls == [
        ("list_models", ()),
        ("load_model", (MODEL_LARGE,)),
        ("probe_embedding_dimensions", (MODEL_LARGE,)),
        ("unload_model", ("instance-small",)),
    ]

    assert captured["model_name"] == MODEL_LARGE
    assert captured["dimensions"] == LARGE_DIMENSIONS
    assert captured["rebuild_mode"] == "embeddings"
    assert captured["config"].embedding.model == MODEL_SMALL
    assert captured["switched_config"].embedding.model == MODEL_LARGE
    assert captured["switched_config"].embedding.dimensions == LARGE_DIMENSIONS

    persisted = toml.load(config_path)
    assert persisted["embedding"]["model"] == MODEL_LARGE
    assert persisted["embedding"]["dimensions"] == LARGE_DIMENSIONS


def test_model_switch_rolls_back_config_when_rebuild_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, MODEL_SMALL, SMALL_DIMENSIONS)
    original_text = config_path.read_text(encoding="utf-8")
    client = StubModelSwitchControlClient()

    def failing_rebuild(**kwargs: Any) -> dict[str, Any]:
        raise EmbeddingError("Embedding rebuild failed")

    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: client,
    )
    monkeypatch.setattr("mdrack.cli.commands.model._run_switch_rebuild", failing_rebuild)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "model", "switch", MODEL_LARGE])

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EMBEDDING_ERROR"
    assert payload["error"]["message"] == "LM Studio operation failed"
    assert payload["meta"]["command"] == "model switch"
    assert client.calls == [
        ("list_models", ()),
        ("load_model", (MODEL_LARGE,)),
        ("probe_embedding_dimensions", (MODEL_LARGE,)),
    ]

    assert config_path.read_text(encoding="utf-8") == original_text
    persisted = toml.load(config_path)
    assert persisted["embedding"]["model"] == MODEL_SMALL
    assert persisted["embedding"]["dimensions"] == SMALL_DIMENSIONS


def test_model_switch_resolves_user_alias_to_lmstudio_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_config(tmp_path, MODEL_SMALL, SMALL_DIMENSIONS)
    client = AliasModelSwitchControlClient()
    captured: dict[str, Any] = {}

    def fake_rebuild(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "performed": True,
            "mode": kwargs["rebuild_mode"],
            "embedded_count": 1,
            "total_chunks": 1,
            "profile": "default",
        }

    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: client,
    )
    monkeypatch.setattr("mdrack.cli.commands.model._run_switch_rebuild", fake_rebuild)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "model", "switch", MODEL_LARGE])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["requested_model"] == MODEL_LARGE
    assert payload["data"]["new_model"] == AliasModelSwitchControlClient.LARGE_KEY
    assert payload["data"]["unload_previous"] == {
        "attempted": True,
        "model": AliasModelSwitchControlClient.SMALL_KEY,
        "status": "unloaded",
        "results": [
            {
                "instance_id": "instance-small",
                "status": "unloaded",
            }
        ],
    }
    assert client.calls == [
        ("list_models", ()),
        ("load_model", (AliasModelSwitchControlClient.LARGE_KEY,)),
        ("probe_embedding_dimensions", (AliasModelSwitchControlClient.LARGE_KEY,)),
        ("unload_model", ("instance-small",)),
    ]
    assert captured["model_name"] == AliasModelSwitchControlClient.LARGE_KEY


def test_model_switch_skips_unload_when_switching_to_same_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_config(tmp_path, MODEL_SMALL, SMALL_DIMENSIONS)
    client = StubModelSwitchControlClient()

    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: client,
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.model._run_switch_rebuild",
        lambda **kwargs: {"performed": True, "mode": kwargs["rebuild_mode"]},
    )

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "model", "switch", MODEL_SMALL])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["unload_previous"] == {
        "attempted": False,
        "model": MODEL_SMALL,
        "reason": "same_model",
    }
    assert client.calls == [
        ("list_models", ()),
        ("probe_embedding_dimensions", (MODEL_SMALL,)),
    ]
