#!/usr/bin/env python3
"""Installed-artifact smoke for MDRack public and persistence contracts."""

from __future__ import annotations

import asyncio
import importlib
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

EXPECTED_VERSION = "1.2.0"
REGISTRY_IMPORTS = {
    "mdrack.application.query": ("SearchService",),
    "mdrack.application.retrieval": ("RetrievalService", "HybridRetrievalService"),
    "mdrack.public_api": (
        "EmbeddingCapabilities",
        "EmbeddingProfile",
        "DuplicateResourceItem",
        "DuplicateResourceResult",
        "FacetFilter",
        "HybridRetrievalService",
        "ExtractedImageText",
        "ImageEmbeddingSpace",
        "ImageExtractor",
        "ImageIngestionResult",
        "ImageSearchItem",
        "ImageSearchResult",
        "IndexingResult",
        "MDRackEngine",
        "RetrievalCandidate",
        "RetrievalItem",
        "RetrievalResult",
        "ResourceQueryScope",
        "SimilarResourceItem",
        "SimilarResourceResult",
        "SourceLocator",
        "StaticImageExtractor",
        "VisualEmbeddingProvider",
    ),
    "mdrack.public_api.models": (
        "EmbeddingCapabilities",
        "EmbeddingProfile",
        "IndexingResult",
        "MetadataFacetValue",
        "MetadataFilter",
        "MetadataFilters",
        "MetadataInspection",
        "RetrievalCandidate",
        "RetrievalItem",
        "RetrievalResult",
        "ResourcePresetEvidence",
        "ResourcePresetSearchItem",
        "ResourcePresetSearchResult",
        "ResourceSearchResult",
        "SourceLocator",
        "TimedEvidence",
        "TimedSearchItem",
        "TimedSearchResult",
        "TextualSimilarityResult",
        "TextualSimilarResourceItem",
        "TranscriptIngestionResult",
        "UnifiedTextEvidence",
        "UnifiedTextSearchItem",
        "UnifiedTextSearchResult",
        "UnifiedTextSimilarityResult",
        "UnifiedTextScopeName",
        "VideoCompositionResult",
    ),
    "mdrack.indexing.indexer": ("IndexerResult", "run_indexer"),
    "mdrack.search.text": ("TextSearchItem", "TextSearchResult", "text_search"),
    "mdrack.search.semantic": (
        "SemanticSearchResultItem",
        "SemanticSearchResult",
        "semantic_search",
    ),
    "mdrack.search.hybrid": ("HybridSearchResultItem", "HybridSearchResult", "hybrid_search"),
    "mdrack.markdown.parser": ("parse_markdown",),
    "mdrack.markdown.ir": (
        "BlockType",
        "ContentType",
        "MarkdownBlock",
        "SectionNode",
        "FinalChunk",
        "ParsedDocument",
    ),
    "mdrack.markdown.chunk_builder": ("build_chunks",),
    "mdrack.markdown.section_builder": ("build_sections",),
    "mdrack.markdown.embedding_text": ("build_embedding_text",),
    "mdrack.markdown.frontmatter": ("parse_frontmatter",),
    "mdrack.embeddings.protocol": ("EmbeddingError", "EmbeddingHealth", "EmbeddingProvider"),
    "mdrack.embeddings.runtime": (
        "create_embedding_provider",
        "embedding_profile_from_config",
        "create_lmstudio_control_client",
        "close_async_resource",
    ),
    "mdrack.embeddings.lmstudio": (
        "EmbeddingError",
        "EmbeddingHealth",
        "LMStudioControlClient",
        "LMStudioControlError",
        "LMStudioDownloadInfo",
        "LMStudioDownloadRequest",
        "LMStudioLoadedModelInfo",
        "LMStudioLoadResult",
        "LMStudioModelInfo",
        "LMStudioProvider",
    ),
    "mdrack.integrations.lmstudio": (
        "EmbeddingError",
        "EmbeddingHealth",
        "LMStudioControlClient",
        "LMStudioControlError",
        "LMStudioDownloadInfo",
        "LMStudioDownloadRequest",
        "LMStudioLoadedModelInfo",
        "LMStudioLoadResult",
        "LMStudioModelInfo",
        "LMStudioProvider",
    ),
}
EXPECTED_PUBLIC_API_EXPORTS = REGISTRY_IMPORTS["mdrack.public_api"]
EXPECTED_PUBLIC_API_MODEL_EXPORTS = REGISTRY_IMPORTS["mdrack.public_api.models"]


