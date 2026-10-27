#!/usr/bin/env python3
"""Installed-artifact smoke for MDRack public and persistence contracts."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import wave
from asyncio import proactor_events as _proactor_events
from asyncio import selector_events as _selector_events
from pathlib import Path
from typing import Any
from unittest.mock import patch

EXPECTED_VERSION = "1.4.0rc3"
REGISTRY_IMPORTS = {
    "mdrack_core": (
        "ExpectedPublication", "PublicationResult", "PublicationState", "ResourceSnapshot", "PublicationPort",
    ),
    "mdrack.application.query": ("SearchService",),
    "mdrack.application.retrieval": ("RetrievalService", "HybridRetrievalService"),
    "mdrack.public_api": (
        "ReadyOCRError", "ReadyOCROperation",
        "MediaSearchRequest", "MediaSearchResult", "StoredUnitInput", "SourceResolver",
        "EmbeddingResult", "EncodingRole", "ImageInput", "InputCapability",
        "RouteCapabilities", "TextInput", "LegacyTextEmbeddingAdapter", "PreparedEmbeddingProvider",
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
        "DurableArtifactStore", "PreparedEnrichmentOperation", "PreparedReindex",
        "AudioInput", "VideoInput", "NativeMediaPolicy", "NativeMediaResult", "NativeMediaDecoder",
        "NativeTextIngestionResult",
    ),
    "mdrack_media": (
        "NativeAudioObservation", "NativeBatchInput", "NativeFrameObservation", "NativeVideoObservation",
        "build_native_audio_batch", "build_native_frame_batch", "build_native_video_batch",
    ),
    "mdrack.public_api.models": (
        "MediaSearchRequest", "MediaSearchResult", "StoredUnitInput",
        "NativeTextIngestionResult",
        "EmbeddingResult", "EncodingRole", "ImageInput", "InputCapability", "RouteCapabilities", "TextInput",
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


_LOOPBACK_SELF_PIPE_HOSTS = frozenset({"127.0.0.1", "::1"})
_FALLBACK_SOCKETPAIR_CODE = getattr(getattr(socket, "_fallback_socketpair", None), "__code__", None)
_SELF_PIPE_CODES = frozenset(
    code for code in (
        getattr(
            getattr(getattr(_proactor_events, "BaseProactorEventLoop", None), "_make_self_pipe", None),
            "__code__", None,
        ),
        getattr(
            getattr(getattr(_selector_events, "BaseSelectorEventLoop", None), "_make_self_pipe", None),
            "__code__", None,
        ),
    ) if code is not None
)


def _owned_asyncio_self_pipe_connect(sock: object, address: object) -> bool:
    """Recognize the actual loaded CPython Windows self-pipe implementation.

    The allowance is bound to the exact stdlib code objects and the immediate
    ``_fallback_socketpair`` -> ``_make_self_pipe`` call relationship, not to
    application-controlled filenames or function labels. The self-pipe frame's
    ``self`` must be a real ``asyncio.AbstractEventLoop`` instance. All other
    application connects remain blocked.
    """
    if _FALLBACK_SOCKETPAIR_CODE is None or not _SELF_PIPE_CODES:
        return False
    if not isinstance(sock, socket.socket):
        return False
    if sock.family not in {socket.AF_INET, socket.AF_INET6} or sock.type != socket.SOCK_STREAM:
        return False
    if sock.gettimeout() != 0.0:
        return False
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    if address[0] not in _LOOPBACK_SELF_PIPE_HOSTS:
        return False
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not _FALLBACK_SOCKETPAIR_CODE:
        frame = frame.f_back
    if frame is None or frame.f_back is None or frame.f_back.f_code not in _SELF_PIPE_CODES:
        return False
    loop = frame.f_back.f_locals.get("self")
    return isinstance(loop, asyncio.AbstractEventLoop)


class _MemoryCatalog:
    def __init__(self) -> None:
        self.batch: Any | None = None

    def replace_resource(self, batch: Any) -> None:
        self.batch = batch

    def delete_resource(self, resource_id: str) -> None:
        if self.batch is not None and self.batch.resource.resource_id == resource_id:
            self.batch = None


def _batch(*, space_fingerprint: str = "installed-smoke-space"):
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
        (EmbeddingSpaceRecord("space-installed-smoke", 1024, "dot", space_fingerprint, {}),),
        (VectorRecord("unit-installed-smoke", "space-installed-smoke", (1.0,) * 1024),),
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


def _check_installed_raw_text_cli() -> None:
    with tempfile.TemporaryDirectory(prefix="mdrack-installed-raw-text-") as directory:
        root = Path(directory) / "root"
        root.mkdir()
        source = Path(directory) / "outside-root.txt"
        source.write_text("installed raw needle", encoding="utf-8")
        executable = str(Path(sys.executable).with_name("mdrack"))
        ingest = subprocess.run(
            [executable, "--root", str(root), "ingest", "text", str(source), "--source-ref", "docs/installed.txt", "--media-type", "text/plain"],  # noqa: E501
            check=False,
            capture_output=True,
            text=True,
        )
        assert ingest.returncode == 0, ingest.stderr
        payload = json.loads(ingest.stdout)
        assert payload["data"]["resource_kind"] == "raw_text"
        assert str(source) not in ingest.stdout and "installed raw needle" not in ingest.stdout
        search = subprocess.run(
            [executable, "--root", str(root), "search", "needle", "--scope", "all", "--mode", "text"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert search.returncode == 0, search.stderr
        search_payload = json.loads(search.stdout)
        assert search_payload["data"]["results"][0]["resource_kind"] == "raw_text"
        locator = search_payload["data"]["results"][0]["evidence"][0]["locator"]
        assert locator["kind"] == "raw_local_source_span"
        assert str(source) not in search.stdout and "installed raw needle" not in search.stdout


def _check_installed_image_cli() -> None:
    with tempfile.TemporaryDirectory(prefix="mdrack-installed-image-") as directory:
        root = Path(directory) / "root"
        root.mkdir()
        source = Path(directory) / "outside-root"
        source.write_bytes(b"\x89PNG\r\n\x1a\ninstalled-image")
        executable = str(Path(sys.executable).with_name("mdrack"))
        init = subprocess.run(
            [executable, "--root", str(root), "init"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert init.returncode == 0, init.stderr
        ingest = subprocess.run(
            [
                executable,
                "--root",
                str(root),
                "image",
                "ingest",
                str(source),
                "--resource-id",
                "installed-image-1",
                "--source-namespace",
                "installed-smoke",
                "--source-ref",
                "images/installed.png",
                "--caption",
                "installed image needle",
                "--provider",
                "fake",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert ingest.returncode == 0, ingest.stderr
        assert str(source) not in ingest.stdout and "installed image needle" not in ingest.stdout
        search = subprocess.run(
            [executable, "--root", str(root), "image", "search", "needle", "--mode", "text"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert search.returncode == 0, search.stderr
        payload = json.loads(search.stdout)
        assert payload["data"]["results"][0]["source_ref"] == "images/installed.png"
        assert str(source) not in search.stdout and "installed image needle" not in search.stdout


def _installed_fake_python_command(
    directory: Path, payload: dict[str, object], name: str,
) -> tuple[str, dict[str, str]]:
    """Return the installed interpreter and an env that emits payload once.

    The temporary sitecustomize activates only for the no-argument child
    interpreter (``sys.argv == ['']``), never for the mdrack launcher. This is a
    checker-owned fake STT/extractor, not product source injection.
    """
    fake_site = directory / f"{name}-site"
    fake_site.mkdir()
    literal = json.dumps(payload, separators=(",", ":"))
    (fake_site / "sitecustomize.py").write_text(
        "import os, sys\n"
        "if sys.argv == ['']:\n"
        f"    sys.stdout.write({literal!r})\n"
        "    sys.stdout.flush()\n"
        "    os._exit(0)\n",
        encoding="utf-8",
    )
    assert [path.name for path in fake_site.iterdir()] == ["sitecustomize.py"]
    return sys.executable, {**os.environ, "PYTHONPATH": str(fake_site)}


def _check_installed_raw_audio_cli() -> None:
    with tempfile.TemporaryDirectory(prefix="mdrack-installed-raw-audio-") as directory:
        root = Path(directory) / "root"
        root.mkdir()
        source = Path(directory) / "outside-root.wav"
        with wave.open(str(source), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\0\0" * 8000)
        # A console .py file is not directly executable on Windows. Use the
        # installed interpreter as a checker-owned fake STT producer.
        stt_command, environment = _installed_fake_python_command(
            Path(directory),
            {"schema": "mdrack.timed-transcript.v1",
             "atoms": [{"start_ms": 0, "end_ms": 100, "text": "installed audio needle"}]},
            "fake-stt",
        )
        executable = str(Path(sys.executable).with_name("mdrack"))
        ingest = subprocess.run(
            [executable, "--root", str(root), "ingest", "audio", str(source),
             "--source-ref", "recordings/installed.wav", "--allow-external-stt",
             "--stt-command", stt_command],
            check=False, capture_output=True, text=True, encoding="utf-8",
            env=environment,
        )
        assert ingest.returncode == 0, ingest.stderr
        payload = json.loads(ingest.stdout)
        assert payload["data"]["resource_kind"] == "audio"
        assert str(source) not in ingest.stdout
        search = subprocess.run(
            [executable, "--root", str(root), "search", "needle", "--scope", "audio", "--mode", "text"],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        assert search.returncode == 0, search.stderr
        search_payload = json.loads(search.stdout)
        assert search_payload["data"]["results"]
        assert str(source) not in search.stdout and "installed audio needle" not in search.stdout


def _check_installed_raw_video_cli() -> None:
    with tempfile.TemporaryDirectory(prefix="mdrack-installed-raw-video-") as directory:
        root = Path(directory) / "root"
        root.mkdir()
        source = Path(directory) / "outside-root.mp4"
        source.write_bytes(b"0000ftypisompayload")
        extractor_command, environment = _installed_fake_python_command(
            Path(directory),
            {"schema": "mdrack.raw-video-extraction.v1", "media_type": "video/mp4",
             "duration_ms": 1000,
             "transcript": {"schema": "mdrack.timed-transcript.v1", "duration_ms": 1000,
                            "atoms": [{"start_ms": 0, "end_ms": 100, "text": "installed video needle"}]},
             "frames": [{"timestamp_ms": 50, "caption": "installed frame"}]},
            "fake-video",
        )
        executable = str(Path(sys.executable).with_name("mdrack"))
        ingest = subprocess.run(
            [executable, "--root", str(root), "ingest", "raw-video", str(source),
             "--source-ref", "videos/installed.mp4", "--allow-external-video-extractor",
             "--video-extractor-command", extractor_command],
            check=False, capture_output=True, text=True, encoding="utf-8",
            env=environment,
        )
        assert ingest.returncode == 0, ingest.stderr
        payload = json.loads(ingest.stdout)
        assert payload["data"]["resource_kind"] == "video"
        assert str(source) not in ingest.stdout
        search = subprocess.run(
            [executable, "--root", str(root), "search", "needle", "--scope", "video", "--mode", "text"],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        assert search.returncode == 0, search.stderr
        assert json.loads(search.stdout)["data"]["results"]
        assert str(source) not in search.stdout and "installed video needle" not in search.stdout


def _check_catalog_retrieval_parity() -> int:
    from click.testing import CliRunner

    from mdrack.cli import main as cli_main
    from mdrack.config.models import MDRackConfig
    from mdrack.embeddings.fake import FakeEmbeddingProvider
    from mdrack.embeddings.runtime import embedding_profile_from_config
    from mdrack.public_api import MDRackEngine

    with tempfile.TemporaryDirectory(prefix="mdrack-installed-parity-") as directory:
        root = Path(directory)
        from mdrack_sqlite import SQLiteCatalog

        store = root / ".mdrack"
        store.mkdir()
        catalog = SQLiteCatalog.create_v2(store / "catalog.sqlite3")
        provider = FakeEmbeddingProvider(dimensions=1024)
        config = MDRackConfig()
        profile = embedding_profile_from_config(config, provider, "default")
        batch = _batch(space_fingerprint=profile.fingerprint)
        catalog.replace_resource(batch)
        catalog.close()

        engine = MDRackEngine(root=root, config=config, embedding_provider=provider)
        try:
            for mode in ("text", "semantic", "hybrid"):
                if mode == "text":
                    embedded = engine.search_text("installed", limit=10).to_dict()
                elif mode == "semantic":
                    embedded = asyncio.run(engine.search_semantic("installed", limit=10)).to_dict()
                else:
                    embedded = asyncio.run(engine.search_hybrid("installed", limit=10)).to_dict()
                cli = CliRunner().invoke(
                    cli_main,
                    [
                        "--root",
                        str(root),
                        "search",
                        "installed",
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
                assert json.loads(cli.stdout).get("data") == embedded, cli.stdout
        finally:
            engine.close()
    return 3


def _check_installed_native_image() -> None:
    """Native source/reopen/parity on installed bytes, not a native model claim."""
    import struct
    import zlib

    from mdrack.config.models import MDRackConfig
    from mdrack.embeddings.synthetic import SyntheticPreparedProvider
    from mdrack.public_api import ImageInput, MDRackEngine, MediaSearchRequest
    from mdrack_core import SearchScope

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    content = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))
    with tempfile.TemporaryDirectory(prefix='mdrack-native-installed-') as directory:
        root = Path(directory)
        source = root / 'source.png'
        source.write_bytes(content)
        command = [sys.executable, '-B', '-m', 'mdrack', '--root', str(root)]
        result = subprocess.run(command + ['image', 'native-ingest', str(source), '--resource-id', 'image',
                                '--source-namespace', 'host', '--source-ref', 'source.png',
                                '--provider', 'synthetic'], cwd=root, capture_output=True, text=True, check=True)
        unit_id = json.loads(result.stdout)['data']['unit_ids'][0]
        with MDRackEngine(root=root, config=MDRackConfig(),
                          prepared_embedding_provider=SyntheticPreparedProvider()) as engine:
            assert engine.read_unit(unit_id)['text'] is None
            request = MediaSearchRequest(ImageInput(content, 'image/png', 'query'),
                                         SearchScope(modalities=('image',)))
            expected = asyncio.run(engine.search_media(request)).to_dict()
            assert engine._storage is None
        child = subprocess.run(command + ['image', 'native-search', '--image-file', str(source),
                               '--provider', 'synthetic'], cwd=root, capture_output=True, text=True, check=True)
        assert json.loads(child.stdout)['data'] == expected
        assert source.read_bytes() == content


def _check_installed_native_text_cli() -> None:
    """Installed ordinary-document route with the explicit synthetic provider only."""
    with tempfile.TemporaryDirectory(prefix='mdrack-native-text-installed-') as directory:
        root = Path(directory) / 'root'
        root.mkdir()
        source = Path(directory) / 'outside-root.md'
        source.write_text('# Installed\n\nordinary installed needle with ёж\n', encoding='utf-8')
        executable = str(Path(sys.executable).with_name('mdrack'))
        command = [executable, '--root', str(root)]
        ingest = subprocess.run(
            command + ['native', 'text-ingest', str(source), '--source-namespace', 'installed',
                       '--source-ref', 'docs/installed.md', '--provider', 'synthetic'],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        assert ingest.returncode == 0, ingest.stderr + ingest.stdout
        data = json.loads(ingest.stdout)['data']
        assert data['schema'] == 'mdrack.native-text-ingest.v1' and data['unit_ids']
        assert str(source) not in ingest.stdout and 'ordinary installed needle' not in ingest.stdout
        repeat = subprocess.run(
            command + ['native', 'text-ingest', str(source), '--source-namespace', 'installed',
                       '--source-ref', 'docs/installed.md', '--provider', 'synthetic'],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        assert repeat.returncode == 0, repeat.stderr + repeat.stdout
        repeated = json.loads(repeat.stdout)['data']
        assert repeated['resource_id'] == data['resource_id']
        assert repeated['unit_ids'] == data['unit_ids']
        assert repeated['model_call_count'] == 0 and repeated['unchanged'] is True
        query = subprocess.run(
            command + ['native', 'text-search', '--text', 'needle', '--candidates', 'text',
                       '--provider', 'synthetic', '--mode', 'semantic'],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        assert query.returncode == 0, query.stderr + query.stdout
        found = json.loads(query.stdout)['data']
        assert found['results'] and found['results'][0]['resource_id'] == data['resource_id']
        assert found['space_id'] == data['space_id']
        assert str(source) not in query.stdout
        read = subprocess.run(command + ['read', 'unit', data['unit_ids'][0]], check=False,
                              capture_output=True, text=True, encoding="utf-8")
        assert read.returncode == 0, read.stderr + read.stdout
        assert 'ёж' in read.stdout


def main() -> None:
    network_attempts = 0
    self_pipe_connects = 0
    original_connect = socket.socket.connect

    def blocked_connect(*args: object, **kwargs: object) -> None:
        nonlocal network_attempts, self_pipe_connects
        if len(args) >= 2 and _owned_asyncio_self_pipe_connect(args[0], args[1]):
            self_pipe_connects += 1
            return original_connect(*args, **kwargs)
        network_attempts += 1
        raise AssertionError("network is forbidden in installed-package smoke")

    with patch.object(socket.socket, "connect", blocked_connect):
        symbol_count = _check_imports()
        _check_memory_core()
        _check_sqlite_candidate()
        _check_installed_audio_retrieval()
        _check_cli_json()
        _check_installed_raw_text_cli()
        _check_installed_image_cli()
        _check_installed_native_image()
        _check_installed_native_text_cli()
        _check_installed_raw_audio_cli()
        _check_installed_raw_video_cli()
        parity_mode_count = _check_catalog_retrieval_parity()

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
                    "installed_raw_text_cli": "passed",
                    "installed_image_cli": "passed",
                    "installed_native_image_contract": "passed",
                    "installed_native_text_contract": "passed",
                    "installed_raw_audio_cli": "passed",
                    "installed_raw_video_cli": "passed",
                    "legacy_retrieval_parity_modes": parity_mode_count,
                    "network_attempts": 0,
                    "owned_self_pipe_connects": self_pipe_connects,
                },
                "meta": {"command": "installed-package-smoke"},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
