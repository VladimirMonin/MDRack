"""S9 explicit image lifecycle over real local SQLite and filesystem."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.indexing import IndexingService
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.ingestion.images import (
    ExtractedImageText,
    ImageEmbeddingSpace,
    ImageIngestionService,
    StaticImageExtractor,
)
from mdrack.ports.embeddings import EmbeddingError
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_candidate_migrations, get_migrations_dir
from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)


class CountingFakeEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self, dimensions: int = 8) -> None:
        super().__init__(dimensions=dimensions)
        self.document_calls = 0
        self.query_calls = 0
        self.network_requests = 0
        self.fail_documents = False
        self.fail_queries = False

    async def embed(self, texts, profile: str = "default"):
        self.document_calls += 1
        if self.fail_documents:
            raise EmbeddingError("PRIVATE_PROVIDER_EXCEPTION_SENTINEL")
        return await super().embed(texts, profile=profile)

    async def embed_query(self, text: str, profile: str = "default"):
        self.query_calls += 1
        if self.fail_queries:
            raise EmbeddingError("PRIVATE_QUERY_EXCEPTION_SENTINEL")
        return await super().embed_query(text, profile=profile)


class FakeVisualProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.network_requests = 0

    async def embed_image(self, content: bytes, *, profile: str = "default") -> tuple[float, ...]:
        del content, profile
        self.calls += 1
        return (1.0, 0.0, 0.0, 0.0)


class ZeroVectorEmbeddingProvider(CountingFakeEmbeddingProvider):
    async def embed(self, texts, profile: str = "default"):
        del profile
        self.document_calls += 1
        return [[0.0] * self.dimensions for _text in texts]


def _document_trap(store: SQLiteResourceStore, text: str) -> None:
    store.replace_resource(
        PreparedResourceBatch(
            ResourceRecord(
                "document-trap",
                "document",
                "text/markdown",
                "fixture",
                Locator("document", {"id": "document-trap"}),
            ),
            (RepresentationRecord("document-representation", "document-trap", "retrieval_text", "text", text),),
            (
                SearchUnitRecord(
                    "document-unit",
                    "document-trap",
                    "document-representation",
                    "text_chunk",
                    "text",
                    text,
                    Locator("document", {"id": "document-trap"}),
                    0,
                ),
            ),
            (
                EmbeddingSpaceRecord(
                    "image-text-space",
                    8,
                    "cosine",
                    "image-text-fingerprint",
                    {"profile": "default"},
                ),
            ),
            (VectorRecord("document-unit", "image-text-space", (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),),
        )
    )


@pytest.fixture
def sqlite_store(tmp_path: Path):
    database_path = tmp_path / "image-resource.db"
    connection = get_connection(database_path)
    apply_candidate_migrations(connection, get_migrations_dir())
    store = SQLiteResourceStore(connection)
    yield database_path, connection, store
    connection.close()


async def test_explicit_image_create_search_replace_delete_and_source_immutability(
    tmp_path: Path,
    sqlite_store,
) -> None:
    database_path, connection, store = sqlite_store
    image = tmp_path / "private-image.png"
    source_bytes = b"S9_IMAGE_SOURCE_BYTES_NOT_FOR_SQLITE_\x00\x01\x02"
    image.write_bytes(source_bytes)
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    provider = CountingFakeEmbeddingProvider()
    visual = FakeVisualProvider()
    text_space = ImageEmbeddingSpace(
        "image-text-space",
        8,
        "image-text-fingerprint",
        profile_name="default",
    )
    visual_space = ImageEmbeddingSpace("image-visual-space", 4, "image-visual-fingerprint")
    first_extraction = (
        ExtractedImageText("caption_text", "complete caption needle", "caption-fake-v1", "en"),
        ExtractedImageText("ocr_text", "complete OCR sentinel text", "ocr-fake-v1", "en"),
    )
    service = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(first_extraction),
        text_embedding_provider=provider,
        text_space=text_space,
        visual_embedding_provider=visual,
        visual_space=visual_space,
    )
    _document_trap(store, "needle " * 50)

    first = await service.ingest(
        image,
        resource_id="image-logical-1",
        source_namespace="fixture",
        source_ref="image-ref-1",
        title="Public image title",
    )
    second = await service.ingest(
        image,
        resource_id="image-logical-1",
        source_namespace="fixture",
        source_ref="image-ref-1",
        title="Public image title",
    )

    assert first == second
    assert first.content_hash == f"sha256:{source_hash}"
    assert first.text_space_id == "image-text-space"
    assert first.visual_space_id == "image-visual-space"
    assert len(first.representation_ids) == len(first.unit_ids) == 3
    assert list(
        map(
            tuple,
            connection.execute(
                "SELECT representation_kind,text_content FROM core_representations "
                "WHERE resource_id='image-logical-1' ORDER BY representation_kind"
            ).fetchall(),
        )
    ) == [
        ("caption_text", "complete caption needle"),
        ("ocr_text", "complete OCR sentinel text"),
        ("visual", None),
    ]
    assert list(
        map(
            tuple,
            connection.execute(
                "SELECT DISTINCT unit_kind FROM core_search_units WHERE resource_id='image-logical-1'"
            ).fetchall(),
        )
    ) == [("whole_resource",)]
    assert list(
        map(
            tuple,
            connection.execute("SELECT space_id FROM core_embedding_spaces ORDER BY space_id").fetchall(),
        )
    ) == [("image-text-space",), ("image-visual-space",)]

    text = service.search_text("needle", limit=1)
    semantic = await service.search_semantic("complete caption needle", limit=1)
    hybrid = await service.search_hybrid("needle", limit=1)
    for result in (text, semantic, hybrid):
        assert [item.resource_id for item in result.results] == ["image-logical-1"]
        assert result.results[0].source_ref == "image-ref-1"
        assert "document-trap" not in repr(result.to_dict())
        assert "sqlite" not in repr(result.to_dict()).lower()
    assert text.results[0].score == text.results[0].evidence[0]["score"]
    assert semantic.results[0].score == semantic.results[0].evidence[0]["score"]
    assert hybrid.results[0].score == pytest.approx(2.0 / 61)

    replacement = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "replacement caption phrase", "caption-fake-v1", "en"),)
        ),
        text_embedding_provider=provider,
        text_space=text_space,
    )
    replaced = await replacement.ingest(
        image,
        resource_id="image-logical-1",
        source_namespace="fixture",
        source_ref="image-ref-1",
    )
    assert replaced.representation_ids[0] == first.representation_ids[0]
    assert replaced.unit_ids[0] == first.unit_ids[0]
    assert replacement.search_text("needle", limit=5).results == ()
    assert [item.resource_id for item in replacement.search_text("replacement", limit=5).results] == [
        "image-logical-1"
    ]
    assert connection.execute(
        "SELECT COUNT(*) FROM core_representations WHERE resource_id='image-logical-1'"
    ).fetchone()[0] == 1

    assert image.read_bytes() == source_bytes
    assert hashlib.sha256(image.read_bytes()).hexdigest() == source_hash
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert source_bytes not in database_path.read_bytes()
    assert provider.network_requests == visual.network_requests == 0
    assert provider.document_calls == 3
    assert provider.query_calls == 2
    assert visual.calls == 2

    replacement.delete("image-logical-1")
    replacement.delete("image-logical-1")
    assert store.read_resource("image-logical-1") is None
    assert replacement.search_text("replacement", limit=5).results == ()
    assert image.read_bytes() == source_bytes


async def test_image_failures_preserve_the_previous_complete_graph_and_sanitize_logs(
    tmp_path: Path,
    sqlite_store,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _database_path, connection, store = sqlite_store
    image = tmp_path / "PRIVATE_PATH_SENTINEL.png"
    image.write_bytes(b"source bytes")
    provider = CountingFakeEmbeddingProvider()
    space = ImageEmbeddingSpace("image-text-space", 8, "image-text-fingerprint")
    original = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "PRIVATE_CONTENT_SENTINEL prior", "caption-fake-v1"),)
        ),
        text_embedding_provider=provider,
        text_space=space,
    )
    await original.ingest(
        image,
        resource_id="image-logical-1",
        source_namespace="PRIVATE_ROOT_SENTINEL",
        source_ref="public-ref",
    )
    prior = connection.execute(
        "SELECT unit_id,text_content FROM core_search_units WHERE resource_id='image-logical-1'"
    ).fetchall()

    store.set_failure_hook(
        lambda point: (_ for _ in ()).throw(RuntimeError("PRIVATE_EXCEPTION_SENTINEL"))
        if point == "after_units"
        else None
    )
    failing = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "PRIVATE_CONTENT_SENTINEL replacement", "caption-fake-v1"),)
        ),
        text_embedding_provider=provider,
        text_space=space,
    )
    caplog.set_level(logging.INFO)
    with pytest.raises(Exception) as error:
        await failing.ingest(
            image,
            resource_id="image-logical-1",
            source_namespace="PRIVATE_ROOT_SENTINEL",
            source_ref="public-ref",
        )
    assert str(error.value) == "catalog_error"
    store.set_failure_hook(None)
    assert connection.execute(
        "SELECT unit_id,text_content FROM core_search_units WHERE resource_id='image-logical-1'"
    ).fetchall() == prior
    observed = caplog.text
    for sentinel in (
        "PRIVATE_PATH_SENTINEL",
        "PRIVATE_CONTENT_SENTINEL",
        "PRIVATE_ROOT_SENTINEL",
        "PRIVATE_EXCEPTION_SENTINEL",
        str(image),
    ):
        assert sentinel not in observed
    assert provider.network_requests == 0


async def test_image_provider_failure_degrades_search_and_preserves_graph_before_replace(
    tmp_path: Path,
    sqlite_store,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _database_path, connection, store = sqlite_store
    image = tmp_path / "PRIVATE_PATH_SENTINEL.png"
    image.write_bytes(b"source bytes")
    provider = CountingFakeEmbeddingProvider()
    space = ImageEmbeddingSpace(
        "image-text-space",
        8,
        "image-text-fingerprint",
        profile_name="default",
    )
    service = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "searchable prior caption", "caption-fake-v1"),)
        ),
        text_embedding_provider=provider,
        text_space=space,
    )
    await service.ingest(
        image,
        resource_id="image-logical-1",
        source_namespace="PRIVATE_ROOT_SENTINEL",
        source_ref="public-ref",
    )
    prior = list(
        map(
            tuple,
            connection.execute(
                "SELECT unit_id,text_content FROM core_search_units WHERE resource_id='image-logical-1'"
            ).fetchall(),
        )
    )

    caplog.set_level(logging.INFO)
    provider.fail_queries = True
    semantic = await service.search_semantic("PRIVATE_QUERY_SENTINEL", limit=5)
    hybrid = await service.search_hybrid("searchable", limit=5)
    assert semantic.results == ()
    assert semantic.degraded is True
    assert semantic.degraded_reason == "embedding_provider_error"
    assert [item.resource_id for item in hybrid.results] == ["image-logical-1"]
    assert hybrid.degraded is True
    assert hybrid.degraded_reason == "embedding_provider_error"

    provider.fail_queries = False
    provider.fail_documents = True
    replacement = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "replacement caption", "caption-fake-v1"),)
        ),
        text_embedding_provider=provider,
        text_space=space,
    )
    with pytest.raises(EmbeddingError, match="^embedding_provider_error$"):
        await replacement.ingest(
            image,
            resource_id="image-logical-1",
            source_namespace="PRIVATE_ROOT_SENTINEL",
            source_ref="public-ref",
        )
    assert list(
        map(
            tuple,
            connection.execute(
                "SELECT unit_id,text_content FROM core_search_units WHERE resource_id='image-logical-1'"
            ).fetchall(),
        )
    ) == prior

    provider.fail_documents = False
    calls_before = provider.document_calls
    bounded = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "two words", "caption-fake-v1"),)
        ),
        text_embedding_provider=provider,
        text_space=space,
        max_text_tokens=1,
    )
    with pytest.raises(ValueError, match="whole-resource limit"):
        await bounded.ingest(
            image,
            resource_id="image-logical-1",
            source_namespace="PRIVATE_ROOT_SENTINEL",
            source_ref="public-ref",
        )
    assert provider.document_calls == calls_before
    assert provider.network_requests == 0
    for sentinel in (
        "PRIVATE_PATH_SENTINEL",
        "PRIVATE_ROOT_SENTINEL",
        "PRIVATE_QUERY_SENTINEL",
        "PRIVATE_PROVIDER_EXCEPTION_SENTINEL",
        "PRIVATE_QUERY_EXCEPTION_SENTINEL",
        str(image),
    ):
        assert sentinel not in caplog.text


async def test_image_zero_cosine_projection_fails_before_replace_and_preserves_graph(
    tmp_path: Path,
    sqlite_store,
) -> None:
    _database_path, connection, store = sqlite_store
    image = tmp_path / "PRIVATE_PATH_SENTINEL.png"
    image.write_bytes(b"source bytes")
    space = ImageEmbeddingSpace("image-text-space", 8, "image-text-fingerprint")
    original_service = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "prior caption", "caption-fake-v1"),)
        ),
        text_embedding_provider=CountingFakeEmbeddingProvider(),
        text_space=space,
    )
    await original_service.ingest(
        image,
        resource_id="image-logical-1",
        source_namespace="PRIVATE_ROOT_SENTINEL",
        source_ref="public-ref",
    )
    prior = list(
        map(
            tuple,
            connection.execute(
                "SELECT unit_id,text_content FROM core_search_units "
                "WHERE resource_id='image-logical-1'"
            ).fetchall(),
        )
    )
    zero_provider = ZeroVectorEmbeddingProvider()
    replacement = ImageIngestionService(
        store,
        extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "replacement caption", "caption-fake-v1"),)
        ),
        text_embedding_provider=zero_provider,
        text_space=space,
    )

    with pytest.raises(Exception) as caught:
        await replacement.ingest(
            image,
            resource_id="image-logical-1",
            source_namespace="PRIVATE_ROOT_SENTINEL",
            source_ref="public-ref",
        )

    assert str(caught.value) == "validation"
    assert list(
        map(
            tuple,
            connection.execute(
                "SELECT unit_id,text_content FROM core_search_units "
                "WHERE resource_id='image-logical-1'"
            ).fetchall(),
        )
    ) == prior
    assert zero_provider.document_calls == 1
    assert zero_provider.network_requests == 0


def test_markdown_scan_never_calls_explicit_image_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    image = root / "referenced.png"
    note.write_text("# Note\n\nBefore ![Alt](referenced.png) after.\n", encoding="utf-8")
    image.write_bytes(b"untouched image")
    before = image.read_bytes()
    original_open = Path.open
    original_stat = Path.stat
    image_accesses: list[str] = []

    async def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("explicit image ingestion was called from Markdown scan")

    def guarded_open(path: Path, *args, **kwargs):
        if path == image:
            image_accesses.append("open")
            raise AssertionError("Markdown scan opened a referenced image")
        return original_open(path, *args, **kwargs)

    def guarded_stat(path: Path, *args, **kwargs):
        if path == image:
            image_accesses.append("stat")
            raise AssertionError("Markdown scan stat-ed a referenced image")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(ImageIngestionService, "ingest", forbidden)
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(tmp_path / "legacy-store")))
    storage = create_sqlite_index_storage(root, config)
    try:
        result = IndexingService(root, config, storage).scan(force_reindex=True)
    finally:
        storage.close()
    assert result.status == "success"
    assert image_accesses == []
    with original_open(image, "rb") as handle:
        assert handle.read() == before
