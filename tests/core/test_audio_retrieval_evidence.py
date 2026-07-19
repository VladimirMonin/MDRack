from __future__ import annotations

from pathlib import Path

import pytest
from fakes.memory_store import MemoryCatalog

from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.domain import (
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    Facet,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog


def _audio_batch(resource_id: str, *, replacement: bool = False) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    texts = ("replacement passage",) if replacement else ("needle needle", "needle")
    units = tuple(
        SearchUnitRecord(
            f"unit-{resource_id}-{ordinal}",
            resource_id,
            representation_id,
            "time_segment",
            "text",
            text,
            Locator(
                "time_segment",
                {"start_ms": ordinal * 1_000, "end_ms": (ordinal + 1) * 1_000, "track": "audio"},
            ),
            ordinal,
        )
        for ordinal, text in enumerate(texts)
    )
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "audio",
            "audio/wav",
            "fixture",
            Locator("relative", {"name": f"{resource_id}.wav"}),
            f"sha256:{resource_id}",
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id,
                "timed_passage",
                "text",
                "\n\n".join(texts),
            ),
        ),
        units,
        (EmbeddingSpaceRecord("audio-space", 2, "dot", "audio-fixture-v1"),),
        tuple(VectorRecord(unit.unit_id, "audio-space", (1.0, 0.0)) for unit in units),
        (ResourceFacet(resource_id, Facet("media", "audio"), "fixture"),),
    )


def _ids(items: object) -> tuple[str, ...]:
    return tuple(item.unit_id for item in items)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "scope",
    [
        SearchScope(),
        SearchScope(resource_kinds=("audio",), media_types=("audio/wav",)),
        SearchScope(source_namespaces=("fixture",), representation_kinds=("timed_passage",)),
        SearchScope(modalities=("text",), unit_kinds=("time_segment",)),
        SearchScope(facets_any=(Facet("media", "audio"),)),
        SearchScope(facets_all=(Facet("media", "audio"),), facets_none=(Facet("media", "video"),)),
    ],
)
def test_audio_memory_and_sqlite_have_identical_ranked_ids_and_filters(
    tmp_path: Path, scope: SearchScope
) -> None:
    memory = MemoryCatalog()
    sqlite = SQLiteCatalog.create(tmp_path / "audio.sqlite3")
    try:
        for resource_id in ("audio-a", "audio-b"):
            batch = _audio_batch(resource_id)
            memory.replace_resource(batch)
            sqlite.replace_resource(batch)

        lexical = LexicalBranch("audio-lexical", "needle", candidate_limit=10)
        vector = VectorBranch("audio-vector", "audio-space", (1.0, 0.0), candidate_limit=10)
        memory_lexical = memory.search_lexical(lexical, scope=scope)
        sqlite_lexical = sqlite.search_lexical(lexical, scope=scope)
        memory_vector = memory.search_vector(vector, scope=scope)
        sqlite_vector = sqlite.search_vector(vector, scope=scope)

        assert _ids(memory_lexical) == _ids(sqlite_lexical)
        assert _ids(memory_vector) == _ids(sqlite_vector)
        assert tuple(item.rank for item in sqlite_lexical) == tuple(range(1, len(sqlite_lexical) + 1))
        assert tuple(item.rank for item in sqlite_vector) == tuple(range(1, len(sqlite_vector) + 1))
    finally:
        sqlite.close()


def test_audio_replacement_and_injected_failure_are_atomic_across_adapters(tmp_path: Path) -> None:
    memory = MemoryCatalog()
    sqlite = SQLiteCatalog.create(tmp_path / "audio.sqlite3")
    original = _audio_batch("audio-a")
    replacement = _audio_batch("audio-a", replacement=True)
    try:
        memory.replace_resource(original)
        sqlite.replace_resource(original)
        memory.replace_resource(replacement)
        sqlite.replace_resource(replacement)
        assert memory.search_lexical(LexicalBranch("replacement", "replacement"), scope=SearchScope())
        assert sqlite.search_lexical(LexicalBranch("replacement", "replacement"), scope=SearchScope())

        memory.inject_replace_failure(RuntimeError("AUDIO_PRIVATE_SENTINEL"))
        sqlite.set_failure_hook(
            lambda point: (
                (_ for _ in ()).throw(RuntimeError("AUDIO_PRIVATE_SENTINEL"))
                if point == "after_units"
                else None
            )
        )
        with pytest.raises(CatalogExecutionError) as memory_error:
            CoreIndexingService(memory).index(original)
        with pytest.raises(CatalogExecutionError) as sqlite_error:
            sqlite.replace_resource(original)
        assert "AUDIO_PRIVATE_SENTINEL" not in str(memory_error.value)
        assert "AUDIO_PRIVATE_SENTINEL" not in str(sqlite_error.value)
        assert memory.read_resource("audio-a").content_hash == replacement.resource.content_hash  # type: ignore[union-attr]
        assert sqlite.read_resource("audio-a").content_hash == replacement.resource.content_hash  # type: ignore[union-attr]
    finally:
        sqlite.close()