class _MemoryCatalog:
    def __init__(self) -> None:
        self.batch: Any | None = None

    def replace_resource(self, batch: Any) -> None:
        self.batch = batch

    def delete_resource(self, resource_id: str) -> None:
        if self.batch is not None and self.batch.resource.resource_id == resource_id:
            self.batch = None


def _batch():
    from mdrack_core import (
        EmbeddingSpaceRecord,
        Locator,
        PreparedResourceBatch,
        RepresentationRecord,
        ResourceRecord,
        SearchUnitRecord,
        VectorRecord,
    )

    return PreparedResourceBatch(
        ResourceRecord(
            "resource-installed-smoke",
            "document",
            "text/plain",
            "installed_smoke",
            Locator("relative", {"ref": "installed-smoke"}),
            "sha256:installed-smoke",
            "",
            {},
        ),
        (
            RepresentationRecord(
                "representation-installed-smoke",
                "resource-installed-smoke",
                "retrieval_text",
                "text",
                "installed smoke text",
                "en",
                "installed-smoke-producer",
                3,
                "exact",
                {},
            ),
        ),
        (
            SearchUnitRecord(
                "unit-installed-smoke",
                "resource-installed-smoke",
                "representation-installed-smoke",
                "whole_resource",
                "text",
                "installed smoke text",
                Locator("whole_resource", {}),
                0,
                3,
                "exact",
                {},
            ),
        ),
        (EmbeddingSpaceRecord("space-installed-smoke", 2, "dot", "installed-smoke-space", {}),),
        (VectorRecord("unit-installed-smoke", "space-installed-smoke", (1.0, -0.0)),),
        (),
    )


def _audio_batch(resource_id: str, *, replacement: bool = False):
    from mdrack_core import (
        EmbeddingSpaceRecord,
        Facet,
        Locator,
        PreparedResourceBatch,
        RepresentationRecord,
        ResourceFacet,
        ResourceRecord,
        SearchUnitRecord,
        VectorRecord,
    )

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
            "installed-audio",
            Locator("relative", {"name": f"{resource_id}.wav"}),
            f"sha256:{resource_id}",
        ),
        (RepresentationRecord(representation_id, resource_id, "timed_passage", "text", "\\n\\n".join(texts)),),
        units,
        (EmbeddingSpaceRecord("audio-space", 2, "dot", "audio-fixture-v1", {}),),
        tuple(VectorRecord(unit.unit_id, "audio-space", (1.0, 0.0)) for unit in units),
        (ResourceFacet(resource_id, Facet("media", "audio"), "installed-audio"),),
    )


def _check_installed_audio_retrieval() -> None:
    from mdrack_core import Facet, LexicalBranch, SearchScope, VectorBranch
    from mdrack_sqlite import SQLiteCatalog

    with tempfile.TemporaryDirectory(prefix="mdrack-installed-audio-") as directory:
        catalog = SQLiteCatalog.create(Path(directory) / "audio.sqlite3")
        try:
            for resource_id in ("audio-a", "audio-b"):
                catalog.replace_resource(_audio_batch(resource_id))
            scopes = (
                SearchScope(resource_kinds=("audio",), media_types=("audio/wav",)),
                SearchScope(representation_kinds=("timed_passage",), unit_kinds=("time_segment",)),
                SearchScope(facets_any=(Facet("media", "audio"),)),
            )
            for scope in scopes:
                lexical = catalog.search_lexical(
                    LexicalBranch("installed-audio-lexical", "needle", candidate_limit=10), scope=scope
                )
                vector = catalog.search_vector(
                    VectorBranch("installed-audio-vector", "audio-space", (1.0, 0.0), candidate_limit=10),
                    scope=scope,
                )
                assert tuple(item.rank for item in lexical) == tuple(range(1, len(lexical) + 1))
                assert tuple(item.rank for item in vector) == tuple(range(1, len(vector) + 1))
                assert all(item.evidence_locator.kind == "time_segment" for item in lexical + vector)
            catalog.replace_resource(_audio_batch("audio-a", replacement=True))
            assert catalog.search_lexical(
                LexicalBranch("installed-audio-replacement", "replacement"), scope=SearchScope()
            )
        finally:
            catalog.close()


