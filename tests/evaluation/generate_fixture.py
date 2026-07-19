"""Deterministically generate the public roadmap-scale evaluation fixture."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests.evaluation.contract_validator import (
        BENCHMARK_CONTRACT,
        CORPUS_CONTRACT,
        MIN_CASE_COUNTS,
        MIN_RESOURCE_COUNTS,
        QUERY_CONTRACT,
        SCHEMA_VERSION,
        seal_document,
        validate_contracts,
    )
else:
    from contract_validator import (
        BENCHMARK_CONTRACT,
        CORPUS_CONTRACT,
        MIN_CASE_COUNTS,
        MIN_RESOURCE_COUNTS,
        QUERY_CONTRACT,
        SCHEMA_VERSION,
        seal_document,
        validate_contracts,
    )

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "corpus-v1"
QUERY_DIR = ROOT / "queries-v1"
BENCHMARK_DIR = ROOT / "benchmark-v1"
ARTIFACT_DIR = CORPUS_DIR / "artifacts"
SOURCE_NAMESPACE = "mdrack-public-eval-v1"

TOPICS = (
    "deterministic indexing",
    "portable identifiers",
    "transaction boundaries",
    "temporal retrieval",
    "frame caption evidence",
    "hybrid ranking",
    "resource grouping",
    "privacy safe reports",
    "schema migration",
    "vector fingerprints",
)


def opaque(prefix: str, label: str) -> str:
    return f"{prefix}_{hashlib.sha256(label.encode()).hexdigest()[:32]}"


def sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> str:
    return write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def provenance(index: int) -> dict[str, object]:
    return {
        "classification": "synthetic",
        "license_spdx": "CC0-1.0",
        "origin": f"deterministic-generator-case-{index:03d}",
        "pii_status": "reviewed_no_pii",
        "publishable": True,
    }


def unit(
    resource_id: str,
    ordinal: int,
    unit_kind: str,
    representation_kind: str,
    **temporal: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "unit_id": opaque("unit", f"{resource_id}:{representation_kind}:{ordinal}"),
        "unit_kind": unit_kind,
        "representation_kind": representation_kind,
        "ordinal": ordinal,
    }
    result.update(temporal)
    return result


def resource_entry(
    index: int,
    resource_kind: str,
    media_type: str,
    representations: list[str],
    units: list[dict[str, object]],
    artifact_suffix: str,
    artifact: object,
) -> dict[str, Any]:
    resource_id = str(units[0].pop("_resource_id"))
    for item in units[1:]:
        item.pop("_resource_id", None)
    artifact_ref = f"artifacts/{resource_kind}-{index:02d}.{artifact_suffix}"
    artifact_path = CORPUS_DIR / artifact_ref
    digest = (
        write_text(artifact_path, str(artifact))
        if artifact_suffix == "md"
        else write_json(artifact_path, artifact)
    )
    return {
        "resource_id": resource_id,
        "resource_kind": resource_kind,
        "media_type": media_type,
        "source_namespace": SOURCE_NAMESPACE,
        "artifact_ref": artifact_ref,
        "artifact_sha256": digest,
        "content_sha256": digest,
        "representations": representations,
        "units": units,
        "provenance": provenance(index),
    }


def make_resources() -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    global_index = 0
    for index in range(20):
        global_index += 1
        resource_id = opaque("res", f"document:{index}")
        topic = TOPICS[index % len(TOPICS)]
        document_units = [
            {"_resource_id": resource_id, **unit(resource_id, 0, "text_chunk", "retrieval_text")},
            unit(resource_id, 1, "text_chunk", "retrieval_text"),
        ]
        markdown = (
            f"# Public document {index + 1}\n\n"
            f"Synthetic topic: {topic}. Marker lexical-document-{index:02d}.\n\n"
            f"## Verification\n\nA reproducible example explains {topic} without private data.\n"
        )
        resources.append(
            resource_entry(
                global_index,
                "document",
                "text/markdown",
                ["retrieval_text"],
                document_units,
                "md",
                markdown,
            )
        )
    for index in range(10):
        global_index += 1
        resource_id = opaque("res", f"image:{index}")
        topic = TOPICS[(index + 2) % len(TOPICS)]
        image_units = [
            {"_resource_id": resource_id, **unit(resource_id, 0, "whole_resource", "caption_text")}
        ]
        image_artifact: dict[str, Any] = {
            "resource_id": resource_id,
            "caption_text": f"Synthetic diagram about {topic}; marker lexical-image-{index:02d}.",
            "ocr_text": f"PUBLIC FIGURE {index + 1}: {topic.upper()}",
        }
        resources.append(
            resource_entry(
                global_index,
                "image",
                "application/vnd.mdrack.synthetic-image-text+json",
                ["ocr_text", "caption_text"],
                image_units,
                "json",
                image_artifact,
            )
        )
    for index in range(10):
        global_index += 1
        resource_id = opaque("res", f"audio:{index}")
        topic = TOPICS[(index + 4) % len(TOPICS)]
        audio_units = [
            {
                "_resource_id": resource_id,
                **unit(
                    resource_id,
                    ordinal,
                    "time_segment",
                    "audio_transcript",
                    start_ms=ordinal * 30_000,
                    end_ms=(ordinal + 1) * 30_000,
                ),
            }
            for ordinal in range(3)
        ]
        audio_artifact: dict[str, Any] = {
            "resource_id": resource_id,
            "duration_ms": 90_000,
            "passages": [
                {
                    "start_ms": ordinal * 30_000,
                    "end_ms": (ordinal + 1) * 30_000,
                    "text": f"Synthetic audio passage {ordinal + 1} about {topic}; marker audio-{index:02d}-{ordinal}.",
                }
                for ordinal in range(3)
            ],
        }
        resources.append(
            resource_entry(
                global_index,
                "audio",
                "application/vnd.mdrack.synthetic-transcript+json",
                ["audio_transcript"],
                audio_units,
                "json",
                audio_artifact,
            )
        )
    for index in range(10):
        global_index += 1
        resource_id = opaque("res", f"video:{index}")
        topic = TOPICS[(index + 6) % len(TOPICS)]
        video_units: list[dict[str, object]] = [
            {
                "_resource_id": resource_id,
                **unit(
                    resource_id,
                    ordinal,
                    "time_segment",
                    "audio_transcript",
                    start_ms=ordinal * 40_000,
                    end_ms=(ordinal + 1) * 40_000,
                ),
            }
            for ordinal in range(3)
        ]
        frames: list[dict[str, object]] = []
        if index < 5:
            for frame_ordinal, timestamp in enumerate((20_000, 80_000)):
                frame_id = opaque("frame", f"{resource_id}:{frame_ordinal}:{timestamp}")
                video_units.append(
                    unit(
                        resource_id,
                        3 + frame_ordinal,
                        "frame",
                        "frame_caption",
                        timestamp_ms=timestamp,
                        frame_id=frame_id,
                    )
                )
                frames.append(
                    {
                        "frame_id": frame_id,
                        "timestamp_ms": timestamp,
                        "caption": f"Synthetic frame {frame_ordinal + 1} illustrates {topic}.",
                    }
                )
        video_artifact: dict[str, Any] = {
            "resource_id": resource_id,
            "duration_ms": 120_000,
            "passages": [
                {
                    "start_ms": ordinal * 40_000,
                    "end_ms": (ordinal + 1) * 40_000,
                    "text": f"Synthetic video passage {ordinal + 1} about {topic}; marker video-{index:02d}-{ordinal}.",
                }
                for ordinal in range(3)
            ],
            "frames": frames,
        }
        representations = ["audio_transcript"] + (["frame_caption"] if frames else [])
        resources.append(
            resource_entry(
                global_index,
                "video",
                "application/vnd.mdrack.synthetic-video-text+json",
                representations,
                video_units,
                "json",
                video_artifact,
            )
        )
    return resources


def basis_for(unit_data: dict[str, object]) -> str:
    return {
        "retrieval_text": "document_text",
        "caption_text": "caption_text",
        "audio_transcript": "transcript_text",
        "frame_caption": "frame_caption_text",
    }[str(unit_data["representation_kind"])]


def judgment(resource: dict[str, Any], unit_data: dict[str, Any], grade: int) -> dict[str, object]:
    result: dict[str, object] = {
        "resource_id": resource["resource_id"],
        "unit_id": unit_data["unit_id"],
        "grade": grade,
        "basis": basis_for(unit_data),
    }
    if unit_data["unit_kind"] == "time_segment":
        result["evidence"] = {
            "kind": "time_interval",
            "start_ms": unit_data["start_ms"],
            "end_ms": unit_data["end_ms"],
        }
    elif unit_data["unit_kind"] == "frame":
        result["evidence"] = {
            "kind": "frame_timestamp",
            "timestamp_ms": unit_data["timestamp_ms"],
            "frame_id": unit_data["frame_id"],
        }
    return result


def allowed_for(resource: dict[str, Any], unit_data: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "resource_kinds": [resource["resource_kind"]],
        "representation_kinds": [unit_data["representation_kind"]],
        "unit_kinds": [unit_data["unit_kind"]],
    }


def case(
    ordinal: int,
    case_kind: str,
    mode: str,
    resource: dict[str, Any],
    unit_data: dict[str, Any],
    judgments: list[dict[str, object]],
    query_text: str,
    *,
    target: str = "unit",
    query_resource_id: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "query_id": opaque("qry", f"{case_kind}:{ordinal}"),
        "query_text": query_text,
        "case_kind": case_kind,
        "mode": mode,
        "target": target,
        "basis": basis_for(unit_data),
        "allowed": allowed_for(resource, unit_data),
        "cutoffs": {"recall": [5, 10], "mrr": 10, "ndcg": 10},
        "slice_tags": [
            f"mode:{case_kind}",
            f"resource:{resource['resource_kind']}",
            f"representation:{unit_data['representation_kind']}",
            f"unit:{unit_data['unit_kind']}",
            "language:en",
            "length:short",
        ],
        "judgments": judgments,
    }
    if query_resource_id is not None:
        result["query_resource_id"] = query_resource_id
    return result


def make_cases(resources: list[dict[str, Any]]) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    primary = [(resource, resource["units"][0]) for resource in resources]
    by_kind: dict[str, list[dict[str, Any]]] = {
        kind: [resource for resource in resources if resource["resource_kind"] == kind]
        for kind in MIN_RESOURCE_COUNTS
    }

    for index, (resource, unit_data) in enumerate(primary):
        cases.append(
            case(
                index,
                "lexical",
                "text",
                resource,
                unit_data,
                [judgment(resource, unit_data, 3)],
                f"exact public marker for {resource['resource_kind']} case {index + 1}",
            )
        )
    for index, (resource, unit_data) in enumerate(primary):
        siblings = by_kind[str(resource["resource_kind"])]
        related = siblings[(siblings.index(resource) + 1) % len(siblings)]
        related_unit = related["units"][0]
        cases.append(
            case(
                index,
                "semantic",
                "semantic",
                resource,
                unit_data,
                [judgment(resource, unit_data, 3), judgment(related, related_unit, 2)],
                f"conceptual explanation related to synthetic topic {index % len(TOPICS) + 1}",
            )
        )
    hybrid_resources = resources[:20] + resources[30:40]
    for index, resource in enumerate(hybrid_resources):
        unit_data = resource["units"][0]
        siblings = by_kind[str(resource["resource_kind"])]
        related = siblings[(siblings.index(resource) + 2) % len(siblings)]
        related_unit = related["units"][0]
        cases.append(
            case(
                index,
                "hybrid",
                "hybrid",
                resource,
                unit_data,
                [judgment(resource, unit_data, 3), judgment(related, related_unit, 1)],
                f"public marker and paraphrase for hybrid case {index + 1}",
            )
        )
    similarity_sources = resources[:20]
    for index, source in enumerate(similarity_sources):
        siblings = by_kind[str(source["resource_kind"])]
        target_resource = siblings[(siblings.index(source) + 1) % len(siblings)]
        secondary = siblings[(siblings.index(source) + 2) % len(siblings)]
        unit_data = source["units"][0]
        cases.append(
            case(
                index,
                "resource_similarity",
                "similarity",
                source,
                unit_data,
                [
                    {"resource_id": target_resource["resource_id"], "grade": 3, "basis": basis_for(unit_data)},
                    {"resource_id": secondary["resource_id"], "grade": 1, "basis": basis_for(unit_data)},
                ],
                f"find resources related to synthetic source {index + 1}",
                target="resource",
                query_resource_id=str(source["resource_id"]),
            )
        )
    timed_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    frame_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for resource in resources:
        for unit_data in resource["units"]:
            if unit_data["unit_kind"] == "time_segment":
                timed_candidates.append((resource, unit_data))
            elif unit_data["unit_kind"] == "frame":
                frame_candidates.append((resource, unit_data))
    timestamp_candidates = timed_candidates[:10] + frame_candidates[:10]
    for index, (resource, unit_data) in enumerate(timestamp_candidates):
        cases.append(
            case(
                index,
                "timestamp",
                "hybrid",
                resource,
                unit_data,
                [judgment(resource, unit_data, 3)],
                f"locate synthetic temporal evidence case {index + 1}",
            )
        )
    return cases


def main() -> None:
    resources = make_resources()
    resource_counts = Counter(str(resource["resource_kind"]) for resource in resources)
    videos_with_frames = sum(
        1 for resource in resources if any(unit_data["unit_kind"] == "frame" for unit_data in resource["units"])
    )
    corpus: dict[str, Any] = {
        "contract": CORPUS_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "corpus_id": opaque("corpus", "mdrack-public-eval-v1"),
        "corpus_version": "1.0.0",
        "source_namespace": SOURCE_NAMESPACE,
        "query_set_ref": {
            "path": "queries-v1/queries.json",
            "contract": QUERY_CONTRACT,
            "schema_version": SCHEMA_VERSION,
        },
        "policy_refs": {
            "builder": sha("public-prepared-artifact-builder-v1"),
            "parser": sha("markdown-parser-policy-v1"),
            "chunker": sha("evaluation-chunker-policy-v1"),
            "vector_profile": sha("provider-free-vector-profile-v1"),
        },
        "roadmap_scale": {
            "status": "satisfied",
            "required": {**MIN_RESOURCE_COUNTS, "videos_with_frames": 5},
            "actual": {**dict(resource_counts), "videos_with_frames": videos_with_frames},
        },
        "resources": resources,
    }
    seal_document(corpus)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    write_json(CORPUS_DIR / "manifest.json", corpus)

    cases = make_cases(resources)
    case_counts = Counter(str(item["case_kind"]) for item in cases)
    queries: dict[str, Any] = {
        "contract": QUERY_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "query_set_id": opaque("qry", "mdrack-public-query-set-v1"),
        "query_set_version": "1.0.0",
        "corpus_ref": corpus["contract_digest"],
        "roadmap_scale": {
            "status": "satisfied",
            "required": MIN_CASE_COUNTS,
            "actual": dict(case_counts),
        },
        "cases": cases,
    }
    seal_document(queries)
    QUERY_DIR.mkdir(parents=True, exist_ok=True)
    write_json(QUERY_DIR / "queries.json", queries)

    benchmark: dict[str, Any] = {
        "contract": BENCHMARK_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": opaque("benchmark", "mdrack-public-benchmark-v1"),
        "benchmark_version": "1.0.0",
        "corpus_ref": corpus["contract_digest"],
        "query_ref": queries["contract_digest"],
        "materialization": {
            "status": "gated_manifest",
            "gate": "W5-B13",
            "seed": 20260719,
            "artifact_policy": "generated-no-binaries",
        },
        "cells": [
            {"units": units, "dimensions": dimensions}
            for units in (1_000, 10_000, 50_000, 100_000)
            for dimensions in (384, 768, 1024)
        ],
        "operations": [
            "atomic_replace",
            "fts_query",
            "semantic_linear_scan",
            "hybrid_search",
            "resource_grouping",
            "weighted_rrf",
            "duplicate_lookup",
            "whole_resource_similarity",
            "close_reopen",
            "migration",
            "batch_import",
        ],
        "non_claims": [
            "Manifest cells are not materialized benchmark evidence.",
            "No latency, capacity, provider quality, or backend decision is claimed.",
        ],
    }
    seal_document(benchmark)
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    write_json(BENCHMARK_DIR / "manifest.json", benchmark)
    validate_contracts(
        CORPUS_DIR / "manifest.json",
        QUERY_DIR / "queries.json",
        BENCHMARK_DIR / "manifest.json",
    )
    print(
        json.dumps(
            {
                "corpus_digest": corpus["contract_digest"],
                "query_digest": queries["contract_digest"],
                "benchmark_digest": benchmark["contract_digest"],
                "resources": dict(resource_counts),
                "videos_with_frames": videos_with_frames,
                "queries": dict(case_counts),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
