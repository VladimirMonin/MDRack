"""Declared storage metadata capability agrees with typing and useful real scans."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.application.compatibility import CoreCompatibilityStorage
from mdrack.application.indexing import IndexingService
from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.domain.indexing import PreparedFile, SourceLocator
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.ports.storage import IndexStorage, KnowledgeStorage, LegacyTextEmbeddingBinding
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack.storage.sqlite.vector import VectorIndex
from mdrack_core import EmbeddingSpaceRecord


class ReplacementStorage:
    def __init__(self) -> None:
        self.prepared: list[PreparedFile] = []
        self.started = 0
        self.closed = False

    def read_text_embedding_spaces(self, profile_name: str) -> tuple[EmbeddingSpaceRecord, ...]:
        return tuple(
            CanonicalTextEmbeddingSpace(prepared.embedding_profile).record
            for prepared in self.prepared if prepared.embedding_profile is not None
        )

    def start_run(self, *, parser_name: str, parser_version: str,
                  chunk_strategy_name: str, chunk_strategy_version: str) -> str:
        self.started += 1
        return "synthetic-run"

    def plan_changes(self, scanned: list[Path], root: Path) -> Any:
        return SimpleNamespace(new_files=scanned, changed_files=[], unchanged_files=[], deleted_files=[])

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        for prepared in self.prepared:
            if prepared.relative_path == relative_path:
                return {"id": prepared.record_id, "logical_id": prepared.logical_id}
        return None

    def replace_file(self, prepared: PreparedFile) -> None:
        self.prepared = [item for item in self.prepared if item.relative_path != prepared.relative_path] + [prepared]

    def delete_file(self, relative_path: str) -> None:
        raise AssertionError("unexpected deletion")

    def record_error(self, run_id: str, code: str, *, file_ref: str) -> None:
        raise AssertionError("unexpected indexing failure")

    def finish_run(self, run_id: str, *, status: str, stats: dict[str, int], error_codes: Sequence[str]) -> None:
        assert status == "success"

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return SourceLocator("default", "note.md", 1, 1, (), "block", chunk_id)

    def close(self) -> None:
        self.closed = True


def _supported_types(canonical: CoreCompatibilityStorage, legacy: SQLiteIndexStorage,
                     injected: ReplacementStorage, knowledge: KnowledgeStorage) -> tuple[IndexStorage, ...]:
    # No casts/ignores: the declared real implementations and public facade
    # injection contract must all provide the same mandatory read capability.
    return canonical, legacy, injected, knowledge


def test_index_storage_declares_mandatory_metadata_read() -> None:
    assert "read_text_embedding_spaces" in IndexStorage.__dict__, "runtime requirement is absent from the port"


@pytest.mark.parametrize("embeddings", [False, True])
def test_declared_replacement_storage_performs_useful_scan_and_refuses_incompatible_profile(
    tmp_path: Path, embeddings: bool,
) -> None:
    (tmp_path / "note.md").write_text("# Stored\n\nDurable synthetic body.", encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(store="must-not-open"))
    replacement = ReplacementStorage()
    storage: IndexStorage = replacement
    provider = FakeEmbeddingProvider(dimensions=4) if embeddings else None
    service = IndexingService(tmp_path, config, storage, provider=provider)
    try:
        result = service.scan()
        assert result.status == "success" and result.files_indexed == 1
        assert replacement.prepared[0].chunks
        assert not (tmp_path / "must-not-open").exists()
        if embeddings:
            before = tuple(replacement.prepared)
            changed = config.model_copy(update={
                "embedding": config.embedding.model_copy(update={"instruction_profile": "other-recipe"}),
            })
            with pytest.raises(IncompatibleEmbeddingProfileError):
                IndexingService(tmp_path, changed, storage, provider=provider).scan(force_reindex=True)
            assert replacement.started == 1
            assert tuple(replacement.prepared) == before
        assert service.scan(force_reindex=True).status == "success"
        assert len(replacement.prepared) == 1
    finally:
        service.close()
    assert replacement.closed


@pytest.mark.parametrize("declare_capability", [False, True])
def test_strict_typing_rejects_only_the_old_incomplete_storage_contract(
    tmp_path: Path, declare_capability: bool,
) -> None:
    source = inspect.getsource(ReplacementStorage)
    if not declare_capability:
        tree = ast.parse(source)
        declaration = tree.body[0]
        assert isinstance(declaration, ast.ClassDef)
        method = next(node for node in declaration.body
                      if isinstance(node, ast.FunctionDef) and node.name == "read_text_embedding_spaces")
        lines = source.splitlines(keepends=True)
        assert method.end_lineno is not None
        source = "".join(lines[:method.lineno - 1] + lines[method.end_lineno:])
    prelude = """from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