def _check_imports() -> int:
    import mdrack
    import mdrack_core

    assert mdrack.__version__ == EXPECTED_VERSION
    assert mdrack.__file__ is not None and "site-packages" in mdrack.__file__
    assert mdrack_core.__file__ is not None and "site-packages" in mdrack_core.__file__
    symbol_count = 0
    for module_name, symbol_names in REGISTRY_IMPORTS.items():
        module = importlib.import_module(module_name)
        for symbol_name in symbol_names:
            assert hasattr(module, symbol_name), f"missing installed import: {module_name}.{symbol_name}"
            symbol_count += 1
    public_api = importlib.import_module("mdrack.public_api")
    public_models = importlib.import_module("mdrack.public_api.models")
    assert tuple(public_api.__all__) == EXPECTED_PUBLIC_API_EXPORTS
    assert tuple(public_models.__all__) == EXPECTED_PUBLIC_API_MODEL_EXPORTS
    return symbol_count


def _check_memory_core() -> None:
    from mdrack_core.application.indexing import CoreIndexingService

    catalog = _MemoryCatalog()
    batch = _batch()
    CoreIndexingService(catalog).index(batch)
    assert catalog.batch == batch
    CoreIndexingService(catalog).delete(batch.resource.resource_id)
    assert catalog.batch is None


def _check_sqlite_candidate() -> None:
    from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
    from mdrack.storage.sqlite.migrations import (
        EXPECTED_MIGRATION_VERSION,
        apply_candidate_migrations,
        get_migrations_dir,
    )
    from mdrack_core.application.indexing import CoreIndexingService

    with tempfile.TemporaryDirectory(prefix="mdrack-installed-smoke-") as directory:
        connection = sqlite3.connect(Path(directory) / "candidate.db")
        connection.row_factory = sqlite3.Row
        try:
            apply_candidate_migrations(connection, get_migrations_dir())
            applied_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            assert applied_version == EXPECTED_MIGRATION_VERSION
            store = SQLiteResourceStore(connection)
            batch = _batch()
            CoreIndexingService(store).index(batch)
            assert store.read_resource(batch.resource.resource_id) == batch.resource
            assert store.read_unit(batch.units[0].unit_id) == batch.units[0]
            assert store.read_vector(batch.vectors[0].unit_id, batch.vectors[0].space_id) == batch.vectors[0]
        finally:
            connection.close()


