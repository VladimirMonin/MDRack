"""End-to-end virtual-store workflow coverage for model switching."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.embeddings.lmstudio import EmbeddingHealth, LMStudioLoadResult, LMStudioModelInfo
from mdrack.storage.sqlite.connection import get_connection

MODEL_SMALL = "Qwen/Qwen3-Embedding-0.6B-GGUF"
MODEL_LARGE = "Qwen/Qwen3-Embedding-4B-GGUF"
SMALL_DIMENSIONS = 8
LARGE_DIMENSIONS = 12
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "markdown"
FIXTURE_FILE_NAMES = [
    "simple_headings.md",
    "frontmatter.md",
    "mixed_content.md",
    "code_blocks.md",
]


class DeterministicLMStudioProvider:
    """Deterministic stand-in for LM Studio embeddings keyed by model name."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        dimensions: int,
        timeout: int = 120,
    ) -> None:
        self.endpoint = endpoint
        self.model_name = model
        self._model_name = model
        self._dimensions = dimensions
        self.timeout = timeout

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector(self, text: str) -> list[float]:
        seed = f"{self.model_name}\0{text}".encode("utf-8")
        raw = hashlib.sha256(seed).digest()
        values: list[float] = []

        while len(values) < self._dimensions:
            block = hashlib.sha256(raw + len(values).to_bytes(2, "big")).digest()
            for byte in block:
                values.append((byte / 127.5) - 1.0)
                if len(values) == self._dimensions:
                    break

        norm = sum(value * value for value in values) ** 0.5
        return [round(value / norm, 8) for value in values]

    async def embed(self, texts: list[str], profile: str = "default") -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        return self._vector(text)

    async def health(self) -> EmbeddingHealth:
        return EmbeddingHealth(
            ok=True,
            provider="lmstudio",
            model=self.model_name,
            dimensions=self._dimensions,
        )


