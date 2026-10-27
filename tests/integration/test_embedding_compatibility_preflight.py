"""Fail-closed text-profile admission using real synthetic catalogs and scan paths."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.application.compatibility import create_application_storage
from mdrack.application.indexing import IndexingService
from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
from mdrack.config.loader import write_config
from mdrack.config.models import EmbeddingConfig, MDRackConfig, PathsConfig
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.indexing.indexer import run_indexer
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack_core import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)


class RecordingProvider(FakeEmbeddingProvider):
    """Only the external embedding edge is synthetic; all application/storage is real."""

    def __init__(self, config: MDRackConfig) -> None:
        super().__init__(config.embedding.dimensions, provider_name="lmstudio")
        self._model_name = config.embedding.model
        self.calls: list[str] = []

    async def embed(self, texts, profile="default"):
        self.calls.append("embed")
        return await super().embed(texts, profile)

    async def embed_query(self, text, profile="default"):
        self.calls.append("embed_query")
        return await super().embed_query(text, profile)


def config_for(**updates: Any) -> MDRackConfig:
    return MDRackConfig(embedding=EmbeddingConfig(model="synthetic-text-a", dimensions=2, **updates))


def seed(root: Path, config: MDRackConfig) -> None:
    (root / "kept.md").write_text("# Kept\n\nRollback searchable evidence.\n", encoding="utf-8")
    assert run_indexer(root, config, provider=RecordingProvider(config)).status == "success"
    write_config(config, root / ".mdrack" / "config.toml")


def catalog_snapshot(root: Path, config: MDRackConfig) -> tuple[bytes, tuple[str, ...]]:
    with open_application_catalog_readonly(root, config) as catalog:
        rows = tuple(catalog.connection.iterdump())
    return (root / config.paths.store / "catalog.sqlite3").read_bytes(), rows


def search_after_reopen(root: Path, config: MDRackConfig) -> tuple[Any, Any]:
    with MDRackEngine(root=root, config=config, embedding_provider=RecordingProvider(config)) as engine:
        text = engine.search_text("Rollback", limit=1)
        semantic = asyncio.run(engine.search_semantic("Rollback", limit=1))
        assert text.results and semantic.results
        assert not semantic.degraded
        return text, semantic


def add_unrelated_space(root: Path, config: MDRackConfig, *, modality: str = "image") -> None:
    """A separately typed native space is not the app's TEXT-profile slot."""
    storage = create_application_storage(root, config, create=True)
    try:
        storage.resource_store.replace_resource(
            PreparedResourceBatch(
                ResourceRecord("native-image", "image", "image/png", "synthetic", Locator("whole_image", {})),
                (RepresentationRecord("native-rep", "native-image", "native", modality),),
                (SearchUnitRecord(
                    "native-unit", "native-image", "native-rep", "whole_resource", modality,
                    None, Locator("whole_image", {}), 0,
                ),),
                (EmbeddingSpaceRecord("native-space", 3, "cosine", "native-fingerprint", {}),),
                (VectorRecord("native-unit", "native-space", (1.0, 0.0, 0.0)),),
            )
        )
    finally:
        storage.close()