def _check_cli_json() -> None:
    with tempfile.TemporaryDirectory(prefix="mdrack-installed-cli-") as directory:
        result = subprocess.run(
            [str(Path(sys.executable).with_name("mdrack")), "--root", directory, "status"],
            check=False,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("\n") == 1
    payload = json.loads(result.stdout)
    assert set(payload) == {"ok", "data", "meta"}
    assert payload["meta"] == {"command": "status"}


def _check_legacy_retrieval_parity() -> int:
    from click.testing import CliRunner

    from mdrack.cli import main as cli_main
    from mdrack.config.models import MDRackConfig, PathsConfig
    from mdrack.embeddings.fake import FakeEmbeddingProvider
    from mdrack.embeddings.runtime import embedding_profile_from_config
    from mdrack.public_api import MDRackEngine
    from mdrack.storage.sqlite.connection import get_connection
    from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir

    with tempfile.TemporaryDirectory(prefix="mdrack-installed-parity-") as directory:
        root = Path(directory)
        store = root / ".mdrack"
        store.mkdir()
        connection = get_connection(store / "knowledge.db")
        apply_migrations(connection, get_migrations_dir())
        provider = FakeEmbeddingProvider(dimensions=1024)
        config = MDRackConfig(paths=PathsConfig(root=".", store=str(store)))
        profile = embedding_profile_from_config(config, provider, "default")
        content = "Installed retrieval parity"
        vector = provider._text_to_vector(content)
        connection.execute(
            "INSERT INTO embedding_profiles (name, model, dimensions, fingerprint) VALUES (?, ?, ?, ?)",
            ("default", "fake", 1024, profile.fingerprint),
        )
        connection.execute(
            "INSERT INTO files (id, logical_id, root_id, relative_path, source_hash, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("file-record", "file-logical", "root", "docs/parity.md", "hash", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO chunks "
            "(id, logical_id, file_id, content, content_type, chunk_index, heading_path, "
            "start_line, end_line, block_logical_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "chunk-record",
                "chunk-logical",
                "file-record",
                content,
                "text",
                0,
                json.dumps(["Parity"]),
                2,
                3,
                "block-logical",
            ),
        )
        connection.execute(
            "INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path) VALUES (?, ?, ?, ?)",
            ("chunk-record", content, "text", "Parity"),
        )
        connection.execute(
            "INSERT INTO chunk_embeddings "
            "(chunk_id, profile_name, embedding, embedded_at, profile_fingerprint) VALUES (?, ?, ?, ?, ?)",
            (
                "chunk-record",
                "default",
                json.dumps(vector).encode("utf-8"),
                "2026-01-01T00:00:00Z",
                profile.fingerprint,
            ),
        )
        connection.commit()
        connection.close()

        engine = MDRackEngine(root=root, config=config, embedding_provider=provider)
        try:
            for mode in ("text", "semantic", "hybrid"):
                if mode == "text":
                    embedded = engine.search_text("Installed", limit=10).to_dict()
                elif mode == "semantic":
                    embedded = asyncio.run(engine.search_semantic("Installed", limit=10)).to_dict()
                else:
                    embedded = asyncio.run(engine.search_hybrid("Installed", limit=10)).to_dict()
                cli = CliRunner().invoke(
                    cli_main,
                    [
                        "--root",
                        str(root),
                        "search",
                        "Installed",
                        "--mode",
                        mode,
                        "--provider",
                        "fake",
                        "--limit",
                        "10",
                    ],
                )
                assert cli.exit_code == 0, cli.output
                assert cli.stdout.strip(), (
                    f"installed CLI emitted no stdout for {mode}: "
                    f"stderr={cli.stderr!r}, exception={cli.exception!r}"
                )
                assert json.loads(cli.stdout)["data"] == embedded
        finally:
            engine.close()
    return 3


def main() -> None:
    network_attempts = 0

    def blocked_connect(*args: object, **kwargs: object) -> None:
        nonlocal network_attempts
        network_attempts += 1
        raise AssertionError("network is forbidden in installed-package smoke")

    with patch.object(socket.socket, "connect", blocked_connect):
        symbol_count = _check_imports()
        _check_memory_core()
        _check_sqlite_candidate()
        _check_installed_audio_retrieval()
        _check_cli_json()
        parity_mode_count = _check_legacy_retrieval_parity()

    assert network_attempts == 0
    print(
        json.dumps(
            {
                "ok": True,
                "data": {
                    "version": EXPECTED_VERSION,
                    "compatibility_modules_checked": len(REGISTRY_IMPORTS),
                    "compatibility_symbols_checked": symbol_count,
                    "public_api_exports_checked": len(EXPECTED_PUBLIC_API_EXPORTS),
                    "memory_core": "passed",
                    "sqlite_candidate": "passed",
                    "installed_audio_retrieval": "passed",
                    "cli_json": "passed",
                    "legacy_retrieval_parity_modes": parity_mode_count,
                    "network_attempts": 0,
                },
                "meta": {"command": "installed-package-smoke"},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
