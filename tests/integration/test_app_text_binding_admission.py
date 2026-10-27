"""Actual app TEXT producers retain admission authority when metadata is unknown."""

from __future__ import annotations

import asyncio
import json
import os
import struct
import zlib
from dataclasses import replace

import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
from mdrack.application.compatibility import create_application_storage
from mdrack.application.indexing import IndexingService
from mdrack.cli import main
from mdrack.config.loader import write_config
from mdrack.config.models import EmbeddingConfig, MDRackConfig
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.indexing.indexer import run_indexer
from mdrack.ingestion.images import ExtractedImageText, ImageEmbeddingSpace, StaticImageExtractor
from mdrack.integrations.lmstudio import LMStudioModelInfo
from mdrack.public_api import MDRackEngine

OLD = "synthetic-text-a"
NEW = "synthetic-text-b"


class Provider(FakeEmbeddingProvider):
    def __init__(self, *, model=OLD, dimensions=4, events=None, **kwargs):
        super().__init__(dimensions, provider_name="lmstudio")
        self._model_name = model
        self.events = events if events is not None else []
        self.events.append("provider_created")

    async def embed(self, texts, profile="default"):
        self.events.append("embed")
        return await super().embed(texts, profile)

    async def close(self):
        self.events.append("provider_closed")


class Visual:
    async def embed_image(self, content, *, profile="default"):
        return (1.0, 0.0, 0.0)


class Control:
    def __init__(self, events):
        self.events = events

    def list_models(self):
        self.events.append("list")
        return [
            LMStudioModelInfo(OLD, "loaded", True, instance_ids=("old-instance",)),
            LMStudioModelInfo(NEW, "downloaded", False),
        ]

    def load_model(self, name):
        self.events.append("load")
        return {"key": name, "state": "loaded"}

    def probe_embedding_dimensions(self, name):
        self.events.append("probe")
        return 4

    def unload_model(self, instance):
        self.events.append("unload")

    async def close(self):
        self.events.append("control_closed")


def _config():
    return MDRackConfig(embedding=EmbeddingConfig(model=OLD, dimensions=4))


def _png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def _initialize(root, config):
    create_application_storage(root, config, create=True).close()
    write_config(config, root / ".mdrack/config.toml")


def _produce_image(root, config, projection, *, profile_name="default"):
    source = root / "synthetic.png"
    source.write_bytes(_png())
    kind = "caption_text" if projection == "image_text_aggregate" else projection
    with MDRackEngine(
        root=root, config=config, embedding_provider=Provider(), profile=profile_name,
        image_extractor=StaticImageExtractor((
            ExtractedImageText(kind, "Durable image evidence", "synthetic-extractor"),
        )),
        visual_embedding_provider=Visual(), visual_embedding_space=ImageEmbeddingSpace("native-visual", 3, "native-v1"),
    ) as engine:
        result = asyncio.run(engine.ingest_image(
            source, resource_id="app-image", source_namespace="synthetic", source_ref="synthetic.png",
        ))
        assert result.text_space_id and result.visual_space_id
    storage = create_application_storage(root, config)
    try:
        batch = storage.resource_store.read_snapshot("app-image").batch
        assert batch is not None
        # Independently retain each actual sibling, so another intact caption
        # cannot conceal an omitted OCR/aggregate binding in selection.
        representations = tuple(
            rep for rep in batch.representations if rep.representation_kind in {projection, "visual"}
        )
        representation_ids = {rep.representation_id for rep in representations}
        units = tuple(unit for unit in batch.units if unit.representation_id in representation_ids)
        unit_ids = {unit.unit_id for unit in units}
        return replace(batch, representations=representations, units=units,
                       vectors=tuple(vector for vector in batch.vectors if vector.unit_id in unit_ids))
    finally:
        storage.close()


def _snapshot(root, config):
    with open_application_catalog_readonly(root, config) as catalog:
        dump = tuple(catalog.connection.iterdump())
    return (
        (root / config.paths.store / "catalog.sqlite3").read_bytes(), dump,
        (root / ".mdrack/config.toml").read_bytes(),
    )


