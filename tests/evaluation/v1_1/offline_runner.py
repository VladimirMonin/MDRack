"""Deterministic MDRack 1.1 offline application-stack evaluation.

The runner consumes the frozen public F1 inputs, creates disposable SQLite
catalogs, and exercises production facades.  Vectors are an explicit graded,
provider-free fixture; they prove retrieval plumbing, not semantic model quality.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import math
import re
import socket
import tempfile
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from mdrack.application.manifest import encode_prepared_resource_manifest
from mdrack.application.metadata_filters import MetadataFilter, MetadataFilters
from mdrack.application.resource_catalog import PreparedResourceCatalog
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    StoreGeneration,
)
from mdrack.config.models import (
    EmbeddingConfig,
    MDRackConfig,
    MetadataConfig,
    MetadataProjectionConfig,
    PathsConfig,
)
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.eval.privacy import (
    PrivacyViolation,
    scan_json_text,
    scan_privacy,
    serialize_safe_json,
)
from mdrack.eval.quality import (
    QualityCase,
    QualityJudgment,
    QualityUnit,
    evaluate_quality,
)
from mdrack.ports.embeddings import EmbeddingError
from mdrack.ports.storage import KnowledgeStorage
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)
from mdrack_core import (
    TARGET_UNIT,
    EmbeddingSpaceRecord,
    Facet,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchRequest,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_media import ProducerFingerprint, resource_id
from mdrack_sqlite import SQLiteCatalog

ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = ROOT / "tests/evaluation/corpus-v1/manifest.json"
QUERIES_PATH = ROOT / "tests/evaluation/queries-v1/queries.json"
BENCHMARK_PATH = ROOT / "tests/evaluation/benchmark-v1/manifest.json"
FREEZE_PATH = ROOT / "tests/evaluation/v1_1/freeze-manifest.json"
CONFIG_PATH = ROOT / "configs/eval-v11.toml"
SENTINELS_PATH = ROOT / "tests/privacy/v1_1/sentinels.json"
RUNTIME_CONTRACT_PATH = ROOT / "tests/evaluation/v1_1/runtime-contract.json"
SPACE_ID = "v11-offline-static-text-space"
SPACE_FINGERPRINT = "sha256:" + hashlib.sha256(
    b"v11-offline-static-text-space-v1"
).hexdigest()

_RUNTIME_CONTRACT = cast(
    dict[str, Any],
    json.loads(RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8")),
)
HYBRID_PROFILES: tuple[tuple[str, float, float], ...] = tuple(
    (
        name,
        float(weights["lexical_weight"]),
        float(weights["semantic_weight"]),
    )
    for name, weights in cast(
        dict[str, dict[str, float]],
        _RUNTIME_CONTRACT["required_hybrid_profiles"],
    ).items()
)
PRIVACY_SURFACES = frozenset(
    cast(list[str], _RUNTIME_CONTRACT["required_privacy_surfaces"])
)
HYBRID_METRIC_KEYS = frozenset(
    cast(list[str], _RUNTIME_CONTRACT["required_hybrid_metric_keys"])
)
_HYBRID_PROFILE_WEIGHTS = {
    name: (lexical_weight, semantic_weight)
    for name, lexical_weight, semantic_weight in HYBRID_PROFILES
}
_HYBRID_CELL_KEYS = frozenset({"lexical_weight", "semantic_weight", "metrics"})
_PRIVACY_ENTRY_KEYS = frozenset(
    {"surface", "captured", "payload_type", "violations"}
)


class OfflineEvaluationError(RuntimeError):
    """A payload-free, fail-closed Q1 evaluation error."""


@dataclass
class PrivacyCaptureLedger:
    """Privacy results derived from values captured at Q1 evidence boundaries."""

    sentinels: Mapping[str, Any]
    entries: dict[str, dict[str, object]]

    @classmethod
    def create(cls) -> PrivacyCaptureLedger:
        return cls(_json(SENTINELS_PATH), {})

    @classmethod
    def from_report(
        cls,
        report: Mapping[str, Any],
        *,
        require_complete: bool = False,
    ) -> PrivacyCaptureLedger:
        ledger = cls.create()
        privacy = report.get("privacy")
        if not isinstance(privacy, dict):
            raise OfflineEvaluationError("Q1 report has no privacy evidence")
        stored = privacy.get("capture_ledger", [])
        if not isinstance(stored, list):
            raise OfflineEvaluationError("Q1 privacy ledger is invalid")
        for entry in stored:
            if not isinstance(entry, dict):
                raise OfflineEvaluationError("Q1 privacy ledger entry is invalid")
            surface = entry.get("surface")
            if (
                set(entry) != _PRIVACY_ENTRY_KEYS
                or
                not isinstance(surface, str)
                or surface not in PRIVACY_SURFACES
                or surface in ledger.entries
                or entry.get("captured") is not True
                or type(entry.get("violations")) is not int
                or entry.get("violations") != 0
                or entry.get("payload_type") not in {"json", "text"}
            ):
                raise OfflineEvaluationError("Q1 privacy ledger entry failed validation")
            ledger.entries[surface] = dict(entry)
        if require_complete and set(ledger.entries) != PRIVACY_SURFACES:
            raise OfflineEvaluationError("Q1 privacy ledger is incomplete")
        expected_surfaces = sorted(ledger.entries)
        if privacy.get("surfaces_checked") != expected_surfaces:
            raise OfflineEvaluationError("Q1 privacy surface summary is inconsistent")
        if type(privacy.get("violations")) is not int or privacy.get("violations") != 0:
            raise OfflineEvaluationError("Q1 privacy violation summary is inconsistent")
        return ledger

    def capture(self, surface: str, payload: object) -> None:
        if surface not in PRIVACY_SURFACES:
            raise OfflineEvaluationError("unknown Q1 privacy surface")
        forbidden_values = list(
            cast(dict[str, str], self.sentinels["forbidden_values"]).values()
        )
        forbidden_keys = cast(list[str], self.sentinels["forbidden_keys"])
        result = scan_privacy(
            payload,
            forbidden_values=forbidden_values,
            forbidden_keys=forbidden_keys,
        )
        serialized = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, sort_keys=True
        )
        violations = result.findings_count + len(
            _privacy_violations(serialized, self.sentinels)
        )
        if violations:
            raise OfflineEvaluationError("privacy sentinel detected in evidence surface")
        self.entries[surface] = {
            "surface": surface,
            "captured": True,
            "payload_type": "text" if isinstance(payload, str) else "json",
            "violations": 0,
        }

    def apply(self, report: dict[str, Any]) -> None:
        privacy = cast(dict[str, Any], report["privacy"])
        ordered = [self.entries[name] for name in sorted(self.entries)]
        privacy["capture_ledger"] = ordered
        privacy["surfaces_checked"] = [entry["surface"] for entry in ordered]
        privacy["violations"] = sum(cast(int, entry["violations"]) for entry in ordered)


def capture_runtime_surfaces(
    *,
    api: object,
    provider: object,
    evaluation: object,
    logs: str,
) -> PrivacyCaptureLedger:
    """Capture the four in-process Q1 evidence boundaries through one scanner."""
    ledger = PrivacyCaptureLedger.create()
    ledger.capture("api", api)
    ledger.capture("provider", provider)
    ledger.capture("eval", evaluation)
    ledger.capture("log", logs)
    return ledger


def capture_cli_transport(
    payload: dict[str, Any],
    *,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    """Capture both subprocess channels before accepting the CLI payload."""
    ledger = PrivacyCaptureLedger.from_report(payload)
    ledger.capture("cli_stdout", stdout)
    ledger.capture("cli_stderr", stderr)
    ledger.apply(payload)
    return payload


class _EngineStorage:
    def __init__(self, catalog: SQLiteCatalog, *, close_catalog: bool = True) -> None:
        self.resource_store = catalog
        self._close_catalog = close_catalog

    def close(self) -> None:
        if self._close_catalog:
            self.resource_store.close()


class _ConceptProvider(FakeEmbeddingProvider):
    """Small deterministic scenario provider with no network capability."""

    def __init__(self) -> None:
        super().__init__(dimensions=4, provider_name="v11-static-fixture")

    def _text_to_vector(self, text: str) -> list[float]:
        lowered = text.casefold()
        if any(token in lowered for token in ("transaction", "atomic boundary", "unit of work")):
            return [1.0, 0.0, 0.0, 0.0]
        if any(token in lowered for token in ("architecture diagram", "visual structure", "frame")):
            return [0.0, 1.0, 0.0, 0.0]
        if "privacy" in lowered:
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]


class _FailingQueryProvider(_ConceptProvider):
    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        del text, profile
        raise EmbeddingError("offline_fixture_failure")


@dataclass(frozen=True)
class _FrozenVectorOracle:
    dimensions: int
    unit_vectors: Mapping[str, tuple[float, ...]]
    query_vectors: Mapping[str, tuple[float, ...]]


class _NetworkGuard:
    def __init__(self) -> None:
        self.attempts = 0
        self._stack = contextlib.ExitStack()

    def _blocked(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.attempts += 1
        raise OfflineEvaluationError("network access attempted")

    def __enter__(self) -> _NetworkGuard:
        self._stack.enter_context(patch.object(socket, "create_connection", self._blocked))
        self._stack.enter_context(patch.object(socket, "getaddrinfo", self._blocked))
        self._stack.enter_context(patch.object(socket, "gethostbyname", self._blocked))
        self._stack.enter_context(patch.object(socket, "gethostbyname_ex", self._blocked))
        self._stack.enter_context(patch.object(socket, "gethostbyaddr", self._blocked))
        self._stack.enter_context(patch.object(socket.socket, "connect", self._blocked))
        self._stack.enter_context(patch.object(socket.socket, "connect_ex", self._blocked))
        self._stack.enter_context(patch.object(socket.socket, "sendto", self._blocked))
        if hasattr(socket.socket, "sendmsg"):
            self._stack.enter_context(patch.object(socket.socket, "sendmsg", self._blocked))
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stack.close()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OfflineEvaluationError("evaluation input root is not an object")
    return cast(dict[str, Any], value)


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_value(value: object) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _implementation_ref() -> str:
    digest = hashlib.sha256()
    paths = sorted((ROOT / "src").rglob("*.py"))
    for package in sorted((ROOT / "packages").glob("*/src")):
        paths.extend(sorted(package.rglob("*.py")))
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _frozen_source_hashes() -> dict[str, str]:
    freeze = _json(FREEZE_PATH)
    expected = freeze.get("input_sha256")
    if not isinstance(expected, dict):
        raise OfflineEvaluationError("freeze manifest has no input digest map")
    actual: dict[str, str] = {}
    for relative, pinned in sorted(expected.items()):
        if not isinstance(relative, str) or not isinstance(pinned, str):
            raise OfflineEvaluationError("freeze digest entry is invalid")
        digest = _digest_bytes((ROOT / relative).read_bytes())
        if digest != pinned:
            raise OfflineEvaluationError("frozen input digest mismatch")
        actual[relative] = digest
    return actual


def _validate_config(path: Path) -> dict[str, Any]:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    if config.get("phase") != "input_freeze":
        raise OfflineEvaluationError("F1 input-freeze phase changed")
    if config.get("candidate_results_allowed") is not False:
        raise OfflineEvaluationError("F1 candidate-output policy changed")
    if config.get("network_allowed") is not False:
        raise OfflineEvaluationError("offline config permits network")
    if config.get("private_corpus_allowed") is not False:
        raise OfflineEvaluationError("offline config permits private corpus")
    if config.get("provider") != "deterministic_static":
        raise OfflineEvaluationError("offline config provider is not static")
    execution = config.get("execution")
    if not isinstance(execution, dict) or execution.get("repeats") != 2:
        raise OfflineEvaluationError("offline config must require two repeats")
    if execution.get("fresh_disposable_catalog_each_run") is not True:
        raise OfflineEvaluationError("offline config must require disposable catalogs")
    return config


def _ready_generation(store_dir: Path) -> None:
    generation_id = "g-v11-q1"
    generations = store_dir / "generations"
    generations.mkdir(parents=True)
    database_path = generations / f"generation-{generation_id}.sqlite3"
    connection = get_connection(database_path)
    apply_candidate_migrations(connection, get_migrations_dir())
    connection.close()
    generation = StoreGeneration(
        generation_id=generation_id,
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
        migration_manifest_digest=EXPECTED_MIGRATION_MANIFEST_DIGEST,
        schema_version=EXPECTED_MIGRATION_VERSION,
        state=GenerationState.READY,
        created_at="2026-07-21T00:00:00+00:00",
        verified_at="2026-07-21T00:00:01+00:00",
    )
    (generations / f"generation-{generation_id}.json").write_bytes(generation.to_bytes())
    (store_dir / "active-generation.json").write_bytes(
        ActiveGenerationPointer(generation_id, GenerationContractKind.RESOURCE_CORE_V1).to_bytes()
    )


def _scenario_a(workspace: Path, sentinel: str) -> dict[str, object]:
    root = workspace / "metadata-root"
    root.mkdir()
    store = workspace / "metadata-store"
    _ready_generation(store)
    source = root / "note.md"
    source.write_text(
        "---\n"
        "title: Offline metadata\n"
        "search_title: Offline metadata\n"
        "aliases: [rare-offline-alias]\n"
        "tags: [offline]\n"
        "enabled: true\n"
        "priority: 3\n"
        f"private_note: {sentinel}\n"
        "nested:\n  kind: synthetic\n"
        "---\n# Searchable body\n\nOrdinary body marker for Q1.\n",
        encoding="utf-8",
    )
    source_hash = _digest_bytes(source.read_bytes())
    projections = [
        MetadataProjectionConfig(path="/title", mode="canonical_title"),
        MetadataProjectionConfig(path="/search_title", mode="lexical_text"),
        MetadataProjectionConfig(path="/aliases", mode="lexical_text"),
        MetadataProjectionConfig(path="/tags", mode="facet_many", namespace="tag"),
        MetadataProjectionConfig(path="/enabled", mode="facet", namespace="enabled"),
        MetadataProjectionConfig(path="/priority", mode="facet", namespace="priority"),
        MetadataProjectionConfig(path="/private_note", mode="store_only"),
    ]
    config = MDRackConfig(
        paths=PathsConfig(root=".", store=str(store)),
        metadata=MetadataConfig(projections=projections),
    )
    engine = MDRackEngine(root=root, config=config)
    try:
        indexed = engine.scan(force_reindex=True)
        body = engine.search_resources_text("ordinary body marker").to_dict()
        alias = engine.search_resources_text("rare-offline-alias").to_dict()
        title = engine.search_resources_text("Offline metadata").to_dict()
        body_only_alias = engine.search_resources_text(
            "rare-offline-alias",
            body_weight=1.0,
            metadata_weight=0.0,
        ).to_dict()
        metadata_only_alias = engine.search_resources_text(
            "rare-offline-alias",
            body_weight=0.0,
            metadata_weight=1.0,
        ).to_dict()
        store_only = engine.search_resources_text(sentinel).to_dict()
        filters = MetadataFilters(
            all=(
                MetadataFilter("tag", "offline"),
                MetadataFilter("enabled", True),
                MetadataFilter("priority", 3),
            )
        )
        filtered = engine.search_resources_text(
            "ordinary body marker",
            metadata_filters=filters,
        ).to_dict()
        resource_id_value = cast(list[dict[str, object]], filtered["results"])[0]["resource_id"]
        if not isinstance(resource_id_value, str):
            raise OfflineEvaluationError("metadata scenario returned invalid resource identity")
        metadata = engine.get_resource_metadata(resource_id_value).to_dict()
        exported = engine.export_resource_manifest(resource_id_value)
    finally:
        engine.close()

    restored_path = workspace / "metadata-restored.sqlite3"
    SQLiteCatalog.create(restored_path).close()
    restored_catalog = SQLiteCatalog.open(restored_path)
    restored = MDRackEngine(
        root=workspace,
        config=MDRackConfig(),
        storage=cast(KnowledgeStorage, _EngineStorage(restored_catalog)),
    )
    try:
        restored.import_resource_manifest(exported)
        restored_result = restored.search_resources_text(
            "ordinary body marker",
            metadata_filters=filters,
        ).to_dict()
    finally:
        restored.close()

    if indexed.status != "success":
        raise OfflineEvaluationError("metadata scenario indexing failed")
    if not body["results"] or not alias["results"] or not filtered["results"]:
        raise OfflineEvaluationError("metadata scenario search failed")
    if not title["results"] or body_only_alias["results"]:
        raise OfflineEvaluationError("metadata projection ablation failed")
    if not metadata_only_alias["results"] or store_only["results"]:
        raise OfflineEvaluationError("metadata allowlist ablation failed")
    if filtered != restored_result:
        raise OfflineEvaluationError("metadata export/import changed logical results")
    source_values = cast(dict[str, object], metadata["source"])
    if source_values.get("private_note") != sentinel:
        raise OfflineEvaluationError("metadata source round-trip failed")
    if _digest_bytes(source.read_bytes()) != source_hash:
        raise OfflineEvaluationError("metadata source changed")
    return {
        "body_hit": True,
        "alias_hit": True,
        "typed_filter_hit": True,
        "metadata_round_trip": True,
        "manifest_round_trip": True,
        "source_unchanged": True,
        "resource_count": 1,
        "metadata_ablations": {
            "body_only": "body_hit_alias_miss",
            "body_title": "title_hit",
            "body_title_aliases": "alias_hit",
            "selected_metadata_text": "alias_hit_store_only_miss",
        },
    }


def _transcript_bytes(resource: str, phrases: Sequence[tuple[int, int, str]]) -> bytes:
    return _canonical_bytes(
        {
            "schema": "mdrack.timed-transcript.v1",
            "resource_ref": resource,
            "language": "en",
            "producer_fingerprint": ProducerFingerprint.from_payload(
                {"producer": "q1-static-transcript"}
            ).value,
            "atoms": [
                {
                    "atom_id": f"a-{index}",
                    "start_ms": start,
                    "end_ms": end,
                    "text": text,
                    "speaker": None,
                    "confidence": 1.0,
                    "timing_precision": "segment",
                }
                for index, (start, end, text) in enumerate(phrases)
            ],
        }
    )


def _read_timed_artifact(payload: bytes, resource: str):
    from mdrack.ingestion.transcripts import read_transcript

    return read_transcript(
        payload,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload(
            {"producer": "q1-static-transcript"}
        ),
    ).artifact


def _scenario_b(workspace: Path) -> dict[str, object]:
    database = workspace / "audio.sqlite3"
    catalog = SQLiteCatalog.create(database)
    provider = _ConceptProvider()
    engine = MDRackEngine(
        root=workspace,
        config=MDRackConfig(embedding=EmbeddingConfig(dimensions=4)),
        embedding_provider=provider,
        storage=cast(KnowledgeStorage, _EngineStorage(catalog, close_catalog=False)),
    )
    first_id = resource_id("q1", "audio-one")
    second_id = resource_id("q1", "audio-two")
    other_id = resource_id("q1", "audio-other")
    try:
        first = _read_timed_artifact(
            _transcript_bytes(
                first_id,
                ((0, 20_000, "transaction boundary keeps an atomic write"), (20_000, 40_000, "closing")),
            ),
            first_id,
        )
        second = _read_timed_artifact(
            _transcript_bytes(second_id, ((0, 30_000, "unit of work transaction boundary"),)),
            second_id,
        )
        other = _read_timed_artifact(
            _transcript_bytes(other_id, ((0, 30_000, "unrelated color palette"),)),
            other_id,
        )
        for artifact in (first, second, other):
            asyncio.run(
                engine.ingest_transcript(
                    artifact,
                    resource_kind="audio",
                    media_type="audio/wav",
                    source_namespace="q1-public",
                    source_locator=Locator("external_record", {"ref": artifact.resource_id}),
                )
            )
        lexical = asyncio.run(engine.search_transcripts("transaction boundary", mode="text"))
        semantic = asyncio.run(engine.search_transcripts("atomic boundary", mode="semantic"))
        hybrid = asyncio.run(engine.search_transcripts("transaction boundary", mode="hybrid"))
        exported = json.loads(engine.export_resource_manifest(first_id))
        whole = next(
            item
            for item in exported["units"]
            if item["unit_kind"] == "whole_resource"
        )
        space = exported["spaces"][0]
        similar = engine.find_textually_similar_resources(
            whole["unit_id"],
            space["space_id"],
            aggregation=whole["metadata"]["aggregation"],
            expected_fingerprint=space["fingerprint"],
            limit=2,
        )
    finally:
        engine.close()
        catalog.close()
    if not lexical.results or not semantic.results or not hybrid.results:
        raise OfflineEvaluationError("audio scenario search failed")
    evidence = lexical.results[0].evidence[0]
    expected_interval = {
        first_id: (0, 20_000),
        second_id: (0, 30_000),
    }.get(lexical.results[0].resource_id)
    if expected_interval is None or (evidence.start_ms, evidence.end_ms) != expected_interval:
        raise OfflineEvaluationError("audio scenario timestamps are incorrect")
    if not similar.results or similar.results[0].resource_id != second_id:
        raise OfflineEvaluationError("audio textual similarity failed")
    return {
        "lexical_hit": True,
        "semantic_fixture_hit": True,
        "hybrid_hit": True,
        "timestamps_valid": True,
        "textual_similarity_hit": True,
        "fake_vector_boundary": "deterministic_static_fixture_not_semantic_quality",
    }


def _frame_artifact(resource: str):
    from mdrack.ingestion.frame_captions import read_frame_captions

    producer = ProducerFingerprint.from_payload({"producer": "q1-static-frame"})
    payload = _canonical_bytes(
        {
            "schema": "mdrack.frame-captions.v1",
            "resource_id": resource,
            "producer_fingerprint": producer.value,
            "normalization_fingerprint": None,
            "metadata": {},
            "frames": [
                {
                    "frame_id": "diagram",
                    "timestamp_ms": 15_000,
                    "caption": "unique architecture diagram",
                    "metadata": {},
                },
                {
                    "frame_id": "closing",
                    "timestamp_ms": 35_000,
                    "caption": "closing title frame",
                    "metadata": {},
                },
            ],
        }
    )
    return read_frame_captions(payload).artifact


def _scenario_c(workspace: Path) -> dict[str, object]:
    database = workspace / "video.sqlite3"
    catalog = SQLiteCatalog.create(database)
    provider = _ConceptProvider()
    engine = MDRackEngine(
        root=workspace,
        config=MDRackConfig(embedding=EmbeddingConfig(dimensions=4)),
        embedding_provider=provider,
        storage=cast(KnowledgeStorage, _EngineStorage(catalog, close_catalog=False)),
    )
    video_id = resource_id("q1", "video-one")
    try:
        transcript = _read_timed_artifact(
            _transcript_bytes(
                video_id,
                (
                    (0, 20_000, "transaction boundary speech"),
                    (20_000, 40_000, "closing speech"),
                ),
            ),
            video_id,
        )
        result = asyncio.run(
            engine.ingest_video(
                transcript,
                _frame_artifact(video_id),
                media_type="video/mp4",
                source_namespace="q1-public",
                source_locator=Locator("external_record", {"ref": video_id}),
                source_metadata={"kind": "synthetic"},
                title="Q1 synthetic video",
            )
        )
        speech = asyncio.run(
            engine.search_resource_content("transaction boundary", preset="speech_first", mode="text")
        )
        frames = asyncio.run(
            engine.search_resource_content("architecture diagram", preset="frames_first", mode="text")
        )
        balanced = asyncio.run(
            engine.search_resource_content("architecture diagram", preset="balanced", mode="hybrid")
        )
    finally:
        engine.close()
        catalog.close()
    transcript_only_path = workspace / "video-transcript-only.sqlite3"
    transcript_only_catalog = SQLiteCatalog.create(transcript_only_path)
    transcript_only_engine = MDRackEngine(
        root=workspace,
        config=MDRackConfig(embedding=EmbeddingConfig(dimensions=4)),
        embedding_provider=_ConceptProvider(),
        storage=cast(
            KnowledgeStorage,
            _EngineStorage(transcript_only_catalog, close_catalog=False),
        ),
    )
    try:
        asyncio.run(
            transcript_only_engine.ingest_transcript(
                transcript,
                resource_kind="video",
                media_type="video/mp4",
                source_namespace="q1-public",
                source_locator=Locator("external_record", {"ref": video_id}),
            )
        )
        transcript_only_search = asyncio.run(
            transcript_only_engine.search_resource_content(
                "transaction boundary",
                preset="speech_first",
                mode="text",
            )
        )
        transcript_only_manifest = json.loads(
            transcript_only_engine.export_resource_manifest(video_id)
        )
    finally:
        transcript_only_engine.close()
        transcript_only_catalog.close()
    if result.transcript_unit_count < 1 or result.frame_unit_count != 2:
        raise OfflineEvaluationError("video scenario graph is incomplete")
    if not speech.results or not frames.results or not balanced.results:
        raise OfflineEvaluationError("video scenario search failed")
    frame_evidence = frames.results[0].evidence
    if not any(
        item.locator.get("kind") == "video_frame"
        and cast(dict[str, object], item.locator.get("payload", {})).get("timestamp_ms") == 15_000
        for item in frame_evidence
    ):
        raise OfflineEvaluationError("video frame timestamp evidence is missing")
    if len({item.resource_id for item in frames.results}) != len(frames.results):
        raise OfflineEvaluationError("video resource grouping failed")
    if not transcript_only_search.results or any(
        unit["unit_kind"] == "frame" for unit in transcript_only_manifest["units"]
    ):
        raise OfflineEvaluationError("transcript-only frame ablation failed")
    return {
        "speech_hit": True,
        "frame_hit": True,
        "presets_compared": ["balanced", "frames_first", "speech_first"],
        "resource_grouping": True,
        "frame_timestamp_valid": True,
        "frame_ablations": {
            "transcript_only": "speech_hit_zero_frame_units",
            "speech_first": "speech_hit_with_frame_branch_present",
            "balanced": "frame_hit_with_speech_branch_present",
        },
    }


def _degradation_batch(resource: str, *, tag: str, sentinel: str) -> PreparedResourceBatch:
    representation = f"rep-{resource}"
    unit = f"unit-{resource}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource,
            "video",
            "video/mp4",
            "q1-public",
            Locator("external_record", {"ref": resource}),
            metadata={"source": {"private": sentinel}},
        ),
        (RepresentationRecord(representation, resource, "timed_passage", "text", "ordinary lexical fallback"),),
        (
            SearchUnitRecord(
                unit,
                resource,
                representation,
                "time_segment",
                "text",
                "ordinary lexical fallback",
                Locator("time_segment", {"start_ms": 0, "end_ms": 1_000, "track": "video"}),
                0,
            ),
        ),
        facets=(ResourceFacet(resource, Facet("tag", f"s:{tag}"), "source"),),
    )


def _scenario_d(workspace: Path, sentinel: str) -> dict[str, object]:
    database = workspace / "degradation.sqlite3"
    catalog = SQLiteCatalog.create(database)
    good = MDRackEngine(
        root=workspace,
        config=MDRackConfig(embedding=EmbeddingConfig(dimensions=4)),
        embedding_provider=_ConceptProvider(),
        storage=cast(KnowledgeStorage, _EngineStorage(catalog, close_catalog=False)),
    )
    try:
        for suffix, tag in (("keep", "keep"), ("drop", "drop")):
            batch = _degradation_batch(f"q1-degradation-{suffix}", tag=tag, sentinel=sentinel)
            good.import_resource_manifest(encode_prepared_resource_manifest(batch))
        failing = MDRackEngine(
            root=workspace,
            config=MDRackConfig(embedding=EmbeddingConfig(dimensions=4)),
            embedding_provider=_FailingQueryProvider(),
            storage=cast(KnowledgeStorage, _EngineStorage(catalog, close_catalog=False)),
        )
        try:
            result = asyncio.run(
                failing.search_resource_content(
                    "ordinary lexical fallback",
                    preset="balanced",
                    mode="hybrid",
                    metadata_filters=MetadataFilters(
                        all=(MetadataFilter("tag", "keep"),)
                    ),
                    limit=5,
                )
            )
        finally:
            failing.close()
    finally:
        good.close()
        catalog.close()
    if not result.degraded or result.degraded_reason != "embedding_provider_error":
        raise OfflineEvaluationError("degradation scenario did not report provider failure")
    if [item.resource_id for item in result.results] != ["q1-degradation-keep"]:
        raise OfflineEvaluationError("degradation scenario did not preserve filtered lexical results")
    return {
        "semantic_failure_injected": True,
        "hybrid_lexical_fallback": True,
        "metadata_filter_preserved": True,
        "degraded_reason": "embedding_provider_error",
    }


def _static_text_vector(text: str, dimensions: int = 128) -> tuple[float, ...]:
    """Hash public text into a deterministic vector without using judgments."""
    values = [0.0] * dimensions
    for token in re.findall(r"[a-z0-9]+", text.casefold()):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        values[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        return tuple(values)
    return tuple(value / norm for value in values)


def _build_oracle(
    corpus: Mapping[str, Any],
    queries: Mapping[str, Any],
) -> _FrozenVectorOracle:
    dimensions = 128
    units: dict[str, tuple[float, ...]] = {}
    for resource in cast(list[dict[str, Any]], corpus["resources"]):
        texts = _resource_texts(resource, CORPUS_PATH.parent / resource["artifact_ref"])
        offsets: dict[str, int] = {}
        for frozen_unit in resource["units"]:
            frozen_kind = str(frozen_unit["representation_kind"])
            text_index = offsets.get(frozen_kind, 0)
            offsets[frozen_kind] = text_index + 1
            units[str(frozen_unit["unit_id"])] = _static_text_vector(
                texts[frozen_kind][text_index],
                dimensions,
            )
    query_vectors = {
        str(case["query_id"]): _static_text_vector(str(case["query_text"]), dimensions)
        for case in cast(list[dict[str, Any]], queries["cases"])
        if case["case_kind"] in {"semantic", "hybrid", "timestamp"}
    }
    return _FrozenVectorOracle(dimensions, units, query_vectors)


def _resource_texts(resource: Mapping[str, Any], artifact: Path) -> dict[str, list[str]]:
    kind = resource["resource_kind"]
    if kind == "document":
        text = artifact.read_text(encoding="utf-8")
        parts = text.split("## Verification", 1)
        return {"retrieval_text": [parts[0].strip(), ("## Verification" + parts[1]).strip()]}
    value = _json(artifact)
    if kind == "image":
        return {"caption_text": [str(value["caption_text"])]}
    result = {"audio_transcript": [str(item["text"]) for item in value["passages"]]}
    if kind == "video" and value.get("frames"):
        result["frame_caption"] = [str(item["caption"]) for item in value["frames"]]
    return result


def _actual_representation_kind(frozen: str) -> str:
    return "timed_passage" if frozen == "audio_transcript" else frozen


def _evaluation_batches(
    corpus: Mapping[str, Any],
    oracle: _FrozenVectorOracle,
) -> Iterator[PreparedResourceBatch]:
    for resource in cast(list[dict[str, Any]], corpus["resources"]):
        resource_id_value = str(resource["resource_id"])
        texts = _resource_texts(resource, CORPUS_PATH.parent / resource["artifact_ref"])
        representations: list[RepresentationRecord] = []
        representation_ids: dict[str, str] = {}
        for frozen_kind in resource["representations"]:
            if frozen_kind not in texts:
                continue
            actual = _actual_representation_kind(frozen_kind)
            representation_id = f"rep-{resource_id_value}-{actual}"
            representation_ids[frozen_kind] = representation_id
            representations.append(
                RepresentationRecord(
                    representation_id,
                    resource_id_value,
                    actual,
                    "text",
                    "\n".join(texts[frozen_kind]),
                )
            )
        units: list[SearchUnitRecord] = []
        vectors: list[VectorRecord] = []
        offsets: dict[str, int] = {}
        for frozen_unit in resource["units"]:
            frozen_kind = str(frozen_unit["representation_kind"])
            text_index = offsets.get(frozen_kind, 0)
            offsets[frozen_kind] = text_index + 1
            text = texts[frozen_kind][text_index]
            unit_kind = str(frozen_unit["unit_kind"])
            if unit_kind == "time_segment":
                locator = Locator(
                    "time_segment",
                    {
                        "start_ms": frozen_unit["start_ms"],
                        "end_ms": frozen_unit["end_ms"],
                        "track": "audio" if resource["resource_kind"] == "audio" else "video",
                    },
                )
            elif unit_kind == "frame":
                locator = Locator(
                    "video_frame",
                    {
                        "timestamp_ms": frozen_unit["timestamp_ms"],
                        "frame_id": frozen_unit["frame_id"],
                    },
                )
            else:
                locator = Locator("text_unit", {"ordinal": frozen_unit["ordinal"]})
            unit_id_value = str(frozen_unit["unit_id"])
            units.append(
                SearchUnitRecord(
                    unit_id_value,
                    resource_id_value,
                    representation_ids[frozen_kind],
                    unit_kind,
                    "text",
                    text,
                    locator,
                    int(frozen_unit["ordinal"]),
                )
            )
            if unit_id_value in oracle.unit_vectors:
                vectors.append(VectorRecord(unit_id_value, SPACE_ID, oracle.unit_vectors[unit_id_value]))

        if resource["resource_kind"] == "document":
            query_rep = f"rep-{resource_id_value}-whole-query"
            candidate_rep = f"rep-{resource_id_value}-whole-candidate"
            representations.extend(
                (
                    RepresentationRecord(query_rep, resource_id_value, "whole_text_query", "text", None),
                    RepresentationRecord(candidate_rep, resource_id_value, "whole_text_candidate", "text", None),
                )
            )
            query_unit = f"whole-query-{resource_id_value}"
            candidate_unit = f"whole-candidate-{resource_id_value}"
            metadata = {"similarity_basis": "document_text", "aggregation": "direct_text_v1"}
            units.extend(
                (
                    SearchUnitRecord(
                        query_unit,
                        resource_id_value,
                        query_rep,
                        "whole_resource",
                        "text",
                        None,
                        Locator("whole_resource", {}),
                        0,
                        metadata=metadata,
                    ),
                    SearchUnitRecord(
                        candidate_unit,
                        resource_id_value,
                        candidate_rep,
                        "whole_resource",
                        "text",
                        None,
                        Locator("whole_resource", {}),
                        0,
                        metadata=metadata,
                    ),
                )
            )
            whole_vector = _static_text_vector(
                "\n".join(texts["retrieval_text"]),
                oracle.dimensions,
            )
            vectors.extend(
                (
                    VectorRecord(query_unit, SPACE_ID, whole_vector),
                    VectorRecord(candidate_unit, SPACE_ID, whole_vector),
                )
            )

        yield PreparedResourceBatch(
            ResourceRecord(
                resource_id_value,
                str(resource["resource_kind"]),
                str(resource["media_type"]),
                str(resource["source_namespace"]),
                Locator("external_record", {"ref": resource_id_value}),
                content_hash=str(resource["content_sha256"]),
                metadata={"ingestion": {"adapter": "q1_public_fixture"}},
            ),
            tuple(representations),
            tuple(units),
            (EmbeddingSpaceRecord(SPACE_ID, oracle.dimensions, "cosine", SPACE_FINGERPRINT),)
            if vectors
            else (),
            tuple(vectors),
        )


def _quality_cases(queries: Mapping[str, Any]) -> tuple[QualityCase, ...]:
    cases = []
    for case in cast(list[dict[str, Any]], queries["cases"]):
        judgments = []
        for item in case["judgments"]:
            evidence = item.get("evidence", {})
            judgments.append(
                QualityJudgment(
                    resource_id=item["resource_id"],
                    unit_id=item.get("unit_id"),
                    grade=item["grade"],
                    start_ms=evidence.get("start_ms"),
                    end_ms=evidence.get("end_ms"),
                    timestamp_ms=evidence.get("timestamp_ms"),
                )
            )
        cases.append(
            QualityCase(
                case_id=case["query_id"],
                case_kind=case["case_kind"],
                query_text=case["query_text"],
                cutoffs=tuple(case["cutoffs"]["recall"]),
                mrr_cutoff=case["cutoffs"]["mrr"],
                ndcg_cutoff=case["cutoffs"]["ndcg"],
                judgments=tuple(judgments),
                slice_tags=tuple(case["slice_tags"]),
            )
        )
    return tuple(cases)


def _scope(case: Mapping[str, Any]) -> SearchScope:
    allowed = case["allowed"]
    return SearchScope(
        resource_kinds=tuple(allowed["resource_kinds"]),
        representation_kinds=tuple(
            _actual_representation_kind(value) for value in allowed["representation_kinds"]
        ),
        unit_kinds=tuple(allowed["unit_kinds"]),
    )


def _quality_unit(catalog: SQLiteCatalog, item: object) -> QualityUnit:
    unit_id_value = getattr(item, "unit_id", None)
    if not isinstance(unit_id_value, str):
        raise OfflineEvaluationError("unit-target result has no unit identity")
    unit = catalog.read_unit(unit_id_value)
    if unit is None:
        raise OfflineEvaluationError("ranked unit could not be read")
    payload = unit.evidence_locator.payload
    return QualityUnit(
        unit_id=unit.unit_id,
        resource_id=unit.resource_id,
        start_ms=cast(int | None, payload.get("start_ms")),
        end_ms=cast(int | None, payload.get("end_ms")),
        timestamp_ms=cast(int | None, payload.get("timestamp_ms")),
    )


def _run_quality(
    catalog: SQLiteCatalog,
    corpus: Mapping[str, Any],
    queries: Mapping[str, Any],
    oracle: _FrozenVectorOracle,
    implementation_ref: str,
    *,
    lexical_weight: float = 0.4,
    semantic_weight: float = 0.6,
) -> dict[str, Any]:
    facade = PreparedResourceCatalog(catalog)
    by_id = {case["query_id"]: case for case in queries["cases"]}

    def rank(case: QualityCase) -> Sequence[QualityUnit]:
        raw = by_id[case.case_id]
        scope = _scope(raw)
        kind = raw["case_kind"]
        if kind == "lexical":
            result = facade.search_text(case.query_text, scope=scope, target=TARGET_UNIT, limit=10)
            ranked = []
            for item in result.results:
                unit_id_value = item["unit_id"]
                if not isinstance(unit_id_value, str):
                    continue
                unit = catalog.read_unit(unit_id_value)
                if unit is not None:
                    ranked.append(_quality_unit(catalog, unit))
            return ranked
        if kind in {"semantic", "timestamp"}:
            result = facade.search_vector(
                oracle.query_vectors[case.case_id],
                SPACE_ID,
                scope=scope,
                target=TARGET_UNIT,
                limit=10,
            )
            ranked = []
            for item in result.results:
                unit_id_value = item["unit_id"]
                if not isinstance(unit_id_value, str):
                    continue
                unit = catalog.read_unit(unit_id_value)
                if unit is not None:
                    ranked.append(_quality_unit(catalog, unit))
            return ranked
        if kind == "hybrid":
            lexical_branches = (
                (
                    LexicalBranch(
                        "lexical",
                        case.query_text,
                        weight=lexical_weight,
                        candidate_limit=100,
                    ),
                )
                if lexical_weight > 0
                else ()
            )
            vector_branches = (
                (
                    VectorBranch(
                        "semantic",
                        SPACE_ID,
                        oracle.query_vectors[case.case_id],
                        weight=semantic_weight,
                        candidate_limit=100,
                        expected_fingerprint=SPACE_FINGERPRINT,
                    ),
                )
                if semantic_weight > 0
                else ()
            )
            result = CoreRetrievalService(catalog).search(
                SearchRequest(
                    lexical_branches=lexical_branches,
                    vector_branches=vector_branches,
                    scope=scope,
                    target=TARGET_UNIT,
                    limit=10,
                    allow_partial=True,
                )
            )
            return [_quality_unit(catalog, item) for item in result.items]
        if kind == "resource_similarity":
            query_resource = raw["query_resource_id"]
            engine = MDRackEngine(
                root=ROOT,
                config=MDRackConfig(),
                storage=cast(KnowledgeStorage, _EngineStorage(catalog, close_catalog=False)),
                search_index=catalog,
                read_storage=catalog,
            )
            try:
                result = engine.find_textually_similar_resources(
                    f"whole-query-{query_resource}",
                    SPACE_ID,
                    aggregation="direct_text_v1",
                    expected_fingerprint=SPACE_FINGERPRINT,
                    limit=10,
                )
            finally:
                engine.close()
            return [
                QualityUnit(item.unit_id, item.resource_id)
                for item in result.results
            ]
        raise OfflineEvaluationError("unknown quality case kind")

    report = evaluate_quality(
        _quality_cases(queries),
        rank,
        corpus_ref=str(corpus["contract_digest"]),
        implementation_ref=implementation_ref,
    )
    cast(dict[str, Any], report["summary"]).pop("evaluation_latency_ms", None)
    return report


def _chunk_ablation(workspace: Path) -> dict[str, object]:
    from mdrack.application.transcript_ingestion import TranscriptIngestionService
    from mdrack_media import TimedChunkingPolicy

    resource = resource_id("q1", "chunk-ablation")
    artifact = _read_timed_artifact(
        _transcript_bytes(
            resource,
            tuple(
                (index * 10_000, (index + 1) * 10_000, f"sentence {index} transaction boundary.")
                for index in range(9)
            ),
        ),
        resource,
    )
    profiles = {
        "compact": TimedChunkingPolicy(
            soft_min_tokens=4,
            target_tokens=8,
            soft_max_tokens=12,
            hard_max_tokens=16,
        ),
        "balanced": TimedChunkingPolicy(
            soft_min_tokens=8,
            target_tokens=16,
            soft_max_tokens=24,
            hard_max_tokens=32,
        ),
        "large": TimedChunkingPolicy(
            soft_min_tokens=16,
            target_tokens=32,
            soft_max_tokens=48,
            hard_max_tokens=64,
        ),
    }
    result: dict[str, object] = {}
    for name, policy in profiles.items():
        path = workspace / f"chunk-{name}.sqlite3"
        catalog = SQLiteCatalog.create(path)
        try:
            batch = TranscriptIngestionService(catalog).prepare(
                artifact,
                resource_kind="audio",
                media_type="audio/wav",
                source_namespace="q1-public",
                source_locator=Locator("external_record", {"ref": resource}),
                chunking_policy=policy,
            )
            intervals = [
                (unit.evidence_locator.payload["start_ms"], unit.evidence_locator.payload["end_ms"])
                for unit in batch.units
                if unit.unit_kind == "time_segment"
            ]
            result[name] = {
                "passages": len(intervals),
                "timeline_start_ms": intervals[0][0],
                "timeline_end_ms": intervals[-1][1],
                "non_overlapping": all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:])),
            }
        finally:
            catalog.close()
    return result


def _privacy_violations(payload: str, sentinels: Mapping[str, Any]) -> list[str]:
    violations = []
    for key in sentinels["forbidden_keys"]:
        if key in payload:
            violations.append("forbidden_key")
    for value in cast(dict[str, str], sentinels["forbidden_values"]).values():
        if value in payload:
            violations.append("forbidden_value")
    return violations


def _run_once(run_ordinal: int, implementation_ref: str) -> tuple[dict[str, Any], str]:
    corpus = _json(CORPUS_PATH)
    queries = _json(QUERIES_PATH)
    oracle = _build_oracle(corpus, queries)
    sentinels = _json(SENTINELS_PATH)
    private_sentinel = cast(dict[str, str], sentinels["forbidden_values"])["metadata"]
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        with tempfile.TemporaryDirectory(prefix="mdrack-v11-q1-") as temporary:
            workspace = Path(temporary)
            scenarios = {
                "A_metadata": _scenario_a(workspace, private_sentinel),
                "B_audio": _scenario_b(workspace),
                "C_video": _scenario_c(workspace),
                "D_degradation": _scenario_d(workspace, private_sentinel),
            }
            catalog_path = workspace / "evaluation.sqlite3"
            SQLiteCatalog.create(catalog_path).close()
            catalog = SQLiteCatalog.open(catalog_path)
            facade = PreparedResourceCatalog(catalog)
            try:
                imported = 0
                for batch in _evaluation_batches(corpus, oracle):
                    facade.import_bytes(encode_prepared_resource_manifest(batch))
                    imported += 1
                profile_quality = {
                    name: _run_quality(
                        catalog,
                        corpus,
                        queries,
                        oracle,
                        implementation_ref,
                        lexical_weight=lexical_weight,
                        semantic_weight=semantic_weight,
                    )
                    for name, lexical_weight, semantic_weight in HYBRID_PROFILES
                }
                quality = profile_quality["configured"]
            finally:
                facade.close()
            chunk_ablation = _chunk_ablation(workspace)
            provider_health = asyncio.run(_ConceptProvider().health())
            run = {
                "run_ordinal": run_ordinal,
                "fresh_catalog": True,
                "catalog_resource_count": imported,
                "scenarios": scenarios,
                "quality": quality,
                "ablations": {
                    "chunk_profiles": chunk_ablation,
                    "frame_profiles": {
                        **cast(
                            dict[str, str],
                            scenarios["C_video"]["frame_ablations"],
                        ),
                    },
                    "metadata_profiles": {
                        **cast(
                            dict[str, str],
                            scenarios["A_metadata"]["metadata_ablations"],
                        ),
                    },
                    "hybrid_profiles": {
                        name: {
                            "lexical_weight": lexical_weight,
                            "semantic_weight": semantic_weight,
                            "metrics": cast(
                                dict[str, Any],
                                profile_quality[name]["by_case_kind"],
                            )["hybrid"],
                        }
                        for name, lexical_weight, semantic_weight in HYBRID_PROFILES
                    },
                },
                "provider_diagnostics": {
                    "boundary": provider_health.provider,
                    "dimensions": provider_health.dimensions,
                    "model": provider_health.model,
                    "status": "ok" if provider_health.ok else "failed",
                },
            }
        if Path(temporary).exists():
            raise OfflineEvaluationError("disposable workspace was not removed")
    finally:
        root_logger.removeHandler(handler)
        handler.close()
    return run, log_stream.getvalue()


def execute_twice(*, config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Execute Q1 twice and return one privacy-safe deterministic report."""
    _validate_config(config_path)
    before = _frozen_source_hashes()
    implementation_ref = _implementation_ref()
    with _NetworkGuard() as network:
        first, first_logs = _run_once(1, implementation_ref)
        second, second_logs = _run_once(2, implementation_ref)
    after = _frozen_source_hashes()
    comparable_first = {key: value for key, value in first.items() if key != "run_ordinal"}
    comparable_second = {key: value for key, value in second.items() if key != "run_ordinal"}
    if comparable_first != comparable_second:
        raise OfflineEvaluationError("two offline runs produced different logical evidence")
    if before != after:
        raise OfflineEvaluationError("frozen source bytes changed")
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_type": "mdrack_v1_1_offline_evaluation",
        "input_ref": _json(FREEZE_PATH)["contract_digest"],
        "implementation_ref": implementation_ref,
        "execution": {
            "repeats": 2,
            "fresh_disposable_catalogs": 2,
            "deterministic_logical_evidence": True,
            "canonical_logical_digest": _digest_value(comparable_first),
            "source_hashes_unchanged": True,
            "disposable_catalogs_removed": True,
            "network_attempts": network.attempts,
            "network_syscalls": None,
            "network_syscalls_observed": False,
            "provider_boundary": "deterministic_static_text_vectors_no_semantic_quality",
        },
        "runs": [first, second],
        "non_claims": [
            "live_provider",
            "private_corpus",
            "paid_or_network_provider",
            "windows",
            "python_3_12",
            "visual_similarity",
            "acoustic_similarity",
            "universal_semantic_quality",
        ],
        "privacy": {
            "raw_queries_included": False,
            "raw_content_included": False,
            "paths_included": False,
            "unit_ids_included": False,
            "violations": 0,
            "surfaces_checked": [],
            "capture_ledger": [],
        },
    }
    ledger = capture_runtime_surfaces(
        api=[first["scenarios"], second["scenarios"]],
        provider=[first["provider_diagnostics"], second["provider_diagnostics"]],
        evaluation=[first, second],
        logs=first_logs + second_logs,
    )
    ledger.apply(report)
    if network.attempts != 0:
        raise OfflineEvaluationError("offline run attempted network access")
    return report


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _validate_hybrid_matrix(report: Mapping[str, Any]) -> None:
    runs = report.get("runs")
    expected_repeats = cast(dict[str, Any], _RUNTIME_CONTRACT["required_execution"])[
        "repeats"
    ]
    if not isinstance(runs, list) or len(runs) != expected_repeats:
        raise OfflineEvaluationError("Q1 hybrid matrix has invalid repeats")

    expected_case_signature: tuple[int, int] | None = None
    for run in runs:
        if not isinstance(run, Mapping):
            raise OfflineEvaluationError("Q1 hybrid matrix run is invalid")
        ablations = run.get("ablations")
        if not isinstance(ablations, Mapping):
            raise OfflineEvaluationError("Q1 hybrid matrix ablations are invalid")
        profiles = ablations.get("hybrid_profiles")
        if not isinstance(profiles, Mapping) or set(profiles) != set(
            _HYBRID_PROFILE_WEIGHTS
        ):
            raise OfflineEvaluationError("Q1 hybrid profile set is invalid")

        for name, expected_weights in _HYBRID_PROFILE_WEIGHTS.items():
            cell = profiles[name]
            if not isinstance(cell, Mapping) or set(cell) != _HYBRID_CELL_KEYS:
                raise OfflineEvaluationError("Q1 hybrid profile cell is invalid")
            weights = (cell.get("lexical_weight"), cell.get("semantic_weight"))
            if not all(_is_finite_number(value) for value in weights) or weights != (
                *expected_weights,
            ):
                raise OfflineEvaluationError("Q1 hybrid profile weights are invalid")

            metrics = cell.get("metrics")
            if not isinstance(metrics, Mapping) or set(metrics) != HYBRID_METRIC_KEYS:
                raise OfflineEvaluationError("Q1 hybrid profile metrics are invalid")
            if not all(_is_finite_number(value) for value in metrics.values()):
                raise OfflineEvaluationError("Q1 hybrid metric value is invalid")
            cases = metrics["cases"]
            temporal_cases = metrics["temporal_cases"]
            if (
                type(cases) is not int
                or cases <= 0
                or type(temporal_cases) is not int
                or temporal_cases < 0
                or temporal_cases > cases
            ):
                raise OfflineEvaluationError("Q1 hybrid case counts are invalid")
            case_signature = (cases, temporal_cases)
            if expected_case_signature is None:
                expected_case_signature = case_signature
            elif case_signature != expected_case_signature:
                raise OfflineEvaluationError("Q1 hybrid case counts are inconsistent")