@pytest.mark.parametrize("entrypoint", ["service", "engine", "wrapper"])
@pytest.mark.parametrize("force", [False, True])
def test_incompatible_scan_refuses_before_dispatch_traversal_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str, force: bool,
) -> None:
    original = config_for()
    seed(tmp_path, original)
    add_unrelated_space(tmp_path, original)
    before_search = search_after_reopen(tmp_path, original)
    changed = original.model_copy(update={
        "embedding": original.embedding.model_copy(update={"instruction_profile": "synthetic-recipe-b"}),
    })
    # A manually edited config must remain byte-exact on refusal (not silently repaired).
    config_path = tmp_path / ".mdrack" / "config.toml"
    write_config(changed, config_path)
    before_config = config_path.read_bytes()
    before = catalog_snapshot(tmp_path, original)
    (tmp_path / "kept.md").unlink()  # An admitted scan would delete useful old work.
    (tmp_path / "new.md").write_text("# New\n\nOther source.\n", encoding="utf-8")
    provider = RecordingProvider(changed)
    traversals: list[object] = []
    walk = os.walk

    def observe_walk(top, *args, **kwargs):
        traversals.append(top)
        return walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", observe_walk)
    if entrypoint == "service":
        service = IndexingService(tmp_path, changed, create_application_storage(tmp_path, changed), provider=provider)
        try:
            with pytest.raises(IncompatibleEmbeddingProfileError):
                service.scan(force_reindex=force)
        finally:
            service.close()
    elif entrypoint == "engine":
        with MDRackEngine(root=tmp_path, config=changed, embedding_provider=provider) as engine:
            with pytest.raises(IncompatibleEmbeddingProfileError):
                engine.scan(force_reindex=force)
    else:
        with pytest.raises(IncompatibleEmbeddingProfileError):
            run_indexer(tmp_path, changed, provider=provider, force_reindex=force)
    assert provider.calls == []
    assert traversals == []
    assert config_path.read_bytes() == before_config
    assert catalog_snapshot(tmp_path, original) == before
    assert search_after_reopen(tmp_path, original) == before_search
    # The next compatible operation is useful, not an empty-success recovery.
    (tmp_path / "kept.md").write_text("# Kept\n\nRollback searchable evidence.\n", encoding="utf-8")
    write_config(original, config_path)
    assert run_indexer(tmp_path, original, provider=RecordingProvider(original)).files_indexed == 1
    assert search_after_reopen(tmp_path, original)[0].results


@pytest.mark.parametrize("field,value", [
    ("model", "synthetic-text-b"), ("dimensions", 3), ("runtime", "other-runtime"),
    ("model_family", "other-family"), ("quantization", "other-quantization"),
    ("query_instruction", "Other query instruction"), ("normalization_mode", "other-normalization"),
    ("endpoint_family", "other-endpoint-family"), ("profile_schema_version", 2),
    ("vector_value_policy", None),
])
def test_full_identity_not_model_name_or_dimension_alone(tmp_path: Path, field: str, value: object) -> None:
    original = config_for()
    seed(tmp_path, original)
    before = catalog_snapshot(tmp_path, original)
    changed = original.model_copy(update={"embedding": original.embedding.model_copy(update={field: value})})
    provider = RecordingProvider(changed)
    with pytest.raises(IncompatibleEmbeddingProfileError):
        run_indexer(tmp_path, changed, provider=provider, force_reindex=True)
    assert provider.calls == []
    assert catalog_snapshot(tmp_path, original) == before


@pytest.mark.parametrize("legacy_policy", [False, True])
@pytest.mark.parametrize("modality", ["image", "text"])
def test_compatible_scan_and_force_rebuild_allow_unrelated_typed_spaces(
    tmp_path: Path, legacy_policy: bool, modality: str,
) -> None:
    config = config_for(vector_value_policy=None) if legacy_policy else config_for()
    seed(tmp_path, config)
    add_unrelated_space(tmp_path, config, modality=modality)
    expected = search_after_reopen(tmp_path, config)
    provider = RecordingProvider(config)
    assert run_indexer(tmp_path, config, provider=provider).status == "success"
    assert provider.calls == []
    assert run_indexer(tmp_path, config, provider=provider, force_reindex=True).status == "success"
    assert provider.calls == ["embed"]
    assert search_after_reopen(tmp_path, config) == expected
    with open_application_catalog_readonly(tmp_path, config) as catalog:
        assert catalog.read_vector("native-unit", "native-space") is not None


@pytest.mark.parametrize("existing", ["empty", "visual-only", "new-collection"])
def test_unbound_empty_text_or_separate_collection_is_allowed(tmp_path: Path, existing: str) -> None:
    original = config_for()
    if existing == "new-collection":
        seed(tmp_path, original)
        before = catalog_snapshot(tmp_path, original)
        config = original.model_copy(update={
            "paths": PathsConfig(store="separate-collection"),
            "embedding": original.embedding.model_copy(update={"model": "synthetic-text-b"}),
        })
    else:
        create_application_storage(tmp_path, original, create=True).close()
        if existing == "visual-only":
            add_unrelated_space(tmp_path, original)
        config = original.model_copy(update={"embedding": original.embedding.model_copy(update={"model": "text-b"})})
    (tmp_path / "kept.md").write_text("# Kept\n\nRollback searchable evidence.\n", encoding="utf-8")
    assert run_indexer(tmp_path, config, provider=RecordingProvider(config)).status == "success"
    assert search_after_reopen(tmp_path, config)[0].results
    if existing == "new-collection":
        assert catalog_snapshot(tmp_path, original) == before