def _image_search(root, config):
    with MDRackEngine(root=root, config=config, embedding_provider=Provider()) as engine:
        lexical = engine.search_images_text("Durable")
        semantic = asyncio.run(engine.search_images_semantic("Durable"))
        assert lexical.results and semantic.results and not semantic.degraded
        return lexical, semantic


def _edges(monkeypatch, events):
    def control(ctx):
        events.append("control_created")
        return Control(events)
    monkeypatch.setattr("mdrack.cli.commands.model.create_model_control_client", control)
    monkeypatch.setattr("mdrack.cli.commands.model.LMStudioProvider", lambda **kw: Provider(events=events, **kw))
    for command in ("scan", "rebuild"):
        monkeypatch.setattr(
            f"mdrack.cli.commands.{command}.create_embedding_provider",
            lambda name, cfg: Provider(model=cfg.embedding.model, dimensions=cfg.embedding.dimensions, events=events),
        )


@pytest.mark.parametrize("entry", ["engine", "wrapper", "model", "service", "scan", "rebuild"])
@pytest.mark.parametrize("projection", ["caption_text", "ocr_text", "image_text_aggregate"])
@pytest.mark.parametrize("metadata", ["intact", "missing-profile", "wrong-profile"])
def test_actual_app_text_unknown_metadata_cannot_unbind_slot(tmp_path, monkeypatch, entry, projection, metadata):
    config = _config()
    _initialize(tmp_path, config)
    producer = tmp_path / "producer"
    producer.mkdir()
    _initialize(producer, config)
    batch = _produce_image(producer, config, projection)
    if metadata != "intact":
        spaces = []
        for space in batch.spaces:
            values = dict(space.metadata)
            if values.get("profile") == "default":
                if metadata == "missing-profile":
                    del values["profile"]
                else:
                    values["profile"] = "unproven-other-slot"
            spaces.append(replace(space, metadata=values))
        batch = replace(batch, spaces=tuple(spaces))
    storage = create_application_storage(tmp_path, config)
    try:
        storage.resource_store.replace_resource(batch)  # Valid atomic generic publication, not raw SQL damage.
        published_image = storage.resource_store.read_snapshot("app-image").batch
        assert published_image is not None
    finally:
        storage.close()
    expected_search = _image_search(tmp_path, config)
    changed = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": NEW})})
    if entry in {"scan", "rebuild"}:
        write_config(changed, tmp_path / ".mdrack/config.toml")
    before = _snapshot(tmp_path, config)
    (tmp_path / "new.md").write_text("# New\n\nDurable document evidence.\n", encoding="utf-8")
    events, traversals = [], []
    _edges(monkeypatch, events)
    original_walk = os.walk

    def observe_walk(top, *args, **kwargs):
        traversals.append(top)
        return original_walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", observe_walk)
    if entry in {"model", "scan", "rebuild"}:
        command = ["model", "switch", NEW] if entry == "model" else ["scan"] if entry == "scan" else [
            "rebuild", "embeddings",
        ]
        result = CliRunner().invoke(main, ["--root", str(tmp_path), *command])
        assert result.exit_code == 1, "unknown app TEXT metadata was treated as empty"
        assert json.loads(result.output)["error"]["details"]["reason_code"] == "incompatible_embedding_profile"
    else:
        provider = Provider(model=NEW, events=events)
        events.clear()
        with pytest.raises(IncompatibleEmbeddingProfileError):
            if entry == "engine":
                with MDRackEngine(root=tmp_path, config=changed, embedding_provider=provider) as engine:
                    engine.scan(force_reindex=True)
            elif entry == "wrapper":
                run_indexer(tmp_path, changed, provider=provider, force_reindex=True)
            else:
                storage = create_application_storage(tmp_path, changed)
                try:
                    IndexingService(tmp_path, changed, storage, provider=provider).scan(force_reindex=True)
                finally:
                    storage.close()
    assert events == []
    assert traversals == []
    assert _snapshot(tmp_path, config) == before
    assert _image_search(tmp_path, config) == expected_search
    # The original profile remains useful for real reopened vector retrieval.
    # Unknown metadata cannot be adopted/repaired by relabeling an immutable
    # space, even with that original model. Continue writes in a fresh collection.
    if metadata != "intact":
        with pytest.raises(IncompatibleEmbeddingProfileError):
            run_indexer(tmp_path, config, provider=Provider())
        assert _snapshot(tmp_path, config) == before
        recovery_root = tmp_path / "next-collection"
        recovery_root.mkdir()
        _initialize(recovery_root, config)
        assert _produce_image(recovery_root, config, projection).vectors
        (recovery_root / "new.md").write_text("# New\n\nDurable document evidence.\n", encoding="utf-8")
    else:
        recovery_root = tmp_path
    result = run_indexer(recovery_root, config, provider=Provider())
    assert result.status == "success" and result.files_indexed == 1
    assert _image_search(recovery_root, config)[0].results
    # A later admitted document changes global BM25 statistics legitimately.
    # The old image graph, locator and semantic retrieval must still be intact.
    with open_application_catalog_readonly(tmp_path, config) as catalog:
        assert catalog.read_snapshot("app-image").batch == published_image
    lexical, semantic = _image_search(tmp_path, config)
    assert [(item.resource_id, item.source_ref) for item in lexical.results] == [
        (item.resource_id, item.source_ref) for item in expected_search[0].results
    ]
    assert semantic == expected_search[1]