def _validate_runtime_result(report: Mapping[str, Any]) -> None:
    _validate_hybrid_matrix(report)
    PrivacyCaptureLedger.from_report(report, require_complete=True)


def _safe_json_text(report: Mapping[str, Any]) -> str:
    sentinels = _json(SENTINELS_PATH)
    try:
        return serialize_safe_json(
            report,
            forbidden_values=list(
                cast(dict[str, str], sentinels["forbidden_values"]).values()
            ),
            forbidden_keys=cast(list[str], sentinels["forbidden_keys"]),
        )
    except PrivacyViolation as error:
        raise OfflineEvaluationError("privacy gate rejected Q1 result") from error


def finalize_report(
    report: Mapping[str, Any],
    *,
    network_syscalls: int | None = None,
) -> dict[str, Any]:
    """Apply the distinct Q1 runtime-result contract and its privacy gate."""
    finalized = json.loads(json.dumps(report))
    finalized.pop("artifact_digest", None)
    if network_syscalls is not None:
        execution = finalized.get("execution")
        if not isinstance(execution, dict):
            raise OfflineEvaluationError("Q1 execution evidence is invalid")
        execution["network_syscalls"] = network_syscalls
        execution["network_syscalls_observed"] = True
    _validate_runtime_result(finalized)
    sentinels = _json(SENTINELS_PATH)
    forbidden_values = list(cast(dict[str, str], sentinels["forbidden_values"]).values())
    forbidden_keys = cast(list[str], sentinels["forbidden_keys"])
    serialized = _safe_json_text(finalized)
    if not scan_json_text(
        serialized,
        forbidden_values=forbidden_values,
        forbidden_keys=forbidden_keys,
    ).safe:
        raise OfflineEvaluationError("serialized Q1 result failed privacy scan")
    finalized["artifact_digest"] = _digest_value(finalized)
    return finalized


