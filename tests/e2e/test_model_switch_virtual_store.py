"""End-to-end canonical-catalog workflow coverage for model switching."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.loader import load_config, write_config
from mdrack.config.models import PathsConfig
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
        *,
        requested_dimensions: int | None = None,
        dimensions_capability: str = "not_tested",
    ) -> None:
        self.endpoint = endpoint
        self.model_name = model
        self._model_name = model
        self._dimensions = dimensions
        self.timeout = timeout
        self.requested_dimensions = requested_dimensions
        self.dimensions_capability = dimensions_capability

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
    # This is the full-success scenario. The heading-only fixture has no
    # retrieval chunks; partial/failed rebuilds have separate explicit oracles.
    headings = root / "simple_headings.md"
    headings.write_text(headings.read_text(encoding="utf-8") + "\nSynthetic retrieval body.\n", encoding="utf-8")


def _provider_factory(provider_name: str, config: object) -> DeterministicLMStudioProvider:
    del provider_name
    return DeterministicLMStudioProvider(
        endpoint=config.embedding.endpoint,
        model=config.embedding.model,
        dimensions=config.embedding.dimensions,
        timeout=config.embedding.timeout_secs,
    )


def _invoke_json(runner: CliRunner, root: Path, *args: str) -> dict[str, Any]:
    result = runner.invoke(main, ["--root", str(root), *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _read_profile_metadata(db_path: Path) -> dict[str, object]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT s.space_id, s.dimensions, s.fingerprint "
            "FROM core_embedding_spaces s "
            "JOIN core_unit_embeddings e ON e.space_id = s.space_id "
            "GROUP BY s.space_id, s.dimensions, s.fingerprint"
        ).fetchall()
        assert len(rows) == 1
        return dict(rows[0])
    finally:
        conn.close()


def _read_vectors(db_path: Path) -> dict[str, tuple[str, bytes]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT unit_id, space_id, embedding FROM core_unit_embeddings ORDER BY unit_id"
        ).fetchall()
        return {
            row["unit_id"]: (str(row["space_id"]), bytes(row["embedding"]))
            for row in rows
        }
    finally:
        conn.close()


def test_model_switch_refuses_hot_migration_and_allows_a_separate_collection(
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
        "mdrack.cli.commands.model.create_embedding_provider",
        _provider_factory,
    )

    runner = CliRunner()
    init_payload = _invoke_json(runner, root, "init")
    assert init_payload["data"]["status"] == "initialized"

    scan_payload = _invoke_json(runner, root, "scan")
    assert scan_payload["data"]["status"] == "success"
    assert scan_payload["data"]["files_seen"] == len(FIXTURE_FILE_NAMES)
    assert scan_payload["data"]["chunks_created"] > 0

    semantic_before = _invoke_json(runner, root, "search", "Subtitle", "--mode", "semantic", "--limit", "3")
    assert semantic_before["data"]["results"]

    db_path = root / ".virtual-store" / "catalog.sqlite3"
    initial_profile = _read_profile_metadata(db_path)
    initial_vectors = _read_vectors(db_path)
    assert initial_profile["dimensions"] == SMALL_DIMENSIONS
    assert initial_vectors
    assert {space_id for space_id, _vector in initial_vectors.values()} == {initial_profile["space_id"]}

    config_path = root / ".mdrack" / "config.toml"
    original_config_bytes = config_path.read_bytes()
    original_catalog_bytes = db_path.read_bytes()
    for mode in ("embeddings", "full", "none"):
        refused = runner.invoke(
            main, ["--root", str(root), "model", "switch", MODEL_LARGE, "--rebuild", mode, "--yes"],
        )
        assert refused.exit_code == 1, refused.output
        payload = json.loads(refused.output)
        assert payload["ok"] is False
        assert payload["error"]["details"]["reason_code"] == "incompatible_embedding_profile"
        assert control_client.calls == []
        assert config_path.read_bytes() == original_config_bytes
        assert db_path.read_bytes() == original_catalog_bytes
        assert _read_profile_metadata(db_path) == initial_profile
        assert _read_vectors(db_path) == initial_vectors
        assert _invoke_json(runner, root, "search", "Subtitle", "--mode", "semantic", "--limit", "3") == semantic_before

    # A separate empty collection admits the other model without renaming old vectors.
    separate_config = load_config(root=root).model_copy(update={"paths": PathsConfig(store=".separate-store")})
    separate_path = root / "separate.toml"
    write_config(separate_config, separate_path)
    _invoke_json(runner, root, "--config-file", "separate.toml", "init")
    switched = _invoke_json(runner, root, "--config-file", "separate.toml", "model", "switch", MODEL_LARGE)
    assert switched["data"]["new_model"] == MODEL_LARGE
    assert switched["data"]["rebuild"]["status"] == "success"
    assert switched["data"]["rebuild"]["chunks_created"] > 0
    separate_db = root / ".separate-store" / "catalog.sqlite3"
    large_profile = _read_profile_metadata(separate_db)
    large_vectors = _read_vectors(separate_db)
    assert large_profile["dimensions"] == LARGE_DIMENSIONS
    assert large_profile["fingerprint"] != initial_profile["fingerprint"]
    assert large_profile["space_id"] != initial_profile["space_id"]
    assert set(large_vectors) == set(initial_vectors)
    assert all(large_vectors[unit_id] != initial_vectors[unit_id] for unit_id in initial_vectors)
    assert _invoke_json(
        runner, root, "--config-file", "separate.toml", "search", "Subtitle", "--mode", "semantic",
    )["data"]["results"]
    assert config_path.read_bytes() == original_config_bytes
    assert db_path.read_bytes() == original_catalog_bytes
    assert _read_vectors(db_path) == initial_vectors
    assert _invoke_json(runner, root, "scan")["data"]["files_changed"] == 0
    assert _invoke_json(runner, root, "search", "Subtitle", "--mode", "semantic", "--limit", "3") == semantic_before
    assert control_client.calls == [
        ("load_model", MODEL_LARGE),
        ("probe_embedding_dimensions", MODEL_LARGE),
        ("unload_model", "instance-Qwen3-Embedding-0.6B-GGUF"),
    ]