@pytest.mark.parametrize("damage", [
    "missing-profile", "foreign-fingerprint", "wrong-policy", "wrong-metric", "foreign-space-id",
])
def test_unknown_or_malformed_active_text_identity_is_not_compatibility(
    tmp_path: Path, damage: str,
) -> None:
    config = config_for()
    seed(tmp_path, config)
    storage = create_application_storage(tmp_path, config)
    try:
        record = storage.get_file_by_path("kept.md")
        batch = storage.resource_store.read_snapshot(record["logical_id"]).batch
        space = batch.spaces[0]
        metadata = dict(space.metadata)
        if damage == "missing-profile":
            metadata.pop("profile")
        elif damage == "wrong-policy":
            metadata = {"profile": "default"}
        replacement = replace(
            space, space_id="unknown-active-space", metadata=metadata,
            fingerprint="unproven-fingerprint" if damage == "foreign-fingerprint" else space.fingerprint,
            metric="dot" if damage == "wrong-metric" else space.metric,
        )
        storage.resource_store.replace_resource(replace(
            batch, spaces=(replacement,),
            vectors=tuple(replace(vector, space_id=replacement.space_id) for vector in batch.vectors),
        ))
    finally:
        storage.close()
    before = catalog_snapshot(tmp_path, config)
    provider = RecordingProvider(config)
    with pytest.raises(IncompatibleEmbeddingProfileError):
        run_indexer(tmp_path, config, provider=provider, force_reindex=True)
    assert provider.calls == []
    assert catalog_snapshot(tmp_path, config) == before


def test_new_profile_name_cannot_relabel_active_document_vectors(tmp_path: Path) -> None:
    config = config_for()
    seed(tmp_path, config)
    before = catalog_snapshot(tmp_path, config)
    with pytest.raises(IncompatibleEmbeddingProfileError):
        run_indexer(tmp_path, config, provider=RecordingProvider(config), profile="different-slot", force_reindex=True)
    assert catalog_snapshot(tmp_path, config) == before


def test_providerless_scan_cannot_remove_incompatible_active_vectors(tmp_path: Path) -> None:
    config = config_for()
    seed(tmp_path, config)
    changed = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": "text-b"})})
    before = catalog_snapshot(tmp_path, config)
    with pytest.raises(IncompatibleEmbeddingProfileError):
        run_indexer(tmp_path, changed, force_reindex=True)
    assert catalog_snapshot(tmp_path, config) == before


def test_legacy_direct_service_checks_recorded_full_fingerprint_before_start_run(tmp_path: Path) -> None:
    config = config_for(vector_value_policy=None)
    connection = get_connection(tmp_path / "synthetic-legacy.sqlite3")
    try:
        apply_migrations(connection, get_migrations_dir())
        storage = SQLiteIndexStorage(connection)
        (tmp_path / "kept.md").write_text("# Kept\n\nRollback evidence.\n", encoding="utf-8")
        assert IndexingService(tmp_path, config, storage, provider=RecordingProvider(config)).scan().status == "success"
        before = tuple(connection.iterdump())
        changed = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": "text-b"})})
        provider = RecordingProvider(changed)
        with pytest.raises(IncompatibleEmbeddingProfileError):
            IndexingService(tmp_path, changed, storage, provider=provider).scan(force_reindex=True)
        assert provider.calls == []
        assert tuple(connection.iterdump()) == before
        assert IndexingService(tmp_path, config, storage, provider=RecordingProvider(config)).scan().status == "success"
    finally:
        connection.close()


def test_missing_injected_storage_metadata_fails_closed_without_disk_fallback(tmp_path: Path) -> None:
    class UnknownStorage:
        def start_run(self, **kwargs):
            raise AssertionError("must not start an unproven run")

    with pytest.raises(IncompatibleEmbeddingProfileError):
        IndexingService(tmp_path, config_for(), UnknownStorage(), provider=RecordingProvider(config_for())).scan()
    assert not (tmp_path / ".mdrack").exists()