def safe_report_json(report: Mapping[str, Any]) -> str:
    """Serialize a Q1 result only after the runtime privacy contract passes."""
    finalized = finalize_report(report)
    return _safe_json_text(finalized) + "\n"


def safe_candidate_json(report: Mapping[str, Any]) -> str:
    """Serialize privacy-safe intermediate evidence without accepting it."""
    PrivacyCaptureLedger.from_report(report)
    _validate_hybrid_matrix(report)
    return _safe_json_text(report) + "\n"


def write_report(report: Mapping[str, Any], output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "summary.json"
    temporary = output / ".summary.json.tmp"
    try:
        candidate = json.loads(json.dumps(report))
        candidate.pop("artifact_digest", None)
        ledger = PrivacyCaptureLedger.from_report(candidate)
        if "report" not in ledger.entries:
            ledger.capture("report", candidate)
            ledger.apply(candidate)
        if "disk" not in ledger.entries:
            temporary.write_text(safe_candidate_json(candidate), encoding="utf-8")
            ledger.capture("disk", temporary.read_text(encoding="utf-8"))
            ledger.apply(candidate)
        finalized = finalize_report(candidate)
        temporary.write_text(safe_report_json(finalized), encoding="utf-8")
        sentinels = _json(SENTINELS_PATH)
        if not scan_json_text(
            temporary.read_text(encoding="utf-8"),
            forbidden_values=list(
                cast(dict[str, str], sentinels["forbidden_values"]).values()
            ),
            forbidden_keys=cast(list[str], sentinels["forbidden_keys"]),
        ).safe:
            raise OfflineEvaluationError("disk Q1 result failed privacy scan")
        temporary.replace(destination)
        if destination.read_text(encoding="utf-8") != safe_report_json(finalized):
            raise OfflineEvaluationError("disk Q1 result changed after replacement")
        return finalized
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "CONFIG_PATH",
    "HYBRID_PROFILES",
    "OfflineEvaluationError",
    "PRIVACY_SURFACES",
    "PrivacyCaptureLedger",
    "capture_cli_transport",
    "capture_runtime_surfaces",
    "execute_twice",
    "finalize_report",
    "safe_candidate_json",
    "safe_report_json",
    "write_report",
]
