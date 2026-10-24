"""Fully-judged synthetic mechanics through real public engine/SQLite, not model quality."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import wave
import zlib
from pathlib import Path

import pytest

from mdrack.domain.embedding_inputs import (
    EmbeddingResult,
    EncodingRole,
    InputCapability,
    PreparedInput,
    RouteCapabilities,
)
from mdrack.eval.native_corpus import NativeCorpusError, array, decode, obj, require_evaluable, validate_native_bundle
from mdrack.eval.native_runner import run_native_experiment
from mdrack.ingestion.transcripts import read_transcript
from mdrack.ports.embeddings import EmbeddingError
from mdrack_core import BranchExecutionError, EmbeddingSpaceRecord, ErrorCategory
from mdrack_media import ProducerFingerprint, resource_id


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def png(red: int, blue: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes((0, red, 0, blue))))
        + chunk(b"IEND", b"")
    )


class ScriptedProvider:
    space = EmbeddingSpaceRecord(
        "eval-scripted",
        2,
        "cosine",
        "scripted-not-a-model",
        metadata={"vector_value_policy": "ieee754-f32-canonical-v1", "vector_codec": "ieee754-f32-le-v1"},
    )
    capabilities = RouteCapabilities(
        tuple(
            InputCapability(role, m, ("text", "image", "audio", "video"), 2000000, 10000)
            for role in EncodingRole
            for m in ("text", "image", "audio", "video")
        )
    )

    def __init__(self) -> None:
        self.calls = 0

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        self.capabilities.validate(value, role)
        self.calls += 1
        if value.modality == "text":
            vector = (0.0, 1.0) if "blue" in getattr(value, "text", "") else (1.0, 0.0)
        else:
            # Deliberately misleading native vectors: tests must measure, not assume, D gains.
            vector = (0.0, 1.0) if getattr(value, "content", b"") == png(255, 0) else (1.0, 0.0)
        return EmbeddingResult(vector, self.space)


def fixture(root: Path) -> dict[str, object]:
    root.mkdir()
    credit = b"Synthetic test data authored for deterministic software mechanics. CC0-1.0."
    (root / "credit.txt").write_bytes(credit)
    resources = []
    groups = []
    for identity, data, description, split in (
        ("red", png(255, 0), "scarlet red object", "dev"),
        ("blue", png(0, 255), "blue alternative object", "dev"),
        ("held", png(100, 100), "held out material", "held_out"),
    ):
        (root / f"{identity}.png").write_bytes(data)
        fixed = {"kind": "ocr", "text": description, "sha256": sha(description.encode()), "origin": "synthetic"}
        resources.append(
            {
                "id": identity,
                "kind": "image",
                "media_type": "image/png",
                "source": "synthetic",
                "artifact": {"path": f"{identity}.png", "sha256": sha(data), "bytes": len(data)},
                "attribution": {"path": "credit.txt", "sha256": sha(credit), "bytes": len(credit)},
                "group": f"group-{identity}",
                "split": split,
                "media_license": "CC0-1.0",
                "annotation_license": "CC0-1.0",
                "local_reviewed": True,
                "publishable": True,
                "projection": fixed,
                "enrichment": fixed,
            }
        )
        groups.append(
            {
                "id": identity,
                "group": f"group-{identity}",
                "split": split,
                "parents": [],
                "content_ref": sha(data),
                "kind": "source",
            }
        )
    queries = []
    for identity, query_text, slice_name, negative in (
        ("en", "scarlet", "en-image", False),
        ("ru", "алый", "ru-image", False),
        ("negative", "unknown", "negative", True),
    ):
        judgments = [
            {
                "judgment_id": r["id"],
                "grade": 0 if negative or r["id"] == "blue" else 3,
                "resource_id": r["id"],
                "source_revision": r["artifact"]["sha256"],
                "unit_id": None,
                "intervals": [],
                "timestamp_ms": None,
            }
            for r in resources[:2]
        ]
        queries.append(
            {
                "id": identity,
                "split": "dev",
                "slice": slice_name,
                "group": "group-red",
                "input": {"modality": "text", "text": query_text, "resource_id": None},
                "eligible": ["red", "blue"],
                "case_type": "negative" if negative else "positive",
                "target": "resource",
                "match": "identity",
                "cutoffs": [1, 2],
                "judgment_policy": "complete",
                "temporal_policy": None,
                "judgments": judgments,
                "adjudicated": True,
                "origin": "synthetic",
            }
        )
    return {
        "contract": "mdrack.native-corpus",
        "schema_version": 2,
        "state": "frozen",
        "evidence": "synthetic",
        "resources": resources,
        "groups": groups,
        "queries": queries,
    }


def config() -> dict[str, object]:
    return {
        "contract": "mdrack.native-experiment",
        "schema_version": 2,
        "split": "dev",
        "scenarios": ["A", "B", "C", "D", "E"],
        "candidate_budget": 20,
        "lexical_weight": 2.0,
        "vector_weight": 1.0,
        "mode_ab": "hybrid",
        "video_kind": "video",
        "policy": None,
    }


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'])
def test_strict_json_has_no_nonfinite_escape(raw: str) -> None:
    with pytest.raises(NativeCorpusError):
        decode(raw)


def policy() -> dict[str, object]:
    return {
        "critical_slices": ["en-image", "ru-image", "negative"],
        "max_regression": 1.0,
        "min_gain": 0.0,
        "max_search_ms": 100000.0,
        "max_index_bytes": 100000000,
        "max_provider_calls": 1000,
        "max_false_positives": 2,
        "min_independent_groups": 1,
    }


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "grade-bool",
        "missing-group",
        "parent-split",
        "cycle",
        "copy",
        "unknown-eligible",
        "scope-split",
        "digest",
        "path",
        "license",
        "publish",
        "asr-class-name",
        "stale-revision",
        "wrong-resource",
    ],
)
def test_invalid_native_inputs_are_refused(tmp_path: Path, fault: str) -> None:
    doc = fixture(tmp_path / "media")
    resources, groups, queries = map(array, (doc["resources"], doc["groups"], doc["queries"]))
    r, node, q = obj(resources[0]), obj(groups[0]), obj(queries[0])
    if fault == "duplicate":
        resources.append(resources[0])
    elif fault == "grade-bool":
        judgments = array(q["judgments"])
        judgments[0] = {**obj(judgments[0]), "grade": True}
        q["judgments"] = judgments
    elif fault in {"stale-revision", "wrong-resource"}:
        judgments = array(q["judgments"])
        judgments[0] = {
            **obj(judgments[0]),
            **({"source_revision": sha(b"stale")} if fault == "stale-revision" else {"resource_id": "held"}),
        }
        q["judgments"] = judgments
    elif fault == "missing-group":
        node["group"] = "unlinked"
    elif fault == "parent-split":
        node["parents"] = ["held"]
    elif fault == "cycle":
        node["parents"] = ["red"]
    elif fault == "copy":
        q["input"] = {"modality": "text", "text": obj(r["projection"])["text"], "resource_id": None}
    elif fault == "unknown-eligible":
        q["eligible"] = ["absent"]
    elif fault == "scope-split":
        q["eligible"] = ["held"]
    elif fault == "digest":
        r["projection"] = {**obj(r["projection"]), "sha256": sha(b"wrong")}
    elif fault == "path":
        r["artifact"] = {**obj(r["artifact"]), "path": "../outside"}
    elif fault == "license":
        r["media_license"] = "NC-whole-ESC50-not-selected-ESC10"
    elif fault == "publish":
        r["local_reviewed"] = False
    elif fault == "asr-class-name":
        r["enrichment"] = {
            "kind": "asr",
            "text": "acoustic class dog",
            "sha256": sha(b"acoustic class dog"),
            "origin": "synthetic",
        }
    resources[0], groups[0], queries[0] = r, node, q
    doc.update(resources=resources, groups=groups, queries=queries)
    with pytest.raises(ValueError):
        validate_native_bundle(doc)


@pytest.mark.parametrize("fault", ["draft", "agent", "unknown-gold", "review", "acquisition"])
def test_unevaluable_never_becomes_empty_gold_quality(tmp_path: Path, fault: str) -> None:
    doc = fixture(tmp_path / "media")
    if fault == "draft":
        doc["state"] = "draft"
    elif fault == "acquisition":
        doc["evidence"] = "acquisition"
    elif fault == "review":
        doc["resources"] = [{**obj(r), "local_reviewed": False, "publishable": False} for r in array(doc["resources"])]
    else:
        doc["queries"] = [
            {**obj(q), **({"origin": "agent_draft"} if fault == "agent" else {"judgments": None})}
            for q in array(doc["queries"])
        ]
    bundle = validate_native_bundle(doc)
    with pytest.raises(NativeCorpusError):
        require_evaluable(bundle)


@pytest.mark.parametrize("bound", ["none", "index", "negative", "groups"])
def test_frozen_policy_and_true_negative_cost_controls(tmp_path: Path, bound: str) -> None:
    root = tmp_path / "media"
    bundle = validate_native_bundle(fixture(root), media_root=root)
    configuration = config()
    limits = policy()
    if bound == "index":
        limits["max_index_bytes"] = 0
    elif bound == "negative":
        limits["max_false_positives"] = 0
    elif bound == "groups":
        limits["min_independent_groups"] = 2
    configuration["policy"] = limits

    class MutatingHost(ScriptedProvider):
        async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
            # A host callback must not rewrite the already-sealed caller policy.
            limits["max_index_bytes"] = 100000000
            limits["max_false_positives"] = 100
            limits["min_independent_groups"] = 1
            return await super().encode(value, role)

    report = run_native_experiment(
        bundle,
        configuration,
        media_root=root,
        output_root=tmp_path / "out",
        provider_a=MutatingHost(),
        provider_b=ScriptedProvider(),
    )
    assert report["executed_cells"] == 15
    assert (
        report["fitness"]
        == {
            "none": "SYNTHETIC_MECHANICS_ONLY",
            "index": "POLICY_EXCEEDED",
            "negative": "POLICY_EXCEEDED",
            "groups": "BLOCKED_INDEPENDENT_GROUPS",
        }[bound]
    )
    assert report["semantic_quality_claim"] is False
    assert obj(report["uncertainty"])["independent_groups"] == 1
    assert len(array(report["comparisons"])) == 6


def test_missing_capability_is_named_and_never_removed(tmp_path: Path) -> None:
    class TextOnly(ScriptedProvider):
        capabilities = RouteCapabilities(
            tuple(InputCapability(role, "text", ("text",), 2000000) for role in EncodingRole)
        )

    root = tmp_path / "media"
    bundle = validate_native_bundle(fixture(root), media_root=root)
    configuration = {**config(), "policy": policy()}
    report = run_native_experiment(
        bundle,
        configuration,
        media_root=root,
        output_root=tmp_path / "out",
        provider_a=TextOnly(),
        provider_b=TextOnly(),
    )
    assert report["planned_cells"] == 15
    assert report["executed_cells"] == 6
    assert report["fitness"] == "BLOCKED_COVERAGE"
    blocked = [obj(c) for c in array(report["cells"]) if obj(c)["status"] == "BLOCKED"]
    assert len(blocked) == 9
    assert blocked[0]["reason"] == "unsupported_embedding_route"


@pytest.mark.parametrize("mode", ["complete", "no-policy", "draft", "missing-provider"])
def test_actual_cli_privacy_atomicity_and_policy(tmp_path: Path, mode: str) -> None:
    root = Path(__file__).parents[2]
    media = tmp_path / "media"
    document = fixture(media)
    if mode == "draft":
        document["state"] = "draft"
    bundle_file, config_file = tmp_path / "bundle.json", tmp_path / "config.json"
    bundle_file.write_text(json.dumps(document), encoding="utf-8")
    configuration = config()
    if mode != "no-policy":
        configuration["policy"] = policy()
    config_file.write_text(json.dumps(configuration), encoding="utf-8")
    output = tmp_path / "report.json"
    output.write_bytes(b"preserve-existing-output")
    paths = [
        root / "src",
        *(root / "packages" / p / "src" for p in ("mdrack-core", "mdrack-media", "mdrack-sqlite")),
        root / "tests/unit",
        Path(pytest.__file__).parents[1],
    ]
    launch = (
        "import sys;sys.dont_write_bytecode=True;sys.path[:0]="
        + repr([str(p) for p in paths])
        + ";import runpy;sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    factory = (
        "not_a_real_provider:NeverImport"
        if mode in {"draft", "missing-provider"}
        else "test_eval_native_runner:ScriptedProvider"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            launch,
            str(root / "scripts/run_native_evaluation.py"),
            "run",
            "--bundle",
            str(bundle_file),
            "--config",
            str(config_file),
            "--media-root",
            str(media),
            "--output-root",
            str(tmp_path / "run"),
            "--output",
            str(output),
            "--provider-a",
            factory,
            "--provider-b",
            factory,
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert result.returncode == {"complete": 0, "no-policy": 2, "draft": 1, "missing-provider": 1}[mode]
    assert b"Traceback" not in result.stderr
    if mode in {"draft", "missing-provider"}:
        assert output.read_bytes() == b"preserve-existing-output"
        assert not (tmp_path / "run").exists()
    else:
        report = json.loads(output.read_bytes())
        assert report["executed_cells"] == 15
        assert report["semantic_quality_claim"] is False
        assert b"scarlet red object" not in output.read_bytes()


@pytest.mark.parametrize("route", ["video", "frames"])
def test_public_video_routes_with_generated_cpu_media(tmp_path: Path, route: str) -> None:
    root = tmp_path / "media"
    document = fixture(root)
    codec = shutil.which("ffmpeg")
    assert codec is not None, "existing CPU codec is required for this mechanics test"
    resources, groups, revisions = [], [], {}
    for raw in array(document["resources"]):
        r = obj(raw)
        identity = str(r["id"])
        rid = resource_id("synthetic-video", identity)
        video = root / f"{identity}.mp4"
        process = subprocess.run(
            [
                codec,
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={identity if identity in ('red', 'blue') else 'green'}:s=16x16:r=1:d=1",
                "-an",
                "-c:v",
                "mpeg4",
                "-threads",
                "1",
                "-y",
                str(video),
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert process.returncode == 0, "synthetic CPU media generation failed"
        data = video.read_bytes()
        revisions[identity] = (rid, sha(data))
        fixed = {**obj(r["projection"]), "kind": "caption"}
        r.update(
            id=rid,
            kind="video",
            media_type="video/mp4",
            artifact={"path": video.name, "sha256": sha(data), "bytes": len(data)},
            projection=fixed,
            enrichment=fixed,
        )
        resources.append(r)
        groups.append(
            {
                "id": rid,
                "group": r["group"],
                "split": r["split"],
                "parents": [],
                "content_ref": sha(data),
                "kind": "source",
            }
        )
    queries = []
    for raw in array(document["queries"]):
        q = obj(raw)
        q["eligible"] = [revisions[i][0] for i in ("red", "blue")]
        q["judgments"] = [
            {
                **obj(j),
                "resource_id": revisions[str(obj(j)["resource_id"])][0],
                "source_revision": revisions[str(obj(j)["resource_id"])][1],
            }
            for j in array(q["judgments"])
        ]
        queries.append(q)
    first = queries[0]
    blue_gold = obj(array(first["judgments"])[1])
    first.update(
        input={"modality": "video", "text": None, "resource_id": revisions["red"][0]},
        eligible=[revisions["blue"][0]],
        judgments=[{**blue_gold, "grade": 3}],
    )
    document.update(resources=resources, groups=groups, queries=queries)
    bundle = validate_native_bundle(document, media_root=root)
    report = run_native_experiment(
        bundle,
        {**config(), "video_kind": route},
        media_root=root,
        output_root=tmp_path / "out",
        provider_a=ScriptedProvider(),
        provider_b=ScriptedProvider(),
    )
    assert report["executed_cells"] == 15, [(obj(c)["scenario"], obj(c).get("reason")) for c in array(report["cells"])]
    assert report["semantic_quality_claim"] is False


def test_native_query_scope_is_applied_before_candidate_limit(tmp_path: Path) -> None:
    root = tmp_path / "media"
    document = fixture(root)
    queries = array(document["queries"])
    first = obj(queries[0])
    blue = obj(array(first["judgments"])[1])
    first.update(
        input={"modality": "image", "text": None, "resource_id": "red"},
        eligible=["blue"],
        judgments=[{**blue, "grade": 3}],
    )
    second = obj(queries[1])
    second.update(eligible=["red"], judgments=[obj(array(second["judgments"])[0])])
    queries[0], queries[1] = first, second
    document["queries"] = [{**obj(q), "cutoffs": [1]} for q in queries]
    bundle = validate_native_bundle(document, media_root=root)
    configuration = {**config(), "candidate_budget": 1}
    report = run_native_experiment(
        bundle,
        configuration,
        media_root=root,
        output_root=tmp_path / "out",
        provider_a=ScriptedProvider(),
        provider_b=ScriptedProvider(),
    )
    assert report["executed_cells"] == 15
    native = obj(array(report["stages"])[2])
    assert obj(native["stats"])["resources"] == 2
    native_results = array(obj(native["quality"])["results"])
    assert obj(array(obj(native_results[0])["cutoffs"])[0])["ndcg"] == 1.0
    assert report["semantic_quality_claim"] is False


def test_clean_plan_does_not_publish_profile_or_slice_labels(tmp_path: Path) -> None:
    canary = "SYNTHETIC_PRIVATE_INPUT_CANARY"

    class PrivateLabels(ScriptedProvider):
        space = EmbeddingSpaceRecord(
            "private-label-space",
            2,
            "cosine",
            canary,
            metadata={
                "vector_value_policy": "ieee754-f32-canonical-v1",
                "vector_codec": "ieee754-f32-le-v1",
                "private_label": canary,
            },
        )

    root = tmp_path / "media"
    document = fixture(root)
    document["queries"] = [
        {**obj(q), "slice": canary if obj(q)["id"] == "en" else obj(q)["slice"]} for q in array(document["queries"])
    ]
    bundle = validate_native_bundle(document, media_root=root)
    limits = {**policy(), "critical_slices": [canary, "ru-image", "negative"]}
    report = run_native_experiment(
        bundle,
        {**config(), "policy": limits},
        media_root=root,
        output_root=tmp_path / "out",
        provider_a=PrivateLabels(),
        provider_b=PrivateLabels(),
    )
    assert report["executed_cells"] == 15
    assert canary not in json.dumps(report)
    assert canary not in (tmp_path / "out/plan.json").read_text(encoding="utf-8")


def test_group_identity_cannot_appear_in_both_partitions(tmp_path: Path) -> None:
    doc = fixture(tmp_path / "media")
    doc["resources"] = [
        {**obj(r), "group": "group-red"} if n == 2 else r for n, r in enumerate(array(doc["resources"]))
    ]
    doc["groups"] = [{**obj(r), "group": "group-red"} if n == 2 else r for n, r in enumerate(array(doc["groups"]))]
    with pytest.raises(NativeCorpusError, match="source_group_leakage"):
        validate_native_bundle(doc)


def test_timed_ready_asr_uses_real_public_audio_and_enrichment(tmp_path: Path) -> None:
    root = tmp_path / "media"
    doc = fixture(root)
    mapping = {
        str(obj(r)["id"]): resource_id("native-eval-synthetic", str(obj(r)["id"])) for r in array(doc["resources"])
    }
    resources = []
    groups = []
    revisions = {}
    for index, raw in enumerate(array(doc["resources"])):
        r = obj(raw)
        old = str(r["id"])
        rid = mapping[old]
        path = root / f"{old}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(struct.pack("<h", index + 1) * 16000)
        data = path.read_bytes()
        revisions[rid] = sha(data)
        transcript = read_transcript(
            json.dumps(
                {
                    "segments": [
                        {
                            "start": 0.1,
                            "end": 0.5,
                            "text": "blue alternative phrase" if old == "blue" else "scarlet red phrase",
                        }
                    ]
                }
            ).encode(),
            resource_id=rid,
            producer_fingerprint=ProducerFingerprint.from_payload({"fixture": "synthetic-not-ASR-quality"}),
        ).artifact
        content = json.dumps(transcript.to_dict())
        r.update(
            id=rid,
            kind="audio",
            media_type="audio/wav",
            artifact={"path": path.name, "sha256": sha(data), "bytes": len(data)},
            enrichment={"kind": "asr", "text": content, "sha256": sha(content.encode()), "origin": "synthetic"},
            projection={"kind": "asr", "text": content, "sha256": sha(content.encode()), "origin": "synthetic"},
        )
        resources.append(r)
        groups.append(
            {
                "id": rid,
                "group": r["group"],
                "split": r["split"],
                "parents": [],
                "content_ref": sha(data),
                "kind": "source",
            }
        )
    doc["resources"], doc["groups"] = resources, groups
    queries = []
    for raw in array(doc["queries"]):
        q = obj(raw)
        q["eligible"] = [mapping["red"], mapping["blue"]]
        q["target"], q["match"] = "unit", "temporal"
        q["temporal_policy"] = {"interval_iou": 0.5, "timestamp_tolerance_ms": 10}
        q["judgments"] = [
            {
                **obj(j),
                "resource_id": mapping[str(obj(j)["resource_id"])],
                "source_revision": revisions[mapping[str(obj(j)["resource_id"])]],
                "intervals": [[100, 500]],
            }
            for j in array(q["judgments"])
        ]
        q["judgments"] = array(q["judgments"]) + [
            {**obj(j), "judgment_id": str(obj(j)["judgment_id"]) + "-whole-audio", "grade": 0, "intervals": [[0, 1000]]}
            for j in array(q["judgments"])
        ]
        queries.append(q)
    doc["queries"] = queries
    bundle = validate_native_bundle(doc, media_root=root)
    report = run_native_experiment(
        bundle,
        config(),
        media_root=root,
        output_root=tmp_path / "out",
        provider_a=ScriptedProvider(),
        provider_b=ScriptedProvider(),
    )
    assert report["executed_cells"] == report["planned_cells"] == 15, [
        (obj(c)["scenario"], obj(c).get("reason")) for c in array(report["cells"])
    ]
    assert all(obj(s)["status"] == "COMPLETE" for s in array(report["stages"]))
    assert report["semantic_quality_claim"] is False
    rows = array(obj(obj(array(report["stages"])[3])["quality"])["results"])
    assert obj(array(obj(rows[0])["cutoffs"])[0])["ndcg"] == 1.0


PORT_CANARY = "SYNTHETIC_PORT_FAILURE_DO_NOT_PUBLISH"


class DocumentPortFailure(ScriptedProvider):
    failure_role = EncodingRole.DOCUMENT

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        self.attempts += 1
        if role == self.failure_role:
            raise EmbeddingError(PORT_CANARY)
        return await super().encode(value, role)


class QueryPortFailure(DocumentPortFailure):
    failure_role = EncodingRole.QUERY


@pytest.mark.parametrize("scenario", ["A", "B"])
@pytest.mark.parametrize("role", ["document", "query"])
def test_declared_port_failure_retains_matrix(tmp_path: Path, scenario: str, role: str) -> None:
    media = tmp_path / "media"
    bundle = validate_native_bundle(fixture(media), media_root=media)
    failed = DocumentPortFailure() if role == "document" else QueryPortFailure()
    healthy = ScriptedProvider()
    a, b = (failed, healthy) if scenario == "A" else (healthy, failed)
    report = run_native_experiment(
        bundle,
        {**config(), "policy": policy()},
        media_root=media,
        output_root=tmp_path / "index",
        provider_a=a,
        provider_b=b,
    )
    assert report["planned_cells"] == 15
    assert report["executed_cells"] == (12 if scenario == "A" else 3)
    assert report["completed_scenarios"] == (4 if scenario == "A" else 1)
    assert report["fitness"] == "BLOCKED_COVERAGE"
    cells = [obj(c) for c in array(report["cells"])]
    blocked = [c for c in cells if c["status"] == "BLOCKED"]
    assert len(blocked) == (3 if scenario == "A" else 12)
    assert all(c["scenario"] == "A" for c in blocked) if scenario == "A" else all(c["scenario"] != "A" for c in blocked)
    assert all(c["reason"] in {"embedding_port_failure", "native_baseline_missing"} for c in blocked)
    stages = [obj(s) for s in array(report["stages"])]
    failed_stage = stages[0 if scenario == "A" else 1]
    assert failed_stage["quality"] is None and failed_stage["case_ordinals"] == []
    assert failed_stage["provider_calls_cumulative"] == (1 if role == "document" else 5)
    assert failed_stage["provider_bytes_cumulative"] > 0
    assert healthy.calls == (18 if scenario == "A" else 5)
    assert failed.attempts == (
        failed_stage["provider_calls_cumulative"] if scenario == "A" else stages[-1]["provider_calls_cumulative"]
    )
    assert PORT_CANARY not in json.dumps(report)
    assert report["semantic_quality_claim"] is False


@pytest.mark.parametrize("phase", ["native", "dense"])
def test_declared_document_port_failure_in_sibling_phases(tmp_path: Path, phase: str) -> None:
    class PhaseFailure(ScriptedProvider):
        text_documents = 0

        async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
            if role == EncodingRole.DOCUMENT:
                if value.modality == "text":
                    self.text_documents += 1
                if (phase == "native" and value.modality != "text") or (
                    phase == "dense" and value.modality == "text" and self.text_documents > 2
                ):
                    raise EmbeddingError(PORT_CANARY)
            return await super().encode(value, role)

    media = tmp_path / "media"
    bundle = validate_native_bundle(fixture(media), media_root=media)
    report = run_native_experiment(
        bundle,
        {**config(), "policy": policy()},
        media_root=media,
        output_root=tmp_path / "index",
        provider_a=ScriptedProvider(),
        provider_b=PhaseFailure(),
    )
    assert report["planned_cells"] == 15
    assert report["executed_cells"] == (6 if phase == "native" else 12)
    assert report["fitness"] == "BLOCKED_COVERAGE"
    stages = [obj(s) for s in array(report["stages"])]
    failed = stages[2 if phase == "native" else 4]
    assert failed["quality"] is None
    assert failed["provider_calls_cumulative"] == (6 if phase == "native" else 14)
    assert PORT_CANARY not in json.dumps(report)


@pytest.mark.parametrize(
    ("role", "kind"),
    [(EncodingRole.DOCUMENT, "programming"), (EncodingRole.DOCUMENT, "cancel"), (EncodingRole.QUERY, "cancel")],
)
def test_non_port_programming_error_and_cancellation_propagate(tmp_path: Path, role: EncodingRole, kind: str) -> None:
    class Abort(ScriptedProvider):
        async def encode(self, value: PreparedInput, current_role: EncodingRole) -> EmbeddingResult:
            if current_role == role:
                if kind == "programming":
                    raise RuntimeError("synthetic programming defect")
                raise asyncio.CancelledError
            return await super().encode(value, current_role)

    media = tmp_path / "media"
    bundle = validate_native_bundle(fixture(media), media_root=media)
    with pytest.raises(RuntimeError if kind == "programming" else asyncio.CancelledError):
        run_native_experiment(
            bundle,
            {**config(), "policy": policy()},
            media_root=media,
            output_root=tmp_path / "index",
            provider_a=Abort(),
            provider_b=ScriptedProvider(),
        )


@pytest.mark.parametrize("factory", ["DocumentPortFailure", "QueryPortFailure"])
def test_declared_port_failure_cli_writes_blocked_report(tmp_path: Path, factory: str) -> None:
    root = Path(__file__).parents[2]
    media = tmp_path / "media"
    bundle_file, config_file, output = (tmp_path / n for n in ("bundle.json", "config.json", "report.json"))
    bundle_file.write_text(json.dumps(fixture(media)), encoding="utf-8")
    config_file.write_text(json.dumps({**config(), "policy": policy()}), encoding="utf-8")
    output.write_bytes(b"existing-output")
    bootstrap = os.environ.get("CORPUS_BOOTSTRAP")
    if bootstrap:
        prefix = ["-I", "-B", "-S", bootstrap, "--script"]
    else:
        paths = [
            root,
            root / "src",
            *(root / "packages" / p / "src" for p in ("mdrack-core", "mdrack-media", "mdrack-sqlite")),
            Path(pytest.__file__).parents[1],
        ]
        launch = (
            "import sys;sys.dont_write_bytecode=True;sys.path[:0]="
            + repr([str(p) for p in paths])
            + ";import runpy;sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
        )
        prefix = ["-I", "-B", "-S", "-c", launch]
    result = subprocess.run(
        [
            sys.executable,
            *prefix,
            str(root / "scripts/run_native_evaluation.py"),
            "run",
            "--bundle",
            str(bundle_file),
            "--config",
            str(config_file),
            "--media-root",
            str(media),
            "--output-root",
            str(tmp_path / "index"),
            "--output",
            str(output),
            "--provider-a",
            "tests.unit.test_eval_native_runner:" + factory,
            "--provider-b",
            "tests.unit.test_eval_native_runner:ScriptedProvider",
        ],
        cwd=tmp_path,
        capture_output=True,
    )
    assert result.returncode == 2
    assert PORT_CANARY.encode() not in result.stdout + result.stderr + output.read_bytes()
    assert b"Traceback" not in result.stderr
    report = json.loads(output.read_bytes())
    assert report["planned_cells"] == 15 and report["executed_cells"] == 12
    assert report["fitness"] == "BLOCKED_COVERAGE"
    assert sum(c["status"] == "BLOCKED" for c in report["cells"]) == 3


def test_public_engine_all_scenarios_and_paired_enrichment(tmp_path: Path) -> None:
    data = tmp_path / "media"
    bundle = validate_native_bundle(fixture(data), media_root=data)
    a, b = ScriptedProvider(), ScriptedProvider()
    report = run_native_experiment(
        bundle, config(), media_root=data, output_root=tmp_path / "run", provider_a=a, provider_b=b
    )
    assert report["completed_scenarios"] == 5
    assert report["planned_cells"] == report["executed_cells"] == 15
    assert report["fitness"] == "BLOCKED_POLICY_MISSING"
    assert report["evidence"] == "synthetic"
    assert len(report["paired"]) == 6
    assert a.calls > 0 and b.calls > 0
    assert (tmp_path / "run" / "multimodal" / ".mdrack" / "catalog.sqlite3").is_file()


class RetrievalRoleProvider(ScriptedProvider):
    """Scripted mechanics with exactly the proposed text/image role matrix."""
    capabilities = RouteCapabilities((
        InputCapability(EncodingRole.DOCUMENT, "image", ("image",), 2000000),
        InputCapability(EncodingRole.DOCUMENT, "text", ("text",), 65500),
        InputCapability(EncodingRole.QUERY, "text", ("image", "text"), 65500),
    ))

    def __init__(self) -> None:
        super().__init__()
        self.roles: list[tuple[str, EncodingRole]] = []

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        self.roles.append((value.modality, role))
        return await super().encode(value, role)


def test_retrieval_roles_complete_required_B_and_D_union_without_dense_E(tmp_path: Path) -> None:
    data = tmp_path / "media"
    bundle = validate_native_bundle(fixture(data), media_root=data)
    a, b = RetrievalRoleProvider(), RetrievalRoleProvider()
    cfg = config()
    cfg["scenarios"] = ["A", "B", "C", "D"]
    report = run_native_experiment(bundle, cfg, media_root=data, output_root=tmp_path / "roles",
                                   provider_a=a, provider_b=b)
    assert [stage["scenario"] for stage in report["stages"]] == ["A", "B", "C", "D"]
    assert report["completed_scenarios"] == 4 and report["executed_cells"] == 12
    assert report["fitness"] == "BLOCKED_POLICY_MISSING"
    assert b.roles.count(("text", EncodingRole.DOCUMENT)) == 2
    assert b.roles.count(("image", EncodingRole.DOCUMENT)) == 2
    assert b.roles.count(("text", EncodingRole.QUERY)) == 9
    stages = report["stages"]
    assert stages[3]["provider_calls_cumulative"] - stages[2]["provider_calls_cumulative"] == 3
    assert b.capabilities.require(EncodingRole.QUERY, "text", ("image", "text"))


def test_old_image_only_roles_still_block_B_and_D_coverage(tmp_path: Path) -> None:
    data = tmp_path / "media"
    bundle = validate_native_bundle(fixture(data), media_root=data)
    old = RetrievalRoleProvider()
    old.capabilities = RouteCapabilities((
        InputCapability(EncodingRole.DOCUMENT, "image", ("image",), 2000000),
        InputCapability(EncodingRole.QUERY, "text", ("image",), 65500),
    ))
    cfg = config()
    cfg.update(scenarios=["A", "B", "C", "D"], policy=policy())
    report = run_native_experiment(bundle, cfg, media_root=data, output_root=tmp_path / "old",
                                   provider_a=RetrievalRoleProvider(), provider_b=old)
    stages = {stage["scenario"]: stage for stage in report["stages"]}
    assert stages["B"]["status"] == stages["D"]["status"] == "BLOCKED"
    assert report["fitness"] == "BLOCKED_COVERAGE"
    assert len([cell for cell in report["cells"] if cell["status"] == "BLOCKED"]) == 6


def test_D_plain_keyword_punctuation_keeps_complete_matrix(tmp_path: Path) -> None:
    media = tmp_path / "media"
    document = fixture(media)
    document["queries"] = [
        {**obj(query), "input": {"modality": "text", "text": value, "resource_id": None}}
        for query, value in zip(array(document["queries"]),
                                ("scarlet and red.", "scarlet Or red.", "unknown not present."), strict=True)
    ]
    bundle = validate_native_bundle(document, media_root=media)
    report = run_native_experiment(
        bundle, {**config(), "scenarios": ["A", "B", "C", "D"], "mode_ab": "semantic"}, media_root=media,
        output_root=tmp_path / "out", provider_a=ScriptedProvider(), provider_b=RetrievalRoleProvider(),
    )
    assert report["planned_cells"] == report["executed_cells"] == 12
    assert report["completed_scenarios"] == 4
    assert report["fitness"] == "BLOCKED_POLICY_MISSING"
    assert [stage["scenario"] for stage in report["stages"]] == ["A", "B", "C", "D"]
    assert all(cell["effective_mode"] == "hybrid" for cell in report["cells"] if cell["scenario"] == "D")


@pytest.mark.parametrize("kind", ["declared", "programming", "cancel"])
def test_search_error_boundary_preserves_matrix_or_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from mdrack.public_api import MDRackEngine

    original = MDRackEngine.search_media
    closed: list[bool] = []
    original_close = MDRackEngine.close

    def close(engine: MDRackEngine) -> None:
        original_close(engine)
        closed.append(True)

    async def search(engine: MDRackEngine, request: object) -> object:
        if getattr(request, "mode") == "hybrid":
            if kind == "declared":
                raise BranchExecutionError(ErrorCategory.ADAPTER_ERROR, branch_id=PORT_CANARY)
            if kind == "programming":
                raise RuntimeError(PORT_CANARY)
            raise asyncio.CancelledError()
        return await original(engine, request)

    monkeypatch.setattr(MDRackEngine, "search_media", search)
    monkeypatch.setattr(MDRackEngine, "close", close)
    media = tmp_path / "media"
    bundle = validate_native_bundle(fixture(media), media_root=media)
    args = {"media_root": media, "output_root": tmp_path / "out",
            "provider_a": ScriptedProvider(), "provider_b": RetrievalRoleProvider()}
    configuration = {**config(), "scenarios": ["A", "B", "C", "D"], "policy": policy(),
                     "mode_ab": "semantic"}
    if kind != "declared":
        with pytest.raises(RuntimeError if kind == "programming" else asyncio.CancelledError):
            run_native_experiment(bundle, configuration, **args)
    else:
        report = run_native_experiment(bundle, configuration, **args)
        assert report["planned_cells"] == 12 and report["executed_cells"] == 9
        assert report["completed_scenarios"] == 3 and report["fitness"] == "BLOCKED_COVERAGE"
        blocked = [c for c in report["cells"] if c["status"] == "BLOCKED"]
        assert len(blocked) == 3 and all(c["scenario"] == "D" for c in blocked)
        assert all(c["reason"] == "search_branch_failure" for c in blocked)
        assert PORT_CANARY not in json.dumps(report)
    assert len(closed) == 3


class DeclaredSearchFailure(ScriptedProvider):
    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        if role is EncodingRole.QUERY:
            raise BranchExecutionError(ErrorCategory.ADAPTER_ERROR, branch_id=PORT_CANARY)
        return await super().encode(value, role)


@pytest.mark.parametrize("declared", [False, True])
def test_public_cli_plain_query_or_declared_branch_report(tmp_path: Path, declared: bool) -> None:
    root = Path(__file__).parents[2]
    media = tmp_path / "media"
    document = fixture(media)
    document["queries"] = [
        {**obj(query), "input": {"modality": "text", "text": "scarlet and red.", "resource_id": None}}
        if n == 0 else query for n, query in enumerate(array(document["queries"]))
    ]
    bundle, configuration, output = (tmp_path / n for n in ("bundle.json", "config.json", "report.json"))
    bundle.write_text(json.dumps(document))
    configuration.write_text(json.dumps({**config(), "scenarios": ["A", "B", "C", "D"],
                                         "mode_ab": "semantic", "policy": policy() if declared else None}))
    paths = [root, root / "src", *(root / "packages" / p / "src" for p in
             ("mdrack-core", "mdrack-media", "mdrack-sqlite")), Path(pytest.__file__).parents[1]]
    launch = ("import sys,runpy;sys.dont_write_bytecode=True;sys.path[:0]=" + repr([str(p) for p in paths])
              + ";sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')")
    result = subprocess.run([
        sys.executable, "-I", "-B", "-S", "-c", launch, str(root / "scripts/run_native_evaluation.py"),
        "run", "--bundle", str(bundle), "--config", str(configuration), "--media-root", str(media),
        "--output-root", str(tmp_path / "out"), "--output", str(output),
        "--provider-a",
        "tests.unit.test_eval_native_runner:" + ("DeclaredSearchFailure" if declared else "ScriptedProvider"),
        "--provider-b", "tests.unit.test_eval_native_runner:RetrievalRoleProvider",
    ], capture_output=True, check=False)
    assert result.returncode == 2
    report = json.loads(output.read_bytes())
    assert report["planned_cells"] == 12 and report["executed_cells"] == (9 if declared else 12)
    assert report["fitness"] == ("BLOCKED_COVERAGE" if declared else "BLOCKED_POLICY_MISSING")
    assert PORT_CANARY.encode() not in result.stdout + result.stderr + output.read_bytes()
    assert b"Traceback" not in result.stderr
