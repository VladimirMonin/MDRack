"""A–D/optional E through the common public engine, never an alternate ranker.

Providers are explicitly supplied by the host. No provider/model is constructed
implicitly; all corpus, gold, scope and policy identities are sealed before indexing/encoding.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from mdrack.application.manifest import (
    ManifestError,
    decode_prepared_resource_manifest,
    encode_prepared_resource_manifest,
)
from mdrack.application.media_search import MediaSearchRequest, MediaSearchResult
from mdrack.application.transcript_ingestion import TranscriptIngestionService
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import EmbeddingResult, EncodingRole, ImageInput, PreparedInput, TextInput
from mdrack.domain.native_media import NativeMediaKind, NativeMediaPolicy
from mdrack.eval import native_corpus, quality_io, quality_v2
from mdrack.eval.native_corpus import (
    NativeBundle,
    NativeResource,
    array,
    choice,
    decode,
    evaluator_input,
    fail,
    integer,
    obj,
    require_evaluable,
    text,
    validate_native_bundle,
)
from mdrack.eval.quality import QualityEvaluationError, fingerprint
from mdrack.eval.quality_io import dumps_quality_report
from mdrack.eval.quality_v2 import evaluate_quality_v2
from mdrack.ports.embeddings import EmbeddingError, PreparedEmbeddingProvider
from mdrack.public_api import MDRackEngine
from mdrack_core import (
    BranchExecutionError,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_core.domain.common import to_plain_json
from mdrack_media import TranscriptArtifact
from mdrack_sqlite import SQLiteCatalog


def _reason(exc: Exception) -> str:
    if isinstance(exc, BranchExecutionError):
        return "search_branch_failure"
    if isinstance(exc, EmbeddingError):
        return "embedding_port_failure"
    if isinstance(exc, QualityEvaluationError):
        return "quality_contract_refused"
    if isinstance(exc, ManifestError):
        return "manifest_contract_refused"
    if isinstance(exc, OSError):
        return "local_artifact_io"
    allowed = {
        "unsupported_embedding_route",
        "embedding_input_too_large",
        "embedding_duration_too_large",
        "embedding_dimension_mismatch",
        "invalid_cosine_vector",
        "provider_binding_changed",
        "provider_space_changed",
        "fixed_projection_missing",
        "fixed_enrichment_missing",
        "native_baseline_missing",
        "cutoff_exceeds_candidate_budget",
        "public_scope_violation",
        "public_evidence_missing",
        "public_revision_mismatch",
        "paired_fixed_projection_missing",
        "native_query_weight_override_unavailable",
    }
    value = str(exc)
    return value if value in allowed else "provider_or_materialization_prerequisite"


def _capabilities(provider: PreparedEmbeddingProvider) -> list[dict[str, object]]:
    return [
        {
            "role": i.role.value,
            "input_modality": i.input_modality,
            "candidate_modalities": list(i.candidate_modalities),
            "max_input_bytes": i.max_input_bytes,
            "max_duration_ms": i.max_duration_ms,
        }
        for i in provider.capabilities.inputs
    ]


def _space(provider: PreparedEmbeddingProvider) -> dict[str, object]:
    s = provider.space
    return {
        "space_id": s.space_id,
        "dimensions": s.dimensions,
        "metric": s.metric,
        "fingerprint": s.fingerprint,
        "metadata": to_plain_json(s.metadata),
    }


class _Meter:
    def __init__(self, provider: PreparedEmbeddingProvider) -> None:
        self.delegate = provider
        self.space = provider.space
        self.capabilities = provider.capabilities
        self.calls = 0
        self.bytes = 0
        self.elapsed_ms = 0.0

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        if self.delegate.space != self.space or self.delegate.capabilities != self.capabilities:
            fail("provider_binding_changed")
        self.capabilities.validate(value, role)
        self.calls += 1
        self.bytes += value.byte_size
        start = time.perf_counter()
        try:
            result = await self.delegate.encode(value, role)
        finally:
            self.elapsed_ms += (time.perf_counter() - start) * 1000
        if result.space != self.space:
            fail("provider_space_changed")
        return result


@dataclass(frozen=True)
class RunConfig:
    document: dict[str, object]
    scenarios: tuple[str, ...]
    split: str
    budget: int
    lexical_weight: float
    vector_weight: float
    mode_ab: str
    video_kind: str
    policy: dict[str, object] | None

    @classmethod
    def parse(cls, raw: object) -> RunConfig:
        doc = decode(json.dumps(raw, allow_nan=False))
        obj(
            doc,
            {
                "contract",
                "schema_version",
                "split",
                "scenarios",
                "candidate_budget",
                "lexical_weight",
                "vector_weight",
                "mode_ab",
                "video_kind",
                "policy",
            },
        )
        if (
            doc["contract"] != "mdrack.native-experiment"
            or type(doc["schema_version"]) is not int
            or doc["schema_version"] != 2
        ):
            fail("invalid_experiment_contract")
        scenarios = tuple(text(s) for s in array(doc["scenarios"]))
        if scenarios not in (("A", "B", "C", "D"), ("A", "B", "C", "D", "E")):
            fail("scenario_matrix_required")
        weights = []
        for name in ("lexical_weight", "vector_weight"):
            value = doc[name]
            if (
                type(value) not in (float, int)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
                or value <= 0
            ):
                fail("invalid_weight")
            weights.append(float(value))
        policy = (
            None
            if doc["policy"] is None
            else obj(
                doc["policy"],
                {
                    "critical_slices",
                    "max_regression",
                    "min_gain",
                    "max_search_ms",
                    "max_index_bytes",
                    "max_provider_calls",
                    "max_false_positives",
                    "min_independent_groups",
                },
            )
        )
        if policy is not None:
            if not array(policy["critical_slices"]):
                fail("empty_critical_slices")
            for s in array(policy["critical_slices"]):
                text(s)
            for name in (
                "max_regression",
                "min_gain",
                "max_search_ms",
                "max_index_bytes",
                "max_provider_calls",
                "max_false_positives",
            ):
                value = policy[name]
                if (
                    not isinstance(value, (float, int))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or value < 0
                ):
                    fail("invalid_fitness_policy")
            integer(policy["min_independent_groups"], 1, 10000)
        return cls(
            doc,
            scenarios,
            choice(doc["split"], {"dev", "held_out"}),
            integer(doc["candidate_budget"], 1, 10000),
            weights[0],
            weights[1],
            choice(doc["mode_ab"], {"semantic", "hybrid"}),
            choice(doc["video_kind"], {"video", "frames"}),
            policy,
        )


async def _projection(
    engine: MDRackEngine, resource: NativeResource, provider: _Meter, temporary: Path | None = None
) -> None:
    fixed = resource.projection
    if fixed is None or fixed.origin == "agent_draft":
        fail("fixed_projection_missing")
    if fixed.kind == "asr":
        if temporary is None or resource.kind not in {"audio", "video"}:
            fail("timed_projection_workdir_required")
        artifact = TranscriptArtifact.from_dict(decode(fixed.content))
        if artifact.resource_id != resource.resource_id:
            fail("fixed_transcript_owner_mismatch")
        # The existing provider-free materializer owns grouping/normalization.
        # Its catalog is read-only and unused during prepare; all publication is
        # still through the real public engine's complete-manifest API.
        with SQLiteCatalog.open_readonly(temporary.parent / ".mdrack" / "catalog.sqlite3") as catalog:
            normalized = TranscriptIngestionService(catalog).prepare(
                artifact,
                resource_kind=resource.kind,
                media_type=resource.media_type,
                source_namespace=resource.namespace,
                source_locator=Locator("external_record", {"ref": resource.artifact.path}),
            )
        reps = tuple(r for r in normalized.representations if r.representation_kind == "timed_passage")
        rep_ids = {r.representation_id for r in reps}
        units = tuple(u for u in normalized.units if u.representation_id in rep_ids)
        vectors = []
        for unit in units:
            encoded = await provider.encode(TextInput(text(unit.text), fixed.sha256), EncodingRole.DOCUMENT)
            vectors.append(VectorRecord(unit.unit_id, provider.space.space_id, encoded.vector))
        owner = replace(
            normalized.resource,
            metadata={
                **normalized.resource.metadata,
                "source_revision": resource.artifact.sha256,
                "provenance": {"raw_source_sha256": resource.artifact.sha256},
            },
        )
        baseline = PreparedResourceBatch(owner, reps, units, (provider.space,), tuple(vectors), normalized.facets)
        engine.import_resource_manifest(encode_prepared_resource_manifest(baseline))
        return
    value = TextInput(fixed.content, fixed.sha256)
    encoded = await provider.encode(value, EncodingRole.DOCUMENT)
    loc = Locator("external_record", {"ref": resource.artifact.path})
    rep_id = "eval-rep-" + fingerprint([resource.resource_id, fixed.sha256])[7:31]
    unit_id = "eval-unit-" + fingerprint([resource.resource_id, fixed.sha256])[7:31]
    batch = PreparedResourceBatch(
        ResourceRecord(
            resource.resource_id,
            resource.kind,
            resource.media_type,
            resource.namespace,
            loc,
            metadata={
                "source_revision": resource.artifact.sha256,
                "provenance": {"raw_source_sha256": resource.artifact.sha256},
            },
        ),
        (RepresentationRecord(rep_id, resource.resource_id, "retrieval_text", "text", fixed.content),),
        (SearchUnitRecord(unit_id, resource.resource_id, rep_id, "whole_resource", "text", fixed.content, loc, 0),),
        (provider.space,),
        (VectorRecord(unit_id, provider.space.space_id, encoded.vector),),
    )
    engine.import_resource_manifest(encode_prepared_resource_manifest(batch))


async def _native(engine: MDRackEngine, resource: NativeResource, root: Path, cfg: RunConfig, provider: _Meter) -> None:
    path = resource.artifact.resolve(root)
    if resource.kind == "image":
        await engine.ingest_native_image(
            path,
            resource_id=resource.resource_id,
            source_namespace=resource.namespace,
            source_ref=resource.artifact.path,
            expected_revision=resource.artifact.sha256,
        )
    elif resource.kind == "document":
        await _projection(engine, resource, provider)
    else:
        kind: NativeMediaKind = (
            "audio" if resource.kind == "audio" else "frames" if cfg.video_kind == "frames" else "video"
        )
        await engine.ingest_native_media(
            path,
            kind=kind,
            resource_id=resource.resource_id,
            source_namespace=resource.namespace,
            source_ref=resource.artifact.path,
            expected_revision=resource.artifact.sha256,
            policy=NativeMediaPolicy(),
        )


async def _enrich(engine: MDRackEngine, resource: NativeResource, provider: _Meter, path: Path, *, dense: bool) -> None:
    fixed = resource.enrichment
    if fixed is None or fixed.origin == "agent_draft":
        fail("fixed_enrichment_missing")
    if fixed.kind not in {"ocr", "asr", "caption", "class_description", "text"}:
        fail("invalid_fixed_artifact_kind")
    # Timed ASR is an existing strict TranscriptArtifact document, never a class
    # label or a full-duration interval synthesized by this runner.
    artifact: object = decode(fixed.content) if fixed.kind == "asr" else fixed.content
    kind = "transcript" if fixed.kind == "asr" else "ocr" if fixed.kind == "ocr" else "text"
    vectors = []
    if dense:
        previous = decode_prepared_resource_manifest(engine.export_resource_manifest(resource.resource_id))
        rep_ids = set()
        for representation in previous.representations:
            info = representation.metadata.get("enrichment_v1")
            if isinstance(info, Mapping) and info.get("purpose") == "native-eval:fixed":
                rep_ids.add(representation.representation_id)
        units = sorted((u for u in previous.units if u.representation_id in rep_ids), key=lambda u: u.ordinal)
        if not units:
            fail("paired_fixed_projection_missing")
        for unit in units:
            encoded = await provider.encode(TextInput(text(unit.text), fixed.sha256), EncodingRole.DOCUMENT)
            vectors.append(list(encoded.vector))
    artifact_hash = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    value = {
        "contract": "mdrack.prepared-enrichment",
        "version": 1,
        "binding": engine.prepared_enrichment_binding(resource.resource_id),
        "artifact_revision": fingerprint([fixed.sha256, dense, _space(provider)]),
        "producer": "native-corpus-fixed-artifact",
        "recipe": fixed.kind,
        "manual": True,
        "artifact": artifact,
        "artifact_sha256": artifact_hash,
        "dependencies": [],
        "dense": None
        if not dense
        else {
            "space": _space(provider),
            "recipe": "fixed-text-document",
            "artifact_sha256": artifact_hash,
            "vectors": vectors,
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    result = engine.enrich_prepared(
        resource.resource_id,
        path,
        kind=kind,
        purpose="native-eval:fixed",
        lexical=True,
        dense=dense,
        artifact_store=path.parent / "artifacts",
        require_durable=True,
    )
    if result.get("publication_status") not in {"published", "already_published"}:
        fail("enrichment_not_published")


def _stats(engine: MDRackEngine, resources: list[NativeResource], directory: Path) -> dict[str, object]:
    counts = {"resources": 0, "representations": 0, "units": 0, "vectors": 0}
    for r in resources:
        inspected = engine.inspect_resource(r.resource_id)
        counts["resources"] += 1
        for key in ("representations", "units", "vectors"):
            counts[key] += inspected.counts[key]
    return {
        **counts,
        "index_bytes": sum(p.stat().st_size for p in directory.rglob("catalog.sqlite3*") if p.is_file()),
        "actual_vector_comparisons": None,
        "peak_rss_bytes": None,
    }


async def _query(
    engine: MDRackEngine,
    q: dict[str, object],
    resource_map: dict[str, NativeResource],
    cfg: RunConfig,
    stage: str,
    media_root: Path,
    provider: _Meter,
) -> MediaSearchResult:
    eligible = [resource_map[text(i)] for i in array(q["eligible"])]
    # Resource-specific source namespaces enforce eligible IDs inside every branch
    # before candidate limits; this is NOT post-limit filtering of a global result.
    modalities = (
        {"text"}
        if stage in {"A", "B"}
        else {
            "image" if r.kind == "video" and cfg.video_kind == "frames" else "text" if r.kind == "document" else r.kind
            for r in eligible
        }
    )
    if stage in {"D", "E"}:
        modalities.add("text")
    scope = SearchScope(source_namespaces=tuple(r.namespace for r in eligible), modalities=tuple(sorted(modalities)))
    inp = obj(q["input"])
    modality = text(inp["modality"])
    route_modality = "image" if modality == "video" and cfg.video_kind == "frames" else modality
    provider.capabilities.require(EncodingRole.QUERY, route_modality, tuple(sorted(modalities)))
    limit = max(integer(k, 1, 10000) for k in array(q["cutoffs"]))
    if limit > cfg.budget:
        fail("cutoff_exceeds_candidate_budget")
    mode = cfg.mode_ab if stage in {"A", "B"} else "semantic" if stage == "C" else "hybrid"
    if modality in {"audio", "video"}:
        if cfg.vector_weight != 1.0:
            fail("native_query_weight_override_unavailable")
        resource = resource_map[text(inp["resource_id"])]
        return await engine.search_native_media(
            resource.artifact.resolve(media_root),
            kind="audio" if modality == "audio" else "frames" if cfg.video_kind == "frames" else "video",
            scope=scope,
            target=text(q["target"]),
            mode=mode,
            limit=limit,
            candidate_budget=cfg.budget,
            expected_revision=resource.artifact.sha256,
        )
    query = (
        TextInput(text(inp["text"]), fingerprint(inp))
        if modality == "text"
        else ImageInput(
            resource_map[text(inp["resource_id"])].artifact.resolve(media_root).read_bytes(),
            resource_map[text(inp["resource_id"])].media_type,
            fingerprint(inp),
        )
    )
    return await engine.search_media(
        MediaSearchRequest(
            query,
            scope,
            target=text(q["target"]),
            mode=mode,
            limit=limit,
            candidate_budget=cfg.budget,
            lexical_weight=cfg.lexical_weight,
            vector_weight=cfg.vector_weight,
        )
    )


def _ranked(
    result: MediaSearchResult, resources: dict[str, NativeResource], q: dict[str, object]
) -> list[dict[str, object]]:
    rows = []
    for item in array(obj(result.to_dict())["results"]):
        record = obj(item)
        rid = text(record["resource_id"])
        if rid not in array(q["eligible"]):
            fail("public_scope_violation")
        evidence = array(record["evidence"])
        if not evidence:
            fail("public_evidence_missing")
        unit = obj(evidence[0])
        if unit["source_revision"] != resources[rid].artifact.sha256:
            fail("public_revision_mismatch")
        payload = obj(obj(unit["locator"])["payload"])
        intervals = [[payload["start_ms"], payload["end_ms"]]] if "start_ms" in payload and "end_ms" in payload else []
        rows.append(
            {
                "resource_id": rid,
                "source_revision": unit["source_revision"],
                "unit_id": unit["unit_id"],
                "intervals": intervals if q["match"] == "temporal" else [],
                "timestamp_ms": payload.get("timestamp_ms") if q["match"] == "temporal" else None,
            }
        )
    return rows


async def run_native_experiment_async(
    bundle: NativeBundle,
    configuration: object,
    *,
    media_root: Path,
    output_root: Path,
    provider_a: PreparedEmbeddingProvider,
    provider_b: PreparedEmbeddingProvider,
) -> dict[str, object]:
    bundle = validate_native_bundle(bundle.document, media_root=media_root)
    require_evaluable(bundle)
    cfg = RunConfig.parse(configuration)
    queries = [q for q in bundle.queries if q["split"] == cfg.split]
    if not queries:
        fail("split_has_no_queries")
    resources = {r.resource_id: r for r in bundle.resources}
    eligible_ids = {text(i) for q in queries for i in array(q["eligible"])}
    selected = [r for r in bundle.resources if r.resource_id in eligible_ids]
    if output_root.resolve().is_relative_to(media_root.resolve()) or media_root.resolve().is_relative_to(
        output_root.resolve()
    ):
        fail("output_overlaps_media")
    a, b = _Meter(provider_a), _Meter(provider_b)
    implementation_ref = fingerprint(
        [
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                Path(__file__),
                Path(native_corpus.__file__),
                Path(quality_v2.__file__),
                Path(quality_io.__file__),
            )
        ]
    )
    plan = {
        "adapter_evaluator_ref": implementation_ref,
        "bundle_ref": fingerprint(bundle.document),
        "config_ref": fingerprint(cfg.document),
        "execution": {
            "scenarios": list(cfg.scenarios),
            "split": cfg.split,
            "candidate_budget": cfg.budget,
            "lexical_weight": cfg.lexical_weight,
            "vector_weight": cfg.vector_weight,
            "mode_ab": cfg.mode_ab,
            "video_kind": cfg.video_kind,
        },
        "policy": None
        if cfg.policy is None
        else {
            **{k: v for k, v in cfg.policy.items() if k != "critical_slices"},
            "critical_slice_refs": [fingerprint(s) for s in array(cfg.policy["critical_slices"])],
        },
        "corpus_ref": bundle.corpus_ref,
        "query_ref": bundle.queries_ref,
        "judgments_ref": bundle.judgments_ref,
        "groups_ref": bundle.groups_ref,
        "profiles": [fingerprint(_space(a)), fingerprint(_space(b))],
        "capabilities": [_capabilities(a), _capabilities(b)],
        "native_policy": asdict(NativeMediaPolicy()),
        "native_policy_ref": NativeMediaPolicy().fingerprint,
        "projection_recipe": "fixed-text-or-normalized-timed-transcript-v1",
    }
    plan_ref = fingerprint(plan)
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / "plan-ref.txt").write_text(plan_ref + "\n", encoding="utf-8")
    (output_root / "plan.json").write_text(
        json.dumps(plan, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    stages: list[dict[str, object]] = []
    cells: list[dict[str, object]] = []
    completed = 0
    for label, provider in (("A", a), ("B", b), ("multimodal", b)):
        directory = output_root / label
        directory.mkdir()
        (directory / ".mdrack").mkdir()
        with SQLiteCatalog.create_v2(directory / ".mdrack" / "catalog.sqlite3"):
            pass
        stage_names = (label,) if label != "multimodal" else tuple(s for s in cfg.scenarios if s in {"C", "D", "E"})
        with MDRackEngine(root=directory, config=MDRackConfig(), prepared_embedding_provider=provider) as engine:
            prepared = False
            for stage in stage_names:
                preparation_started = time.perf_counter()
                preparation_error = False
                preparation_reason = "preparation_prerequisite"
                try:
                    if stage in {"A", "B"}:
                        for n, r in enumerate(selected):
                            await _projection(engine, r, provider, directory / f"projection-{n}.json")
                    elif stage == "C":
                        for r in selected:
                            await _native(engine, r, media_root, cfg, provider)
                        prepared = True
                    else:
                        if not prepared:
                            fail("native_baseline_missing")
                        for n, r in enumerate(selected):
                            await _enrich(engine, r, provider, directory / f"fixed-{n}.json", dense=stage == "E")
                except (ValueError, OSError, ManifestError, EmbeddingError) as exc:
                    preparation_error = True
                    preparation_reason = _reason(exc)
                preparation_elapsed_ms = (time.perf_counter() - preparation_started) * 1000
                successful_queries: list[dict[str, object]] = []
                ranked: list[list[dict[str, object]]] = []
                stage_cells = []
                for ordinal, q in enumerate(queries, 1):
                    start = time.perf_counter()
                    cell: dict[str, object] = {
                        "scenario": stage,
                        "query_ordinal": ordinal,
                        "slice_ref": fingerprint(q["slice"]),
                        "group_ref": fingerprint(q["group"]),
                        "status": "BLOCKED",
                        "search_latency_ms": None,
                    }
                    if not preparation_error:
                        try:
                            found = await _query(engine, q, resources, cfg, stage, media_root, provider)
                            values = _ranked(found, resources, q)
                            # Validate before adding a case: complete/unjudged failures
                            # remain visible blocked cells, never fabricated zero scores.
                            validate = evaluator_input(bundle.document, [q], resources, plan_ref, [values])
                            evaluate_quality_v2(validate)
                            successful_queries.append(q)
                            ranked.append(values)
                            cell.update(
                                status="EXECUTED",
                                effective_mode=found.effective_mode,
                                search_latency_ms=(time.perf_counter() - start) * 1000,
                            )
                        except (ValueError, OSError, ManifestError, EmbeddingError, BranchExecutionError) as exc:
                            cell["reason"] = _reason(exc)
                    else:
                        cell["reason"] = preparation_reason
                    stage_cells.append(cell)
                quality = None
                if successful_queries:
                    quality = evaluate_quality_v2(
                        evaluator_input(bundle.document, successful_queries, resources, plan_ref, ranked)
                    )
                    (directory / f"quality-{stage}.json").write_text(dumps_quality_report(quality), encoding="utf-8")
                complete = len(successful_queries) == len(queries)
                completed += int(complete)
                cells.extend(stage_cells)
                stats = None if preparation_error else _stats(engine, selected, directory)
                stages.append(
                    {
                        "scenario": stage,
                        "status": "COMPLETE" if complete else "BLOCKED",
                        "quality": quality,
                        "case_ordinals": [i + 1 for i, c in enumerate(stage_cells) if c["status"] == "EXECUTED"],
                        "preparation_elapsed_ms": preparation_elapsed_ms,
                        "stats": stats,
                        "provider_calls_cumulative": provider.calls,
                        "provider_bytes_cumulative": provider.bytes,
                        "provider_elapsed_ms_cumulative": provider.elapsed_ms,
                    }
                )
    paired = _pairs(stages, queries)
    comparisons = _pairs(stages, queries, edges=(("A", "B"), ("B", "C")))
    fitness = _fitness(cfg, bundle, stages, cells, paired, comparisons)
    return {
        "contract": "mdrack.native-experiment-report",
        "schema_version": 2,
        "evidence": bundle.evidence,
        "plan_ref": plan_ref,
        "policy_ref": fingerprint(cfg.policy),
        "corpus_ref": bundle.corpus_ref,
        "query_ref": bundle.queries_ref,
        "judgments_ref": bundle.judgments_ref,
        "groups_ref": bundle.groups_ref,
        "profile_refs": [fingerprint(_space(a)), fingerprint(_space(b))],
        "capability_limits": [_capabilities(a), _capabilities(b)],
        "candidate_budget_per_branch": cfg.budget,
        "native_policy_ref": NativeMediaPolicy().fingerprint,
        "native_policy": asdict(NativeMediaPolicy()),
        "adapter_evaluator_ref": implementation_ref,
        "planned_cells": len(queries) * len(cfg.scenarios),
        "executed_cells": sum(c["status"] == "EXECUTED" for c in cells),
        "completed_scenarios": completed,
        "cells": cells,
        "stages": stages,
        "paired": paired,
        "comparisons": comparisons,
        "fitness": fitness,
        "search_api": "MDRackEngine.search_media/search_native_media",
        "provider_cost_currency": None,
        "uncertainty": {
            "unit": "source_group",
            "independent_groups": len({text(q["group"]) for q in queries}),
            "confidence_interval": None,
            "method": "not_estimated",
        },
        "semantic_quality_claim": False,
    }


def _pairs(
    stages: list[dict[str, object]],
    queries: list[dict[str, object]],
    *,
    edges: tuple[tuple[str, str], ...] = (("C", "D"), ("D", "E")),
) -> list[dict[str, object]]:
    pairs = []
    by_name = {text(s["scenario"]): s for s in stages}
    for before, after in edges:
        if after not in by_name:
            continue
        left, right = by_name[before], by_name[after]
        left_rows = (
            {}
            if left["quality"] is None
            else dict(zip(array(left["case_ordinals"]), array(obj(left["quality"])["results"]), strict=True))
        )
        right_rows = (
            {}
            if right["quality"] is None
            else dict(zip(array(right["case_ordinals"]), array(obj(right["quality"])["results"]), strict=True))
        )
        for ordinal, q in enumerate(queries, 1):
            a, b = left_rows.get(ordinal), right_rows.get(ordinal)
            delta = None
            if a is not None and b is not None:
                la = obj(array(obj(a)["cutoffs"])[-1])["ndcg"]
                rb = obj(array(obj(b)["cutoffs"])[-1])["ndcg"]
                if isinstance(la, (int, float)) and isinstance(rb, (int, float)):
                    delta = float(rb) - float(la)
            other_deltas: dict[str, object] = {}
            for metric in ("recall", "mrr", "negative_returned"):
                difference = None
                if a is not None and b is not None:
                    lv = obj(array(obj(a)["cutoffs"])[-1])[metric]
                    rv = obj(array(obj(b)["cutoffs"])[-1])[metric]
                    if isinstance(lv, (float, int)) and isinstance(rv, (float, int)):
                        difference = float(rv) - float(lv)
                other_deltas["false_positives_delta" if metric == "negative_returned" else metric + "_delta"] = (
                    difference
                )
            pairs.append(
                {
                    **other_deltas,
                    "before": before,
                    "after": after,
                    "query_ordinal": ordinal,
                    "group_ref": fingerprint(q["group"]),
                    "slice_ref": fingerprint(q["slice"]),
                    "ndcg_delta": delta,
                    "status": "PAIRED" if a is not None and b is not None else "BLOCKED",
                }
            )
    return pairs


def _fitness(
    cfg: RunConfig,
    bundle: NativeBundle,
    stages: list[dict[str, object]],
    cells: list[dict[str, object]],
    paired: list[dict[str, object]],
    comparisons: list[dict[str, object]],
) -> str:
    if cfg.policy is None:
        return "BLOCKED_POLICY_MISSING"
    if any(s["status"] != "COMPLETE" for s in stages):
        return "BLOCKED_COVERAGE"
    critical = {fingerprint(s) for s in array(cfg.policy["critical_slices"])}
    if not critical <= {text(c["slice_ref"]) for c in cells}:
        return "BLOCKED_CRITICAL_SLICE"

    def number(name: str) -> float:
        value = cfg.policy[name] if cfg.policy is not None else 0
        if not isinstance(value, (int, float)):
            fail("invalid_policy_number")
        return float(value)

    if len({text(c["group_ref"]) for c in cells}) < number("min_independent_groups"):
        return "BLOCKED_INDEPENDENT_GROUPS"
    for stage in stages:
        stats = obj(stage["stats"])
        for result in array(obj(stage["quality"])["results"]):
            for cutoff in array(obj(result)["cutoffs"]):
                fp = obj(cutoff)["negative_returned"]
                if isinstance(fp, (int, float)) and fp > number("max_false_positives"):
                    return "POLICY_EXCEEDED"
        if integer(stats["index_bytes"], 0, 2**53 - 1) > number("max_index_bytes"):
            return "POLICY_EXCEEDED"
        if integer(stage["provider_calls_cumulative"], 0, 2**53 - 1) > number("max_provider_calls"):
            return "POLICY_EXCEEDED"
    for cell in cells:
        latency = cell["search_latency_ms"]
        if isinstance(latency, (float, int)) and latency > number("max_search_ms"):
            return "POLICY_EXCEEDED"
    for pair in comparisons + paired:
        for metric in ("recall", "mrr"):
            metric_delta = pair[metric + "_delta"]
            if isinstance(metric_delta, (float, int)) and metric_delta < -number("max_regression"):
                return "POLICY_EXCEEDED"
        delta = pair["ndcg_delta"]
        if isinstance(delta, (float, int)) and delta < -number("max_regression"):
            return "POLICY_EXCEEDED"
        if pair in paired and isinstance(delta, (float, int)) and delta < number("min_gain"):
            return "POLICY_EXCEEDED"
    return "SYNTHETIC_MECHANICS_ONLY" if bundle.evidence == "synthetic" else "POLICY_SATISFIED"


def run_native_experiment(
    bundle: NativeBundle,
    configuration: object,
    *,
    media_root: Path,
    output_root: Path,
    provider_a: PreparedEmbeddingProvider,
    provider_b: PreparedEmbeddingProvider,
) -> dict[str, object]:
    return asyncio.run(
        run_native_experiment_async(
            bundle,
            configuration,
            media_root=media_root,
            output_root=output_root,
            provider_a=provider_a,
            provider_b=provider_b,
        )
    )