@pytest.mark.parametrize("derived_dense", [False, True])
def test_actual_native_text_and_visual_siblings_remain_unrelated(tmp_path, derived_dense):
    config = _config()
    _initialize(tmp_path, config)
    source = tmp_path / "synthetic.png"
    source.write_bytes(_png())
    native = SyntheticPreparedProvider()
    with MDRackEngine(root=tmp_path, config=config, prepared_embedding_provider=native) as engine:
        result = asyncio.run(engine.ingest_native_image(
            source, resource_id="native-image", source_namespace="synthetic", source_ref="synthetic.png",
            derived_text=(ExtractedImageText("caption_text", "Durable native evidence", "synthetic-extractor"),),
            derived_dense=derived_dense,
        ))
    assert result.visual_space_id == native.space.space_id
    (tmp_path / "new.md").write_text("# New\n\nDurable document evidence.\n", encoding="utf-8")
    assert run_indexer(tmp_path, config, provider=Provider()).status == "success"
    assert run_indexer(tmp_path, config, provider=Provider(), force_reindex=True).status == "success"
    with open_application_catalog_readonly(tmp_path, config) as catalog:
        assert catalog.read_snapshot("native-image").batch is not None
    with MDRackEngine(root=tmp_path, config=config) as engine:
        assert engine.search_images_text("Durable").results


def test_explicit_other_app_text_slot_is_not_a_global_space_restriction(tmp_path):
    config = _config()
    _initialize(tmp_path, config)
    producer = tmp_path / "producer"
    producer.mkdir()
    other = config.model_copy(update={
        "embedding": config.embedding.model_copy(update={"query_instruction": "Other synthetic slot instruction"}),
    })
    _initialize(producer, other)
    batch = _produce_image(producer, other, "caption_text", profile_name="other-slot")
    storage = create_application_storage(tmp_path, config)
    try:
        storage.resource_store.replace_resource(batch)
    finally:
        storage.close()
    (tmp_path / "new.md").write_text("# New\n\nDurable document evidence.\n", encoding="utf-8")
    assert run_indexer(tmp_path, config, provider=Provider()).status == "success"
    with MDRackEngine(root=tmp_path, config=other, profile="other-slot", embedding_provider=Provider()) as engine:
        assert asyncio.run(engine.search_images_semantic("Durable")).results
    with open_application_catalog_readonly(tmp_path, config) as catalog:
        assert catalog.read_snapshot("app-image").batch == batch