class StubModelControlClient:
    """Fake LM Studio control surface with real target model identifiers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.loaded_instance_ids: dict[str, tuple[str, ...]] = {
            MODEL_SMALL: ("instance-Qwen3-Embedding-0.6B-GGUF",),
            MODEL_LARGE: (),
        }

    def list_models(self) -> list[LMStudioModelInfo]:
        return [
            LMStudioModelInfo(
                key=MODEL_SMALL,
                state="downloaded",
                loaded=bool(self.loaded_instance_ids[MODEL_SMALL]),
                instance_ids=self.loaded_instance_ids[MODEL_SMALL],
            ),
            LMStudioModelInfo(
                key=MODEL_LARGE,
                state="downloaded",
                loaded=bool(self.loaded_instance_ids[MODEL_LARGE]),
                instance_ids=self.loaded_instance_ids[MODEL_LARGE],
            ),
        ]

    def load_model(self, model_name: str) -> LMStudioLoadResult:
        self.calls.append(("load_model", model_name))
        instance_id = f"instance-{model_name.rsplit('/', 1)[-1]}"
        self.loaded_instance_ids[model_name] = (instance_id,)
        return LMStudioLoadResult(
            key=model_name,
            state="loaded",
            instance_id=instance_id,
        )

    def unload_model(self, instance_id: str) -> None:
        self.calls.append(("unload_model", instance_id))
        for model_name, loaded_instances in self.loaded_instance_ids.items():
            if instance_id in loaded_instances:
                self.loaded_instance_ids[model_name] = tuple(
                    item for item in loaded_instances if item != instance_id
                )
                break

    def probe_embedding_dimensions(self, model_name: str) -> int:
        self.calls.append(("probe_embedding_dimensions", model_name))
        if model_name == MODEL_SMALL:
            return SMALL_DIMENSIONS
        if model_name == MODEL_LARGE:
            return LARGE_DIMENSIONS
        raise AssertionError(f"Unexpected model probe: {model_name}")


def _write_config(root: Path, store_name: str, model_name: str, dimensions: int) -> Path:
    config_path = root / ".mdrack" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[paths]",
                f'store = "{store_name}"',
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


def _copy_fixtures(root: Path) -> None:
    for file_name in FIXTURE_FILE_NAMES:
        shutil.copy2(FIXTURES_DIR / file_name, root / file_name)


def _provider_factory(provider_name: str, config: object) -> DeterministicLMStudioProvider:
    del provider_name
    return DeterministicLMStudioProvider(
        endpoint=config.embedding.endpoint,
        model=config.embedding.model,
        dimensions=config.embedding.dimensions,
        timeout=config.embedding.timeout_secs,
    )


def _invoke_json(runner: CliRunner, root: Path, *args: str) -> dict[str, object]:
    result = runner.invoke(main, ["--root", str(root), *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _read_profile_metadata(db_path: Path) -> dict[str, object]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT name, model, dimensions, endpoint FROM embedding_profiles WHERE name = ?",
            ("default",),
        ).fetchone()
        assert row is not None
        return dict(row)
    finally:
        conn.close()


def _read_vectors(db_path: Path) -> dict[str, list[float]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT chunk_id, embedding FROM chunk_embeddings WHERE profile_name = ? ORDER BY chunk_id",
            ("default",),
        ).fetchall()
        return {
            row["chunk_id"]: json.loads(row["embedding"])
            for row in rows
        }
    finally:
        conn.close()


def test_model_switch_rebuilds_virtual_store_vectors_end_to_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "virtual-project"
    root.mkdir()
    _copy_fixtures(root)
    _write_config(root, ".virtual-store", MODEL_SMALL, SMALL_DIMENSIONS)

    control_client = StubModelControlClient()
    monkeypatch.setattr(
        "mdrack.cli.commands.scan.create_embedding_provider",
        _provider_factory,
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.search.create_embedding_provider",
        _provider_factory,
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: control_client,
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.model.LMStudioProvider",
        DeterministicLMStudioProvider,
    )

    runner = CliRunner()
    init_payload = _invoke_json(runner, root, "init")
    assert init_payload["data"]["status"] == "initialized"

    scan_payload = _invoke_json(runner, root, "scan")
    assert scan_payload["data"]["files_seen"] == len(FIXTURE_FILE_NAMES)
    assert scan_payload["data"]["chunks_created"] > 0

    semantic_before = _invoke_json(runner, root, "search", "Subtitle", "--mode", "semantic", "--limit", "3")
    assert semantic_before["data"]["results"]

    db_path = root / ".virtual-store" / "knowledge.db"
    initial_profile = _read_profile_metadata(db_path)
    initial_vectors = _read_vectors(db_path)
    assert initial_profile == {
        "name": "default",
        "model": MODEL_SMALL,
        "dimensions": SMALL_DIMENSIONS,
        "endpoint": "http://localhost:1234/v1",
    }
    assert initial_vectors
    assert {len(vector) for vector in initial_vectors.values()} == {SMALL_DIMENSIONS}

    switch_to_large = _invoke_json(runner, root, "model", "switch", MODEL_LARGE)
    assert switch_to_large["data"]["new_model"] == MODEL_LARGE
    assert switch_to_large["data"]["new_dimensions"] == LARGE_DIMENSIONS
    assert switch_to_large["data"]["rebuild"]["performed"] is True
    assert switch_to_large["data"]["rebuild"]["embedded_count"] == len(initial_vectors)
    assert switch_to_large["data"]["unload_previous"] == {
        "attempted": True,
        "model": MODEL_SMALL,
        "status": "unloaded",
        "results": [
            {
                "instance_id": "instance-Qwen3-Embedding-0.6B-GGUF",
                "status": "unloaded",
            }
        ],
    }

    status_large = _invoke_json(runner, root, "status")
    assert status_large["data"]["configured_model"] == MODEL_LARGE
    assert status_large["data"]["configured_dimensions"] == LARGE_DIMENSIONS
    assert status_large["data"]["profile_model"] == MODEL_LARGE
    assert status_large["data"]["profile_dimensions"] == LARGE_DIMENSIONS

    semantic_large = _invoke_json(runner, root, "search", "Subtitle", "--mode", "semantic", "--limit", "3")
    assert semantic_large["data"]["results"]

    large_profile = _read_profile_metadata(db_path)
    large_vectors = _read_vectors(db_path)
    assert large_profile == {
        "name": "default",
        "model": MODEL_LARGE,
        "dimensions": LARGE_DIMENSIONS,
        "endpoint": "http://localhost:1234/v1",
    }
    assert set(large_vectors) == set(initial_vectors)
    assert {len(vector) for vector in large_vectors.values()} == {LARGE_DIMENSIONS}
    assert all(large_vectors[chunk_id] != initial_vectors[chunk_id] for chunk_id in initial_vectors)

    switch_back = _invoke_json(runner, root, "model", "switch", MODEL_SMALL)
    assert switch_back["data"]["new_model"] == MODEL_SMALL
    assert switch_back["data"]["new_dimensions"] == SMALL_DIMENSIONS
    assert switch_back["data"]["rebuild"]["performed"] is True
    assert switch_back["data"]["rebuild"]["embedded_count"] == len(initial_vectors)
    assert switch_back["data"]["load"] == {
        "key": MODEL_SMALL,
        "state": "loaded",
        "instance_id": "instance-Qwen3-Embedding-0.6B-GGUF",
    }
    assert switch_back["data"]["unload_previous"] == {
        "attempted": True,
        "model": MODEL_LARGE,
        "status": "unloaded",
        "results": [
            {
                "instance_id": "instance-Qwen3-Embedding-4B-GGUF",
                "status": "unloaded",
            }
        ],
    }

    status_small = _invoke_json(runner, root, "status")
    assert status_small["data"]["configured_model"] == MODEL_SMALL
    assert status_small["data"]["configured_dimensions"] == SMALL_DIMENSIONS
    assert status_small["data"]["profile_model"] == MODEL_SMALL
    assert status_small["data"]["profile_dimensions"] == SMALL_DIMENSIONS

    semantic_after = _invoke_json(runner, root, "search", "Subtitle", "--mode", "semantic", "--limit", "3")
    assert semantic_after["data"]["results"]

    final_profile = _read_profile_metadata(db_path)
    final_vectors = _read_vectors(db_path)
    assert final_profile == initial_profile
    assert final_vectors == initial_vectors
    assert control_client.calls == [
        ("load_model", MODEL_LARGE),
        ("probe_embedding_dimensions", MODEL_LARGE),
        ("unload_model", "instance-Qwen3-Embedding-0.6B-GGUF"),
        ("load_model", MODEL_SMALL),
        ("probe_embedding_dimensions", MODEL_SMALL),
        ("unload_model", "instance-Qwen3-Embedding-4B-GGUF"),
    ]
