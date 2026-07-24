"""Reproducible, provider-free benchmark for compact builtin exact vector search.

The harness builds disposable clean-v2 catalogs with canonical binary float32 or
float64 payloads and an explicit diagnostic read-only JSON baseline. It records
cold and warm timings plus privacy-safe backend counters; it never probes optional
accelerators or external services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_core.domain.common import JSONValue
from mdrack_sqlite import SQLiteCatalog
from mdrack_sqlite.builtin_exact import BuiltinExactVectorBackend

DEFAULT_CELLS = ((10_000, 384), (50_000, 384), (48_000, 1024), (100_000, 1024))
MetricSample = dict[str, float | int | None]


@dataclass(frozen=True)
class CodecSpec:
    """A canonical binary payload shape available in the builtin backend."""

    name: str
    codec_id: str
    component_bytes: int
    value_policy: str | None


_CODECS = {
    "f32": CodecSpec("f32", "ieee754-f32-le-v1", 4, "ieee754-f32-canonical-v1"),
    "f64": CodecSpec("f64", "ieee754-f64-le-v1", 8, None),
    "legacy-json": CodecSpec("legacy-json", "json-f64-v1", 0, None),
}

_CHILD = r'''
import json
import sys
import time
from pathlib import Path

try:
    import resource
except ImportError:
    resource = None

from mdrack_core.domain import SearchScope, VectorBranch
from mdrack_sqlite import SQLiteCatalog


def sample(catalog, branch):
    started_cpu = time.process_time()
    started = time.perf_counter()
    result = catalog.search_vector(branch, scope=SearchScope())
    wall_ms = (time.perf_counter() - started) * 1000
    cpu_ms = (time.process_time() - started_cpu) * 1000
    counters = catalog.last_vector_search_counters
    if counters is None:
        raise RuntimeError("builtin vector counters were not recorded")
    return {
        "wall_ms": wall_ms,
        "cpu_ms": cpu_ms,
        "rss_kib": None if resource is None else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "candidate_rows": counters.candidate_rows,
        "decoded_vectors": counters.decoded_vectors,
        "skipped_vectors": counters.skipped_vectors,
        "decode_cpu_ms": counters.decode_cpu_seconds * 1000,
        "score_cpu_ms": counters.score_seconds * 1000,
        "sort_ms": counters.sort_seconds * 1000,
        "result_count": len(result),
    }


database, dimensions, candidate_limit, warm_queries, query_json = sys.argv[1:]
query = tuple(json.loads(query_json))
if len(query) != int(dimensions):
    raise RuntimeError("query dimension mismatch")
branch = VectorBranch("envelope", "space", query, candidate_limit=int(candidate_limit))
with SQLiteCatalog.open(Path(database)) as catalog:
    cold = sample(catalog, branch)
    warm = [sample(catalog, branch) for _ in range(int(warm_queries))]
print(json.dumps({"cold": cold, "warm": warm}, separators=(",", ":")))
'''


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _summary(samples: list[MetricSample]) -> dict[str, dict[str, float | None]]:
    metrics: dict[str, dict[str, float | None]] = {}
    for metric in (
        "wall_ms",
        "cpu_ms",
        "rss_kib",
        "candidate_rows",
        "decoded_vectors",
        "skipped_vectors",
        "decode_cpu_ms",
        "score_cpu_ms",
        "sort_ms",
        "result_count",
    ):
        values = [sample[metric] for sample in samples]
        if any(value is None for value in values):
            metrics[metric] = {"p50": None, "p95": None, "p99": None}
            continue
        numeric = [float(value) for value in values if value is not None]
        metrics[metric] = {
            "p50": percentile(numeric, 0.50),
            "p95": percentile(numeric, 0.95),
            "p99": percentile(numeric, 0.99),
        }
    return metrics


def _space_metadata(codec: CodecSpec) -> str:
    if codec.name == "legacy-json":
        return "{}"
    metadata: dict[str, str] = {"vector_codec": codec.codec_id}
    if codec.value_policy is not None:
        metadata["vector_value_policy"] = codec.value_policy
    return json.dumps(metadata, separators=(",", ":"), sort_keys=True)


def _canonical_float32(values: tuple[float, ...]) -> tuple[float, ...]:
    """Round finite fixture values once to the compact storage representation."""
    return struct.unpack(f"<{len(values)}f", struct.pack(f"<{len(values)}f", *values))


def _representative_vector(seed: str, dimensions: int) -> tuple[float, ...]:
    """Generate a deterministic normalized finite vector without one-hot shortcuts."""
    state = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")
    phase = (state % 10_007) / 10_007
    values: list[float] = []
    for index in range(dimensions):
        state = (state * 6_364_136_223_846_793_005 + 1_442_695_040_888_963_407) & ((1 << 64) - 1)
        uniform = ((state >> 11) / (1 << 53)) * 2.0 - 1.0
        position = index + 1
        values.append(
            uniform * 0.72
            + math.sin(position * (phase + 0.17)) * 0.21
            + math.cos(position * (phase + 0.31)) * 0.07
        )
    norm = math.hypot(*values)
    if norm == 0.0:
        raise RuntimeError("representative vector unexpectedly has zero norm")
    return tuple(value / norm for value in values)


def _vector_for_codec(seed: str, dimensions: int, codec: CodecSpec) -> tuple[float, ...]:
    values = _representative_vector(seed, dimensions)
    return _canonical_float32(values) if codec.name == "f32" else values


def _vector_payload(values: tuple[float, ...], codec: CodecSpec) -> bytes:
    if codec.name == "legacy-json":
        return json.dumps(values, allow_nan=False, separators=(",", ":")).encode("utf-8")
    format_code = "f" if codec.name == "f32" else "d"
    return struct.pack(f"<{len(values)}{format_code}", *values)


def make_catalog(path: Path, count: int, dimensions: int, codec: CodecSpec) -> None:
    with SQLiteCatalog.create_v2(path) as catalog:
        connection = catalog.connection
        now = "2026-07-24T00:00:00+00:00"
        connection.execute(
            "INSERT INTO core_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "resource",
                "synthetic",
                "application/x.synthetic",
                "offline-envelope",
                "synthetic",
                "{}",
                "sha256:" + "0" * 64,
                None,
                None,
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO core_representations VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("representation", "resource", "whole_resource", "text", None, None, None, None, None, "{}"),
        )
        connection.execute(
            "INSERT INTO core_embedding_spaces VALUES(?,?,?,?,?)",
            ("space", dimensions, "cosine", "offline-envelope-v1", _space_metadata(codec)),
        )
        for start in range(0, count, 1_000):
            stop = min(count, start + 1_000)
            connection.executemany(
                "INSERT INTO core_search_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    (
                        f"unit-{index:08d}",
                        "resource",
                        "representation",
                        "whole_resource",
                        "text",
                        None,
                        "synthetic",
                        "{}",
                        index,
                        None,
                        None,
                        "{}",
                    )
                    for index in range(start, stop)
                ),
            )
            connection.executemany(
                "INSERT INTO core_unit_embeddings VALUES(?,?,?,?)",
                (
                    (
                        f"unit-{index:08d}",
                        "space",
                        _vector_payload(_vector_for_codec(f"benchmark-unit-{index}", dimensions, codec), codec),
                        now,
                    )
                    for index in range(start, stop)
                ),
            )
        connection.commit()


def _run_child(
    database: Path,
    dimensions: int,
    candidate_limit: int,
    warm_queries: int,
    query: tuple[float, ...],
) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _CHILD,
            str(database),
            str(dimensions),
            str(candidate_limit),
            str(warm_queries),
            json.dumps(query, allow_nan=False, separators=(",", ":")),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    decoded = json.loads(completed.stdout)
    if not isinstance(decoded, dict):
        raise RuntimeError("benchmark child did not return an object")
    return cast(dict[str, object], decoded)


def run_cell(
    count: int,
    dimensions: int,
    codec: CodecSpec,
    warmups: int,
    repetitions: int,
    candidate_limit: int,
    warm_queries: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="mdrack-builtin-exact-") as temp:
        database = Path(temp) / "catalog.db"
        make_catalog(database, count, dimensions, codec)
        query = _vector_for_codec("benchmark-query", dimensions, codec)
        for _ in range(warmups):
            _run_child(database, dimensions, candidate_limit, warm_queries, query)
        samples = [
            _run_child(database, dimensions, candidate_limit, warm_queries, query)
            for _ in range(repetitions)
        ]
        cold_samples = [cast(MetricSample, sample["cold"]) for sample in samples]
        warm_samples = [
            item
            for sample in samples
            for item in cast(list[MetricSample], sample["warm"])
        ]
        db_bytes = sum(path.stat().st_size for path in database.parent.glob("catalog.db*"))
        return {
            "codec": codec.name,
            "codec_id": codec.codec_id,
            "component_bytes": codec.component_bytes,
            "units": count,
            "dimensions": dimensions,
            "binary_payload_bytes": count * dimensions * codec.component_bytes,
            "vector_payload_bytes": sum(
                len(_vector_payload(_vector_for_codec(f"benchmark-unit-{index}", dimensions, codec), codec))
                for index in range(count)
            ),
            "payload_encoding": "canonical_legacy_json_readonly" if codec.name == "legacy-json" else "binary",
            "db_bytes": db_bytes,
            "candidate_limit": candidate_limit,
            "cold": {"samples": len(cold_samples), "metrics": _summary(cold_samples)},
            "warm": {"samples": len(warm_samples), "metrics": _summary(warm_samples)},
        }


_PARITY_DIMENSIONS = 16
_PARITY_SPACE_ID = "stage8-multimodal-space"
_PARITY_FINGERPRINT = "stage8-multimodal-fingerprint-v1"
_MULTIMODAL_FIXTURE_BYTES = {
    "stage8-note.md": b"# Stage 8 synthetic note\n\nDeterministic compact parity fixture.\n",
    "stage8-audio.json": b'{"segments":[{"start_ms":0,"end_ms":1200,"text":"synthetic audio"}]}\n',
    "stage8-video.json": b'{"segments":[{"start_ms":0,"end_ms":1600}],"frames":[{"timestamp_ms":800}]}\n',
    "stage8-image.bin": bytes(range(32)),
}


@dataclass(frozen=True)
class MultimodalFixtureUnit:
    """One public search unit backed by a synthetic immutable source file."""

    source_name: str
    resource_id: str
    resource_kind: str
    media_type: str
    representation_kind: str
    modality: str
    unit_kind: str
    unit_id: str
    evidence_kind: str
    evidence_payload: Mapping[str, JSONValue]


_MULTIMODAL_UNITS = (
    MultimodalFixtureUnit(
        "stage8-note.md",
        "stage8-note",
        "document",
        "text/markdown",
        "retrieval_text",
        "text",
        "text_chunk",
        "stage8-note-unit",
        "line_range",
        {"end_line": 3, "start_line": 1},
    ),
    MultimodalFixtureUnit(
        "stage8-audio.json",
        "stage8-audio",
        "audio",
        "audio/wav",
        "timed_passage",
        "text",
        "time_segment",
        "stage8-audio-unit",
        "time_segment",
        {"end_ms": 1200, "start_ms": 0, "track": "audio"},
    ),
    MultimodalFixtureUnit(
        "stage8-video.json",
        "stage8-video",
        "video",
        "video/mp4",
        "timed_passage",
        "text",
        "time_segment",
        "stage8-video-segment-unit",
        "time_segment",
        {"end_ms": 1600, "start_ms": 0, "track": "video"},
    ),
    MultimodalFixtureUnit(
        "stage8-video.json",
        "stage8-video",
        "video",
        "video/mp4",
        "frame_caption",
        "text",
        "frame",
        "stage8-video-frame-unit",
        "video_frame",
        {"timestamp_ms": 800},
    ),
    MultimodalFixtureUnit(
        "stage8-image.bin",
        "stage8-image",
        "image",
        "image/png",
        "visual",
        "image",
        "whole_resource",
        "stage8-image-unit",
        "whole_image",
        {"source_ref": "stage8-image"},
    ),
)
_MULTIMODAL_SCOPES = (
    ("notes", SearchScope(resource_kinds=("document",))),
    ("audio", SearchScope(resource_kinds=("audio",))),
    (
        "video",
        SearchScope(
            resource_kinds=("video",),
            representation_kinds=("timed_passage",),
            unit_kinds=("time_segment",),
        ),
    ),
    ("frames", SearchScope(representation_kinds=("frame_caption",), unit_kinds=("frame",))),
    ("images", SearchScope(resource_kinds=("image",))),
    ("all", SearchScope()),
)


def _multimodal_source_hashes(root: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in sorted(_MULTIMODAL_FIXTURE_BYTES)
    }


def _multimodal_input_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for name in sorted(_MULTIMODAL_FIXTURE_BYTES):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def _parity_space(codec: CodecSpec) -> EmbeddingSpaceRecord:
    metadata: Mapping[str, str] = {} if codec.name == "legacy-json" else json.loads(_space_metadata(codec))
    return EmbeddingSpaceRecord(
        _PARITY_SPACE_ID,
        _PARITY_DIMENSIONS,
        "cosine",
        _PARITY_FINGERPRINT,
        metadata,
    )


def _multimodal_batches(
    codec: CodecSpec,
    source_hashes: Mapping[str, str],
) -> tuple[PreparedResourceBatch, ...]:
    grouped: dict[str, list[MultimodalFixtureUnit]] = {}
    for item in _MULTIMODAL_UNITS:
        grouped.setdefault(item.resource_id, []).append(item)
    space = _parity_space(codec)
    batches: list[PreparedResourceBatch] = []
    for resource_id, items in grouped.items():
        first = items[0]
        representations = tuple(
            RepresentationRecord(
                f"{item.unit_id}-representation",
                resource_id,
                item.representation_kind,
                item.modality,
                None if item.modality == "image" else f"synthetic {item.unit_id}",
            )
            for item in items
        )
        units = tuple(
            SearchUnitRecord(
                item.unit_id,
                resource_id,
                f"{item.unit_id}-representation",
                item.unit_kind,
                item.modality,
                None if item.modality == "image" else f"synthetic {item.unit_id}",
                Locator(item.evidence_kind, item.evidence_payload),
                0,
            )
            for item in items
        )
        vectors = tuple(
            VectorRecord(
                item.unit_id,
                space.space_id,
                _vector_for_codec(f"stage8-parity/{item.unit_id}", space.dimensions, codec),
            )
            for item in items
        )
        batches.append(
            PreparedResourceBatch(
                ResourceRecord(
                    resource_id,
                    first.resource_kind,
                    first.media_type,
                    "stage8-synthetic",
                    Locator("stage8_source", {"resource_id": resource_id, "source": first.source_name}),
                    f"sha256:{source_hashes[first.source_name]}",
                ),
                representations,
                units,
                (space,),
                vectors,
            )
        )
    return tuple(batches)


def _install_multimodal_catalog(path: Path, codec: CodecSpec, source_hashes: Mapping[str, str]) -> None:
    with SQLiteCatalog.create_v2(path) as catalog:
        for batch in _multimodal_batches(codec, source_hashes):
            catalog.replace_resource(batch)
        if codec.name == "legacy-json":
            for item in _MULTIMODAL_UNITS:
                payload = _vector_payload(
                    _vector_for_codec(f"stage8-parity/{item.unit_id}", _PARITY_DIMENSIONS, codec),
                    codec,
                )
                catalog.connection.execute(
                    "UPDATE core_unit_embeddings SET embedding=? WHERE unit_id=? AND space_id=?",
                    (payload, item.unit_id, _PARITY_SPACE_ID),
                )
            catalog.connection.commit()


def _public_multimodal_results(path: Path, codec: CodecSpec) -> dict[str, list[dict[str, object]]]:
    query = _vector_for_codec("stage8-parity/query", _PARITY_DIMENSIONS, codec)
    branch = VectorBranch(
        "stage8-parity",
        _PARITY_SPACE_ID,
        query,
        candidate_limit=len(_MULTIMODAL_UNITS),
        expected_fingerprint=_PARITY_FINGERPRINT,
    )
    results: dict[str, list[dict[str, object]]] = {}
    with SQLiteCatalog.open(path) as catalog:
        for scope_name, scope in _MULTIMODAL_SCOPES:
            candidates = catalog.search_vector(branch, scope=scope)
            ranks = [candidate.rank for candidate in candidates]
            if ranks != list(range(1, len(candidates) + 1)):
                raise RuntimeError(f"{scope_name} results do not have dense ranks")
            results[scope_name] = [
                {
                    "resource_id": candidate.resource_id,
                    "unit_id": candidate.unit_id,
                    "rank": candidate.rank,
                    "evidence": {
                        "kind": candidate.evidence_locator.kind,
                        "payload": dict(candidate.evidence_locator.payload),
                    },
                }
                for candidate in candidates
            ]
    return results


def run_multimodal_parity_oracle() -> dict[str, object]:
    """Compare legacy JSON and compact float32 public retrieval on one immutable fixture."""
    with tempfile.TemporaryDirectory(prefix="mdrack-stage8-parity-") as temp:
        workspace = Path(temp)
        source_root = workspace / "sources"
        source_root.mkdir()
        for name, source in _MULTIMODAL_FIXTURE_BYTES.items():
            (source_root / name).write_bytes(source)
        before_hashes = _multimodal_source_hashes(source_root)
        input_sha256 = _multimodal_input_digest(source_root)
        legacy_path = workspace / "legacy-json.db"
        compact_path = workspace / "compact-f32.db"
        _install_multimodal_catalog(legacy_path, _CODECS["legacy-json"], before_hashes)
        _install_multimodal_catalog(compact_path, _CODECS["f32"], before_hashes)
        legacy_first = _public_multimodal_results(legacy_path, _CODECS["legacy-json"])
        legacy_repeat = _public_multimodal_results(legacy_path, _CODECS["legacy-json"])
        compact_first = _public_multimodal_results(compact_path, _CODECS["f32"])
        compact_repeat = _public_multimodal_results(compact_path, _CODECS["f32"])
        after_hashes = _multimodal_source_hashes(source_root)
        if before_hashes != after_hashes:
            raise RuntimeError("immutable multimodal fixture source hashes changed")
        if legacy_first != legacy_repeat or compact_first != compact_repeat:
            raise RuntimeError("multimodal retrieval is not deterministic across repeats")
        if legacy_first != compact_first:
            raise RuntimeError("legacy JSON and compact float32 public results diverged")
        return {
            "contract": "mdrack.stage8-multimodal-parity-v1",
            "input_sha256": input_sha256,
            "source_hashes_unchanged": True,
            "deterministic_repeats": True,
            "legacy_json_equals_compact_f32": True,
            "scopes": legacy_first,
        }


def parse_cells(raw: str) -> tuple[tuple[int, int], ...]:
    cells: list[tuple[int, int]] = []
    for item in raw.split(","):
        units, dimensions = item.split("x", 1)
        count = int(units)
        width = int(dimensions)
        if count < 1 or width < 1:
            raise ValueError("cells must contain positive unit and dimension counts")
        cells.append((count, width))
    return tuple(cells)


def parse_codecs(raw: str) -> tuple[CodecSpec, ...]:
    names = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not names or len(set(names)) != len(names):
        raise ValueError("codecs must be a non-empty unique comma-separated list")
    try:
        return tuple(_CODECS[name] for name in names)
    except KeyError as error:
        raise ValueError(f"unknown codec: {error.args[0]}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", default=",".join(f"{count}x{dimensions}" for count, dimensions in DEFAULT_CELLS))
    parser.add_argument("--codecs", default="f32")
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warm-queries", type=int, default=5)
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.warmups < 0 or args.repetitions < 1 or args.warm_queries < 1 or args.candidate_limit < 1:
        parser.error("warmups >= 0, repetitions >= 1, warm-queries >= 1 and candidate-limit >= 1 are required")
    try:
        cells = parse_cells(args.cells)
        codecs = parse_codecs(args.codecs)
    except ValueError as error:
        parser.error(str(error))

    started = time.perf_counter()
    parity_oracle = run_multimodal_parity_oracle()
    results = [
        run_cell(count, dimensions, codec, args.warmups, args.repetitions, args.candidate_limit, args.warm_queries)
        for codec in codecs
        for count, dimensions in cells
    ]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runner_digest = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    input_digest = hashlib.sha256(
        json.dumps(
            {
                "benchmark": {
                    "candidate_limit": args.candidate_limit,
                    "cells": args.cells,
                    "codecs": [codec.name for codec in codecs],
                    "repetitions": args.repetitions,
                    "warm_queries": args.warm_queries,
                    "warmups": args.warmups,
                },
                "parity_input_sha256": parity_oracle["input_sha256"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    report = {
        "contract": "mdrack.builtin-exact-benchmark-v1",
        "evidence_boundary": "local components",
        "privacy": {"source": "synthetic", "network_attempts": 0, "temporary_catalogs_removed": True},
        "host": {"python": sys.version.split()[0], "platform": platform.platform(), "cpu": platform.processor()},
        "revision": revision,
        "harness_sha256": script_digest,
        "runner_sha256": runner_digest,
        "input_sha256": input_digest,
        "backend_id": BuiltinExactVectorBackend.backend_id,
        "packages": {"mdrack": "workspace", "mdrack-core": "workspace", "mdrack-sqlite": "workspace"},
        "config": {
            "cells": args.cells,
            "codecs": [codec.name for codec in codecs],
            "vector_fixture": "deterministic-normalized-finite-real-shaped-v1",
            "warmups": args.warmups,
            "repetitions": args.repetitions,
            "warm_queries": args.warm_queries,
            "candidate_limit": args.candidate_limit,
        },
        "parity_oracle": parity_oracle,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cells": results,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