from mdrack.domain.indexing import PreparedFile, SourceLocator
from mdrack.ports.storage import IndexStorage
from mdrack_core import EmbeddingSpaceRecord
"""
    probe = tmp_path / "storage_specimen.py"
    probe.write_text(prelude + source + "\nstorage: IndexStorage = ReplacementStorage()\n", encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    argv = [
        sys.executable, "-m", "mypy", "src/mdrack", "packages/mdrack-core/src/mdrack_core",
        "packages/mdrack-media/src/mdrack_media", "packages/mdrack-sqlite/src/mdrack_sqlite", str(probe),
        "--strict", "--no-incremental", "--show-error-codes", "--cache-dir", str(tmp_path / "mypy-cache"),
    ]
    result = subprocess.run(argv, cwd=repo, text=True, capture_output=True, timeout=120)
    (tmp_path / "mypy.stdout.log").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "mypy.stderr.log").write_text(result.stderr, encoding="utf-8")
    (tmp_path / "mypy.receipt.json").write_text(json.dumps({
        "argv": argv, "cwd": str(repo), "exit_code": result.returncode, "awaited": True,
        "intentional_static_rejection": not declare_capability,
        "specimen_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
    }, indent=2), encoding="utf-8")
    if declare_capability:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1, "the undeclared mandatory runtime capability is still accepted statically"
        assert "read_text_embedding_spaces" in result.stdout
        assert "IndexStorage" in result.stdout and "Found 1 error" in result.stdout


@pytest.mark.parametrize("policy", [None, "ieee754-f32-canonical-v1"])
def test_declared_legacy_reader_preserves_real_identity_and_unknown_refusal(tmp_path: Path, policy: str | None) -> None:
    config = MDRackConfig()
    config = config.model_copy(update={
        "embedding": config.embedding.model_copy(update={"vector_value_policy": policy}),
    })
    provider = FakeEmbeddingProvider(dimensions=4)
    profile = embedding_profile_from_config(config, provider)
    (tmp_path / "note.md").write_text("# Stored\n\nDurable synthetic body.", encoding="utf-8")
    connection = get_connection(tmp_path / "legacy.sqlite3")
    try:
        apply_migrations(connection, get_migrations_dir())
        adapter = SQLiteIndexStorage(connection)
        storage: IndexStorage = adapter
        assert IndexingService(tmp_path, config, storage, provider=provider).scan().status == "success"
        bindings = storage.read_text_embedding_spaces("default")
        assert bindings and all(isinstance(binding, LegacyTextEmbeddingBinding) for binding in bindings)
        assert all(binding.profile_fingerprint == profile.fingerprint for binding in adapter.read_text_embedding_spaces(
            "default",
        ))
        query = asyncio.run(provider.embed_query("Durable"))
        assert VectorIndex(connection).search(query, "default", profile_fingerprint=profile.fingerprint)
        # Legacy NULL fingerprints are real unknown identities, not canonical
        # spaces manufactured by the compatibility layer or an empty store.
        connection.execute("UPDATE embedding_profiles SET fingerprint=NULL")
        connection.execute("UPDATE chunk_embeddings SET profile_fingerprint=NULL")
        connection.commit()
        before = tuple(connection.iterdump())
        unknown = adapter.read_text_embedding_spaces("default")
        assert unknown and all(binding.profile_fingerprint is None for binding in unknown)
        with pytest.raises(IncompatibleEmbeddingProfileError):
            IndexingService(tmp_path, config, storage, provider=provider).scan(force_reindex=True)
        assert tuple(connection.iterdump()) == before
    finally:
        connection.close()
