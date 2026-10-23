"""Native input-side corpus v2. No network, inference or evaluator arithmetic.

Attribution, text and source groups remain input-only. Quality-v2 receives only
its original closed identities/grades/coordinates projection.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from mdrack.eval.quality import fingerprint
from mdrack.eval.quality_v2 import validate_quality_input
from mdrack_media import TranscriptArtifact

SHA = re.compile(r"sha256:[0-9a-f]{64}")
LICENSES = {"CC0-1.0", "CC-BY-2.0", "CC-BY-3.0", "CC-BY-4.0", "MIT"}


class NativeCorpusError(ValueError):
    """Fixed, payload-free input failure."""


def fail(code: str) -> NoReturn:
    raise NativeCorpusError(code)


def obj(value: object, keys: set[str] | None = None) -> dict[str, object]:
    if not isinstance(value, dict):
        fail("invalid_object")
    result: dict[str, object] = {}
    for key, child in value.items():
        if not isinstance(key, str):
            fail("invalid_key")
        result[key] = child
    if keys is not None and set(result) != keys:
        fail("invalid_fields")
    return result


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        fail("invalid_array")
    return list(value)


def text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        fail("invalid_text")
    return value


def flag(value: object) -> bool:
    if type(value) is not bool:
        fail("invalid_boolean")
    return value


def integer(value: object, low: int = 0, high: int = 32 * 1024 * 1024) -> int:
    if type(value) is not int or not low <= value <= high:
        fail("invalid_integer")
    return value


def choice(value: object, values: set[str]) -> str:
    selected = text(value)
    if selected not in values:
        fail("invalid_choice")
    return selected


def digest(value: object) -> str:
    selected = text(value)
    if SHA.fullmatch(selected) is None:
        fail("invalid_digest")
    return selected


def unique(values: list[str]) -> None:
    if len(values) != len(set(values)):
        fail("duplicate_identity")


def decode(data: str) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                fail("duplicate_json_key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        fail("nonfinite_json")

    def finite(raw: str) -> float:
        value = float(raw)
        if not math.isfinite(value):
            fail("nonfinite_json")
        return value

    try:
        return obj(json.loads(data, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite))
    except (ValueError, TypeError, RecursionError):
        raise NativeCorpusError("invalid_json") from None


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str
    size: int

    @classmethod
    def parse(cls, value: object) -> Artifact:
        record = obj(value, {"path", "sha256", "bytes"})
        path = text(record["path"])
        if "\\" in path or ":" in path or Path(path).is_absolute() or any(p in {"..", "."} for p in path.split("/")):
            fail("unsafe_artifact_path")
        return cls(path, digest(record["sha256"]), integer(record["bytes"], 1))

    def resolve(self, root: Path) -> Path:
        base = root.resolve()
        path = base / self.path
        if not path.resolve().is_relative_to(base) or any(
            p.is_symlink() for p in (path, *path.parents) if p != base.parent
        ):
            fail("unsafe_artifact_path")
        if not path.is_file() or path.stat().st_size != self.size:
            fail("artifact_missing_or_size")
        if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != self.sha256:
            fail("artifact_digest")
        return path


@dataclass(frozen=True)
class FixedText:
    kind: str
    content: str = field(repr=False)
    sha256: str
    origin: str

    @classmethod
    def parse(cls, value: object) -> FixedText | None:
        if value is None:
            return None
        record = obj(value, {"kind", "text", "sha256", "origin"})
        content = text(record["text"])
        reference = digest(record["sha256"])
        if "sha256:" + hashlib.sha256(content.encode()).hexdigest() != reference:
            fail("text_digest")
        kind = choice(record["kind"], {"text", "ocr", "asr", "caption", "class_description"})
        if kind == "asr":
            try:
                TranscriptArtifact.from_dict(decode(content))
            except (ValueError, TypeError):
                fail("invalid_fixed_transcript")
        return cls(
            kind,
            content,
            reference,
            choice(record["origin"], {"human", "synthetic", "agent_draft"}),
        )

    @property
    def searchable_texts(self) -> tuple[str, ...]:
        if self.kind != "asr":
            return (self.content,)
        parts = tuple(a.text for a in TranscriptArtifact.from_dict(decode(self.content)).atoms)
        return (*parts, " ".join(parts))


@dataclass(frozen=True)
class NativeResource:
    resource_id: str
    kind: str
    media_type: str
    source: str
    artifact: Artifact
    attribution: Artifact
    group: str
    split: str
    media_license: str
    annotation_license: str | None
    local_reviewed: bool
    publishable: bool
    projection: FixedText | None
    enrichment: FixedText | None

    @property
    def namespace(self) -> str:
        return "native-eval-" + fingerprint(self.resource_id)[7:31]


@dataclass(frozen=True)
class NativeBundle:
    document: dict[str, object] = field(repr=False)
    state: str
    evidence: str
    resources: tuple[NativeResource, ...]
    queries: tuple[dict[str, object], ...] = field(repr=False)
    corpus_ref: str
    queries_ref: str
    judgments_ref: str
    groups_ref: str


def validate_native_bundle(value: object, *, media_root: Path | None = None) -> NativeBundle:
    optional = set(obj(value)) & {"provenance_ref", "selection_ref"}
    doc = obj(value, optional | {"contract", "schema_version", "state", "evidence", "resources", "groups", "queries"})
    for key in optional:
        digest(doc[key])
    if (
        doc["contract"] != "mdrack.native-corpus"
        or type(doc["schema_version"]) is not int
        or doc["schema_version"] != 2
    ):
        fail("unsupported_native_contract")
    # Own the frozen input graph; provider callbacks cannot mutate caller data
    # underneath an already-sealed policy/query/judgment identity.
    doc = decode(json.dumps(doc, allow_nan=False))
    state = choice(doc["state"], {"draft", "frozen"})
    evidence = choice(doc["evidence"], {"acquisition", "synthetic", "real_reviewed"})
    resources = []
    for raw in array(doc["resources"]):
        r = obj(
            raw,
            {
                "id",
                "kind",
                "media_type",
                "source",
                "artifact",
                "attribution",
                "group",
                "split",
                "media_license",
                "annotation_license",
                "local_reviewed",
                "publishable",
                "projection",
                "enrichment",
            },
        )
        license_id = choice(r["media_license"], LICENSES)
        annotation_license = None if r["annotation_license"] is None else choice(r["annotation_license"], LICENSES)
        resource = NativeResource(
            text(r["id"]),
            choice(r["kind"], {"document", "image", "audio", "video"}),
            text(r["media_type"]),
            text(r["source"]),
            Artifact.parse(r["artifact"]),
            Artifact.parse(r["attribution"]),
            text(r["group"]),
            choice(r["split"], {"dev", "held_out"}),
            license_id,
            annotation_license,
            flag(r["local_reviewed"]),
            flag(r["publishable"]),
            FixedText.parse(r["projection"]),
            FixedText.parse(r["enrichment"]),
        )
        for fixed in (resource.projection, resource.enrichment):
            if fixed is not None and fixed.kind == "asr":
                artifact = TranscriptArtifact.from_dict(decode(fixed.content))
                if resource.kind not in {"audio", "video"} or artifact.resource_id != resource.resource_id:
                    fail("fixed_transcript_owner_mismatch")
        if resource.publishable and not resource.local_reviewed:
            fail("publication_not_reviewed")
        if media_root is not None:
            resource.artifact.resolve(media_root)
            resource.attribution.resolve(media_root)
        resources.append(resource)
    unique([r.resource_id for r in resources])
    if not resources:
        fail("empty_resources")
    nodes: dict[str, tuple[str, str, list[str], str | None, str]] = {}
    for raw in array(doc["groups"]):
        node = obj(raw, {"id", "group", "split", "parents", "content_ref", "kind"})
        identity = text(node["id"])
        if identity in nodes:
            fail("duplicate_identity")
        text(node["kind"])
        nodes[identity] = (
            text(node["group"]),
            choice(node["split"], {"dev", "held_out"}),
            [text(p) for p in array(node["parents"])],
            None if node["content_ref"] is None else digest(node["content_ref"]),
            text(node["kind"]),
        )
    by_content: dict[str, tuple[str, str]] = {}
    group_splits: dict[str, str] = {}
    for identity, (group, split, parents, content, kind) in nodes.items():
        if group in group_splits and group_splits[group] != split:
            fail("source_group_leakage")
        group_splits[group] = split
        for parent in parents:
            if parent not in nodes or nodes[parent][:2] != (group, split):
                fail("source_group_leakage")
        if content is not None and kind in {"source", "derived_media"}:
            if content in by_content and by_content[content] != (group, split):
                fail("duplicate_content_leakage")
            by_content[content] = (group, split)
        seen: set[str] = set()
        active: set[str] = set()

        def visit(current: str) -> None:
            if current not in nodes:
                fail("source_group_leakage")
            if current in active:
                fail("cyclic_parent_graph")
            if current in seen:
                return
            active.add(current)
            for parent in nodes[current][2]:
                visit(parent)
            active.remove(current)
            seen.add(current)

        visit(identity)
    for resource in resources:
        if resource.resource_id not in nodes or nodes[resource.resource_id][:2] != (resource.group, resource.split):
            fail("resource_group_missing")
        if nodes[resource.resource_id][4] != "source" or nodes[resource.resource_id][3] != resource.artifact.sha256:
            fail("source_revision_mismatch")
    resource_map = {r.resource_id: r for r in resources}
    queries = []
    for raw in array(doc["queries"]):
        optional = set(obj(raw)) & {"task", "intent", "source_node"}
        q = obj(
            raw,
            optional
            | {
                "id",
                "split",
                "slice",
                "group",
                "input",
                "eligible",
                "case_type",
                "target",
                "match",
                "cutoffs",
                "judgment_policy",
                "temporal_policy",
                "judgments",
                "adjudicated",
                "origin",
            },
        )
        text(q["id"])
        split = choice(q["split"], {"dev", "held_out"})
        text(q["slice"])
        group = text(q["group"])
        if not any(n[:2] == (group, split) for n in nodes.values()):
            fail("query_group_missing")
        inp = obj(q["input"], {"modality", "text", "resource_id"})
        modality = choice(inp["modality"], {"text", "image", "audio", "video"})
        task = choice(q.get("task", "cross_source"), {"cross_source", "instance", "localization"})
        if "intent" in q:
            text(q["intent"])
        eligible = [text(i) for i in array(q["eligible"])]
        unique(eligible)
        if not eligible or any(i not in resource_map or resource_map[i].split != split for i in eligible):
            fail("eligible_scope_or_split")
        if modality == "text":
            query_text = text(inp["text"])
            if inp["resource_id"] is not None:
                fail("text_query_resource")
            for i in eligible:
                for projection in (resource_map[i].projection, resource_map[i].enrichment):
                    if projection is not None and any(
                        " ".join(t.split()).casefold() == " ".join(query_text.split()).casefold()
                        for t in projection.searchable_texts
                    ):
                        fail("query_projection_copy")
        else:
            query_id = text(inp["resource_id"])
            if inp["text"] is not None or query_id not in resource_map or resource_map[query_id].split != split:
                fail("invalid_native_query")
            if resource_map[query_id].group != group:
                fail("query_group_mismatch")
            if task == "cross_source" and (
                query_id in eligible or any(resource_map[i].group == group for i in eligible)
            ):
                fail("cross_source_self_leakage")
            if task == "localization" and (q["match"] != "temporal" or q["target"] != "unit"):
                fail("localization_requires_temporal_target")
            if resource_map[query_id].kind != modality:
                fail("query_modality_mismatch")
        if evidence == "real_reviewed" and state == "frozen":
            node_id = text(q.get("source_node"))
            text(q.get("intent"))
            if node_id not in nodes or nodes[node_id][:2] != (group, split):
                fail("query_source_linkage_missing")
            query_ref = (
                "sha256:" + hashlib.sha256(text(inp["text"]).encode()).hexdigest()
                if modality == "text"
                else resource_map[text(inp["resource_id"])].artifact.sha256
            )
            if nodes[node_id][3] != query_ref:
                fail("query_source_content_mismatch")
        flag(q["adjudicated"])
        choice(q["origin"], {"human", "synthetic", "agent_draft"})
        # Delegate relevance/target/cutoff/temporal semantics to the accepted evaluator.
        if q["judgments"] is not None:
            validate_quality_input(evaluator_input(doc, [q], resource_map, fingerprint(doc)))
        queries.append(q)
    unique([text(q["id"]) for q in queries])
    return NativeBundle(
        doc,
        state,
        evidence,
        tuple(resources),
        tuple(queries),
        fingerprint(doc["resources"]),
        fingerprint(doc["queries"]),
        fingerprint([q["judgments"] for q in queries]),
        fingerprint(doc["groups"]),
    )


def evaluator_input(
    document: dict[str, object],
    queries: list[dict[str, object]],
    resources: dict[str, NativeResource],
    implementation_ref: str,
    ranked: list[list[dict[str, object]]] | None = None,
) -> dict[str, object]:
    cases = []
    for index, q in enumerate(queries):
        policy = choice(q["judgment_policy"], {"complete", "pooled"})
        cases.append(
            {
                "case_id": q["id"],
                "case_type": q["case_type"],
                "target": q["target"],
                "match": q["match"],
                "cutoffs": q["cutoffs"],
                "scope": [
                    {"resource_id": i, "source_revision": resources[text(i)].artifact.sha256}
                    for i in array(q["eligible"])
                ],
                "judgment_policy": policy,
                "unjudged_policy": "error" if policy == "complete" else "nonrelevant",
                "temporal_policy": q["temporal_policy"],
                "judgments": q["judgments"],
                "ranked": [] if ranked is None else ranked[index],
            }
        )
    return {
        "contract": "mdrack.quality-input",
        "schema_version": 2,
        "corpus_ref": fingerprint(document["resources"]),
        "judgments_ref": fingerprint([q["judgments"] for q in queries]),
        "implementation_ref": implementation_ref,
        "cases": cases,
    }


def require_evaluable(bundle: NativeBundle) -> None:
    if (
        bundle.state != "frozen"
        or bundle.evidence == "acquisition"
        or not bundle.queries
        or any(not r.local_reviewed for r in bundle.resources)
    ):
        fail("corpus_not_evaluable")
    for q in bundle.queries:
        if (
            not q["adjudicated"]
            or q["judgments"] is None
            or q["origin"] == "agent_draft"
            or (q["origin"] == "synthetic" and bundle.evidence != "synthetic")
        ):
            fail("judgments_not_adjudicated")
    validate_quality_input(
        evaluator_input(
            bundle.document,
            list(bundle.queries),
            {r.resource_id: r for r in bundle.resources},
            fingerprint(bundle.document),
        )
    )