def test_refusal_diagnostic_is_content_free(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config = config_for()
    seed(tmp_path, config)
    changed = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": "PRIVATE_MODEL"})})
    with pytest.raises(IncompatibleEmbeddingProfileError) as error:
        run_indexer(tmp_path, changed, provider=RecordingProvider(changed))
    assert str(error.value) == "incompatible_embedding_profile"
    assert "PRIVATE_MODEL" not in caplog.text
    assert str(tmp_path) not in caplog.text
    assert "Rollback searchable evidence" not in caplog.text
    assert "PRIVATE_MODEL" not in json.dumps(error.value.args)


def test_app_text_profile_on_an_image_is_still_an_active_binding(tmp_path: Path) -> None:
    config = config_for()
    storage = create_application_storage(tmp_path, config, create=True)
    try:
        space = CanonicalTextEmbeddingSpace(embedding_profile_from_config(config, RecordingProvider(config))).record
        storage.resource_store.replace_resource(PreparedResourceBatch(
            ResourceRecord("text-image", "image", "image/png", "synthetic", Locator("whole_image", {})),
            (RepresentationRecord("text-rep", "text-image", "caption_text", "text", "Rollback caption"),),
            (SearchUnitRecord(
                "text-unit", "text-image", "text-rep", "whole_resource", "text", "Rollback caption",
                Locator("whole_image", {"source_ref": "synthetic-image"}), 0,
            ),),
            (space,), (VectorRecord("text-unit", space.space_id, (1.0, 0.0)),),
        ))
    finally:
        storage.close()
    before = catalog_snapshot(tmp_path, config)
    changed = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": "text-b"})})
    provider = RecordingProvider(changed)
    with pytest.raises(IncompatibleEmbeddingProfileError):
        run_indexer(tmp_path, changed, provider=provider)
    assert provider.calls == []
    assert catalog_snapshot(tmp_path, config) == before
    with MDRackEngine(root=tmp_path, config=config) as engine:
        assert engine.search_images_text("Rollback").results


def test_unused_old_spaces_do_not_bind_an_empty_text_collection(tmp_path: Path) -> None:
    config = config_for()
    seed(tmp_path, config)
    storage = create_application_storage(tmp_path, config)
    try:
        storage.delete_file("kept.md")
        assert storage.connection.execute("SELECT COUNT(*) FROM core_embedding_spaces").fetchone()[0] == 1
    finally:
        storage.close()
    changed = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": "text-b"})})
    assert run_indexer(tmp_path, changed, provider=RecordingProvider(changed)).status == "success"
    assert search_after_reopen(tmp_path, changed)[0].results


@pytest.mark.parametrize("status", ["partial_success", "failed"])
def test_embedded_provider_failure_preserves_last_good_resource_and_honest_status(
    tmp_path: Path, status: str, caplog: pytest.LogCaptureFixture,
) -> None:
    config = config_for()
    seed(tmp_path, config)
    before = search_after_reopen(tmp_path, config)
    (tmp_path / "kept.md").write_text("# Changed\n\nPRIVATE_PROVIDER_INPUT", encoding="utf-8")
    if status == "partial_success":
        (tmp_path / "good.md").write_text("# Good\n\nUseful next resource.", encoding="utf-8")

    class FailingProvider(RecordingProvider):
        async def embed(self, texts, profile="default"):
            if any("PRIVATE_PROVIDER_INPUT" in text for text in texts):
                raise RuntimeError("PRIVATE_PROVIDER_EXCEPTION")
            return await super().embed(texts, profile)

    with MDRackEngine(root=tmp_path, config=config, embedding_provider=FailingProvider(config)) as engine:
        result = engine.scan()
    assert result.status == status
    assert result.files_failed == 1 and result.errors_count == 1
    assert result.files_indexed == (1 if status == "partial_success" else 0)
    assert result.error_codes == ("FILE_INDEX_ERROR",)
    assert search_after_reopen(tmp_path, config)[0] == before[0]
    assert "PRIVATE_PROVIDER_INPUT" not in caplog.text
    assert "PRIVATE_PROVIDER_EXCEPTION" not in caplog.text
    (tmp_path / "kept.md").write_text("# Kept\n\nRollback searchable evidence.\n", encoding="utf-8")
    assert run_indexer(tmp_path, config, provider=RecordingProvider(config)).status == "success"
    assert search_after_reopen(tmp_path, config)[0].results
