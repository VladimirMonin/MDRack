"""Strict validator for the public MDRack evaluation fixture contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, cast

CORPUS_CONTRACT = "mdrack.evaluation-corpus"
QUERY_CONTRACT = "mdrack.evaluation-queries"
BENCHMARK_CONTRACT = "mdrack.evaluation-benchmark"
SCHEMA_VERSION = 1
DIGEST_PREFIX = "sha256:"
OPAQUE_ID = re.compile(r"^(?:corpus|benchmark|res|unit|frame|qry)_[0-9a-f]{32}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
RESOURCE_KINDS = frozenset({"document", "image", "audio", "video"})
REPRESENTATION_KINDS = frozenset(
    {
        "retrieval_text",
        "ocr_text",
        "caption_text",
        "audio_transcript",
        "frame_caption",
    }
)
UNIT_KINDS = frozenset({"text_chunk", "time_segment", "frame", "whole_resource"})
CASE_KINDS = frozenset({"lexical", "semantic", "hybrid", "resource_similarity", "timestamp"})
MODES = frozenset({"text", "semantic", "hybrid", "similarity"})
TARGETS = frozenset({"unit", "resource"})
PUBLICATION_LICENSE_IDS = frozenset(
    {
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "CC0-1.0",
        "MIT",
    }
)
CASE_SHAPES = {
    "lexical": ("text", "unit"),
    "semantic": ("semantic", "unit"),
    "hybrid": ("hybrid", "unit"),
    "resource_similarity": ("similarity", "resource"),
    "timestamp": ("hybrid", "unit"),
}
BASIS_BY_REPRESENTATION = {
    "retrieval_text": "document_text",
    "ocr_text": "ocr_text",
    "caption_text": "caption_text",
    "audio_transcript": "transcript_text",
    "frame_caption": "frame_caption_text",
}
MIN_RESOURCE_COUNTS = {"document": 20, "image": 10, "audio": 10, "video": 10}
MIN_FRAME_VIDEO_COUNT = 5
MIN_CASE_COUNTS = {
    "lexical": 50,
    "semantic": 50,
    "hybrid": 30,
    "resource_similarity": 20,
    "timestamp": 20,
}
_SAFE_LOGICAL_LOCATOR = re.compile(
    r"(?<![A-Za-z0-9._~+-])(?:"
    r"sha256:[0-9a-f]{64}"
    r"|mode:(?:hybrid|lexical|resource_similarity|semantic|timestamp)"
    r"|resource:(?:audio|document|image|video)"
    r"|representation:(?:audio_transcript|caption_text|frame_caption|retrieval_text)"
    r"|unit:(?:frame|text_chunk|time_segment|whole_resource)"
    r"|language:[a-z][a-z0-9_-]*"
    r"|length:[a-z][a-z0-9_-]*"
    r")(?![A-Za-z0-9._~+-])"
)
_PRIVATE_LOCATOR_PATTERNS = (
    # POSIX absolute paths, without treating ordinary embedded slashes or MIME types as paths.
    re.compile(r"(?<![A-Za-z0-9._~+%-])/(?!/)(?=[^/\s<>{}\[\]\"']|$)"),
    re.compile(r"(?m)^[ \t]*/[ \t]*$"),
    # Windows drive paths with either separator.
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    # UNC, device, and slash-normalized network paths.
    re.compile(r"(?<![A-Za-z0-9._~+%-])(?:\\\\|//)(?=[^\s/\\])"),
    # Parent traversal in either path grammar, at any segment boundary.
    re.compile(r"(?<![A-Za-z0-9._~-])\.\.(?:[\\/]|$)"),
    # RFC 3986 scheme syntax followed by a non-whitespace URI character.
    re.compile(
        r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*:"
        r"(?=[A-Za-z0-9/?#%._~!$&'()*+,;=@-]|$)"
    ),
)
_PRIVACY_SENTINELS = (
    re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
)


class ContractError(ValueError):
    """A privacy-safe public fixture contract failure."""


def _fail(message: str) -> None:
    raise ContractError(message)


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("JSON object contains a duplicate key")
            result[key] = value
        return result

    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("Contract JSON could not be read") from exc
    if not isinstance(raw, dict):
        _fail("Contract root must be an object")
    return cast(dict[str, Any], raw)


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def document_digest(document: dict[str, Any]) -> str:
    """Digest a contract document while excluding its self-identifying digest."""
    payload = {key: value for key, value in document.items() if key != "contract_digest"}
    return DIGEST_PREFIX + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def seal_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return the document after refreshing its deterministic contract digest."""
    document["contract_digest"] = document_digest(document)
    return document


def _expect_keys(value: object, required: set[str], allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    mapping = cast(dict[str, Any], value)
    missing = sorted(required - set(mapping))
    unknown = sorted(set(mapping) - allowed)
    if missing:
        _fail(f"{label} is missing required fields")
    if unknown:
        _fail(f"{label} contains unknown fields")
    return mapping


def _expect_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{label} must be an array")
    return cast(list[Any], value)


def _expect_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a non-empty string")
    return cast(str, value)


def _expect_int(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{label} must be an integer in range")
    return cast(int, value)


def _expect_opaque_id(value: object, label: str, prefix: str | None = None) -> str:
    text = _expect_text(value, label)
    if OPAQUE_ID.fullmatch(text) is None:
        _fail(f"{label} must be an opaque public ID")
    if prefix is not None and not text.startswith(f"{prefix}_"):
        _fail(f"{label} has the wrong opaque ID kind")
    return text


def _expect_sha(value: object, label: str) -> str:
    text = _expect_text(value, label)
    if SHA256.fullmatch(text) is None:
        _fail(f"{label} must be a sha256 identity")
    return text


def _expect_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        _fail(f"{label} must be unique")


def _check_publishable_string(value: str, label: str) -> None:
    if any(pattern.search(value) for pattern in _PRIVACY_SENTINELS):
        _fail(f"{label} contains prohibited privacy data")


def contains_private_locator(value: str) -> bool:
    """Return whether parser-neutral text contains a prohibited locator form."""
    candidate = _SAFE_LOGICAL_LOCATOR.sub("", value)
    return any(pattern.search(candidate) for pattern in _PRIVATE_LOCATOR_PATTERNS)


def _scan_publishable_value(value: object, label: str) -> None:
    if isinstance(value, str):
        _check_public_string(value, label)
    elif isinstance(value, dict):
        for key, nested in value.items():
            _check_public_string(str(key), label)
            _scan_publishable_value(nested, label)
    elif isinstance(value, list):
        for nested in value:
            _scan_publishable_value(nested, label)


def _scan_publishable_artifact(value: str, media_type: str) -> None:
    declared_type = media_type.partition(";")[0].strip().lower()
    json_required = declared_type == "application/json" or declared_type.endswith("+json")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, nested in pairs:
            if key in result:
                raise ValueError
            result[key] = nested
        return result

    def reject_nonstandard_constant(_constant: str) -> None:
        raise ValueError

    try:
        decoded = json.loads(
            value,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonstandard_constant,
        )
    except (json.JSONDecodeError, ValueError):
        if json_required:
            _fail("Published JSON artifact is invalid")
        _check_public_string(value, "published artifact")
        return

    _scan_publishable_value(decoded, "published artifact")


def _check_public_string(value: str, label: str) -> None:
    if contains_private_locator(value):
        _fail("Published value contains a prohibited locator")
    _check_publishable_string(value, label)


def _validate_publication_license(value: object) -> None:
    expression = _expect_text(value, "license_spdx")
    tokens = re.findall(r"\(|\)|AND|OR|[A-Za-z0-9][A-Za-z0-9.+-]*", expression)
    if not tokens or "".join(tokens) != re.sub(r"\s+", "", expression):
        _fail("license must use the approved publication policy")
    position = 0

    def parse_factor() -> None:
        nonlocal position
        if position >= len(tokens):
            _fail("license must use the approved publication policy")
        token = tokens[position]
        if token == "(":
            position += 1
            parse_or()
            if position >= len(tokens) or tokens[position] != ")":
                _fail("license must use the approved publication policy")
            position += 1
        elif token in PUBLICATION_LICENSE_IDS:
            position += 1
        else:
            _fail("license must use the approved publication policy")

    def parse_and() -> None:
        nonlocal position
        parse_factor()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            parse_factor()

    def parse_or() -> None:
        nonlocal position
        parse_and()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            parse_and()

    parse_or()
    if position != len(tokens):
        _fail("license must use the approved publication policy")


def _safe_artifact_path(root: Path, value: object) -> Path:
    relative = Path(_expect_text(value, "artifact_ref"))
    if relative.is_absolute() or ".." in relative.parts:
        _fail("artifact_ref must be repository-relative")
    _check_public_string(relative.as_posix(), "artifact_ref")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        _fail("artifact_ref escapes the corpus directory")
    return candidate


def _validate_provenance(value: object) -> None:
    data = _expect_keys(
        value,
        {"classification", "license_spdx", "origin", "pii_status", "publishable"},
        {"classification", "license_spdx", "origin", "pii_status", "publishable"},
        "provenance",
    )
    if data["classification"] not in {"synthetic", "licensed", "deidentified"}:
        _fail("provenance classification is not publishable")
    _validate_publication_license(data["license_spdx"])
    if data["pii_status"] != "reviewed_no_pii":
        _fail("PII review must be complete")
    if data["publishable"] is not True:
        _fail("resource must be publishable")
    _check_public_string(_expect_text(data["origin"], "origin"), "origin")


def _validate_unit(value: object, resource_id: str) -> tuple[str, dict[str, Any]]:
    data = _expect_keys(
        value,
        {"unit_id", "unit_kind", "representation_kind", "ordinal"},
        {
            "unit_id",
            "unit_kind",
            "representation_kind",
            "ordinal",
            "start_ms",
            "end_ms",
            "timestamp_ms",
            "frame_id",
        },
        "unit",
    )
    unit_id = _expect_opaque_id(data["unit_id"], "unit_id", "unit")
    if data["unit_kind"] not in UNIT_KINDS:
        _fail("unit_kind is invalid")
    if data["representation_kind"] not in REPRESENTATION_KINDS:
        _fail("representation_kind is invalid")
    _expect_int(data["ordinal"], "ordinal")
    if data["unit_kind"] == "time_segment":
        start = _expect_int(data.get("start_ms"), "start_ms")
        end = _expect_int(data.get("end_ms"), "end_ms")
        if end <= start:
            _fail("timed interval must be non-empty and half-open")
        if "timestamp_ms" in data or "frame_id" in data:
            _fail("timed unit contains frame fields")
    elif data["unit_kind"] == "frame":
        _expect_int(data.get("timestamp_ms"), "timestamp_ms")
        _expect_opaque_id(data.get("frame_id"), "frame_id", "frame")
        if "start_ms" in data or "end_ms" in data:
            _fail("frame unit contains interval fields")
    elif any(key in data for key in ("start_ms", "end_ms", "timestamp_ms", "frame_id")):
        _fail("non-timed unit contains temporal fields")
    return unit_id, {**data, "resource_id": resource_id}


def validate_corpus(path: Path) -> dict[str, Any]:
    """Validate the corpus manifest, artifact bytes, provenance, and roadmap counts."""
    data = _load_json(path)
    _scan_publishable_value(data, "corpus contract")
    required = {
        "contract",
        "schema_version",
        "corpus_id",
        "corpus_version",
        "source_namespace",
        "contract_digest",
        "query_set_ref",
        "policy_refs",
        "roadmap_scale",
        "resources",
    }
    _expect_keys(data, required, required, "corpus contract")
    if data["contract"] != CORPUS_CONTRACT or data["schema_version"] != SCHEMA_VERSION:
        _fail("corpus contract identity is unsupported")
    _expect_opaque_id(data["corpus_id"], "corpus_id", "corpus")
    _expect_text(data["corpus_version"], "corpus_version")
    _expect_text(data["source_namespace"], "source_namespace")
    if data["contract_digest"] != document_digest(data):
        _fail("corpus contract digest mismatch")
    query_ref = _expect_keys(
        data["query_set_ref"],
        {"path", "contract", "schema_version"},
        {"path", "contract", "schema_version"},
        "query_set_ref",
    )
    if query_ref["contract"] != QUERY_CONTRACT or query_ref["schema_version"] != SCHEMA_VERSION:
        _fail("query_set_ref identity is unsupported")
    _safe_artifact_path(path.parent.parent, query_ref["path"])
    refs = _expect_keys(
        data["policy_refs"],
        {"builder", "parser", "chunker", "vector_profile"},
        {"builder", "parser", "chunker", "vector_profile"},
        "policy_refs",
    )
    for name, value in refs.items():
        _expect_sha(value, name)
    scale = _expect_keys(
        data["roadmap_scale"], {"status", "required", "actual"}, {"status", "required", "actual"}, "roadmap_scale"
    )
    if scale["status"] != "satisfied":
        _fail("roadmap-scale fixture is not satisfied")
    resources = _expect_list(data["resources"], "resources")
    resource_ids: list[str] = []
    unit_ids: list[str] = []
    frame_ids: list[str] = []
    artifact_refs: list[str] = []
    artifact_digests: list[str] = []
    counts: Counter[str] = Counter()
    frame_video_ids: set[str] = set()
    for item in resources:
        resource = _expect_keys(
            item,
            {
                "resource_id",
                "resource_kind",
                "media_type",
                "source_namespace",
                "artifact_ref",
                "artifact_sha256",
                "content_sha256",
                "representations",
                "units",
                "provenance",
            },
            {
                "resource_id",
                "resource_kind",
                "media_type",
                "source_namespace",
                "artifact_ref",
                "artifact_sha256",
                "content_sha256",
                "representations",
                "units",
                "provenance",
            },
            "resource",
        )
        resource_id = _expect_opaque_id(resource["resource_id"], "resource_id", "res")
        if resource["resource_kind"] not in RESOURCE_KINDS:
            _fail("resource_kind is invalid")
        media_type = _expect_text(resource["media_type"], "media_type")
        if resource["source_namespace"] != data["source_namespace"]:
            _fail("resource source_namespace mismatch")
        artifact_ref = _expect_text(resource["artifact_ref"], "artifact_ref")
        artifact_path = _safe_artifact_path(path.parent, artifact_ref)
        if not artifact_path.is_file():
            _fail("referenced artifact is missing")
        try:
            artifact_bytes = artifact_path.read_bytes()
            artifact_text = artifact_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ContractError("Published artifact could not be read as UTF-8 text") from exc
        _scan_publishable_artifact(artifact_text, media_type)
        artifact_digest = DIGEST_PREFIX + hashlib.sha256(artifact_bytes).hexdigest()
        if resource["artifact_sha256"] != artifact_digest or resource["content_sha256"] != artifact_digest:
            _fail("artifact digest mismatch")
        artifact_refs.append(artifact_ref)
        artifact_digests.append(artifact_digest)
        representations = _expect_list(resource["representations"], "representations")
        if not representations or any(value not in REPRESENTATION_KINDS for value in representations):
            _fail("representations must use known kinds")
        representation_names = [str(value) for value in representations]
        _expect_unique(representation_names, "representations")
        _validate_provenance(resource["provenance"])
        resource_units = _expect_list(resource["units"], "units")
        if not resource_units:
            _fail("resource must declare at least one unit")
        resource_ordinals: list[int] = []
        timed_intervals: list[tuple[int, int]] = []
        frame_timestamps: list[int] = []
        for raw_unit in resource_units:
            unit_id, unit = _validate_unit(raw_unit, resource_id)
            if unit["representation_kind"] not in representation_names:
                _fail("unit references an undeclared representation")
            unit_ids.append(unit_id)
            resource_ordinals.append(cast(int, unit["ordinal"]))
            if unit["unit_kind"] == "time_segment":
                timed_intervals.append((cast(int, unit["start_ms"]), cast(int, unit["end_ms"])))
            if unit["unit_kind"] == "frame":
                frame_id = str(unit["frame_id"])
                frame_ids.append(frame_id)
                frame_video_ids.add(resource_id)
                frame_timestamps.append(cast(int, unit["timestamp_ms"]))
        if resource_ordinals != list(range(len(resource_units))):
            _fail("resource unit ordinals must be unique, ordered, and contiguous")
        if timed_intervals != sorted(timed_intervals) or any(
            previous[1] > current[0]
            for previous, current in zip(timed_intervals, timed_intervals[1:], strict=False)
        ):
            _fail("timed units must be ordered and non-overlapping")
        if frame_timestamps != sorted(set(frame_timestamps)):
            _fail("frame timestamps must be unique and ordered")
        resource_ids.append(resource_id)
        counts[str(resource["resource_kind"])] += 1
    _expect_unique(resource_ids, "resource IDs")
    _expect_unique(unit_ids, "unit IDs")
    _expect_unique(frame_ids, "frame IDs")
    _expect_unique(artifact_refs, "artifact references")
    _expect_unique(artifact_digests, "artifact digests")
    for kind, minimum in MIN_RESOURCE_COUNTS.items():
        if counts[kind] < minimum:
            _fail("corpus does not meet roadmap resource counts")
    if len(frame_video_ids) < MIN_FRAME_VIDEO_COUNT:
        _fail("corpus does not meet frame-video count")
    resource_count_keys = set(MIN_RESOURCE_COUNTS) | {"videos_with_frames"}
    actual = _expect_keys(
        scale["actual"],
        resource_count_keys,
        resource_count_keys,
        "actual counts",
    )
    expected_actual = {**dict(counts), "videos_with_frames": len(frame_video_ids)}
    if actual != expected_actual:
        _fail("roadmap actual counts do not match resources")
    required_counts = _expect_keys(scale["required"], set(expected_actual), set(expected_actual), "required counts")
    if any(_expect_int(required_counts[key], key, 1) > expected_actual[key] for key in expected_actual):
        _fail("roadmap required counts are not met")
    return data


def _validate_allowed(value: object) -> dict[str, list[str]]:
    data = _expect_keys(
        value,
        {"resource_kinds", "representation_kinds", "unit_kinds"},
        {"resource_kinds", "representation_kinds", "unit_kinds"},
        "allowed",
    )
    domains = {
        "resource_kinds": RESOURCE_KINDS,
        "representation_kinds": REPRESENTATION_KINDS,
        "unit_kinds": UNIT_KINDS,
    }
    normalized: dict[str, list[str]] = {}
    for key, domain in domains.items():
        values = _expect_list(data[key], key)
        if not values or any(value not in domain for value in values):
            _fail(f"{key} contains invalid values")
        texts = [str(value) for value in values]
        _expect_unique(texts, key)
        normalized[key] = texts
    return normalized


def _validate_judgment(
    value: object,
    resource_index: dict[str, dict[str, Any]],
    unit_index: dict[str, dict[str, Any]],
) -> tuple[str, str | None, int, str, bool]:
    data = _expect_keys(
        value,
        {"resource_id", "grade", "basis"},
        {"resource_id", "unit_id", "grade", "basis", "evidence"},
        "judgment",
    )
    resource_id = _expect_opaque_id(data["resource_id"], "judgment resource_id", "res")
    if resource_id not in resource_index:
        _fail("judgment references a missing resource")
    unit_id: str | None = None
    if "unit_id" in data:
        unit_id = _expect_opaque_id(data["unit_id"], "judgment unit_id", "unit")
        unit = unit_index.get(unit_id)
        if unit is None or unit["resource_id"] != resource_id:
            _fail("judgment references a missing or foreign unit")
    grade = _expect_int(data["grade"], "grade")
    if grade > 3:
        _fail("grade must be between 0 and 3")
    basis = _expect_text(data["basis"], "basis")
    if "evidence" in data:
        if unit_id is None:
            _fail("temporal evidence requires unit_id")
        evidence = _expect_keys(
            data["evidence"],
            {"kind"},
            {"kind", "start_ms", "end_ms", "timestamp_ms", "frame_id"},
            "evidence",
        )
        unit = unit_index[cast(str, unit_id)]
        if evidence["kind"] == "time_interval":
            start = _expect_int(evidence.get("start_ms"), "evidence start_ms")
            end = _expect_int(evidence.get("end_ms"), "evidence end_ms")
            if end <= start:
                _fail("judgment interval must be non-empty and half-open")
            if unit["unit_kind"] != "time_segment" or start != unit["start_ms"] or end != unit["end_ms"]:
                _fail("judgment interval does not match its timed unit")
            if set(evidence) != {"kind", "start_ms", "end_ms"}:
                _fail("time evidence contains frame fields")
        elif evidence["kind"] == "frame_timestamp":
            timestamp = _expect_int(evidence.get("timestamp_ms"), "evidence timestamp_ms")
            frame_id = _expect_opaque_id(
                evidence.get("frame_id"),
                "evidence frame_id",
                "frame",
            )
            if unit["unit_kind"] != "frame" or timestamp != unit["timestamp_ms"] or frame_id != unit["frame_id"]:
                _fail("judgment frame evidence does not match its unit")
            if set(evidence) != {"kind", "timestamp_ms", "frame_id"}:
                _fail("frame evidence contains interval fields")
        else:
            _fail("evidence kind is invalid")
    return resource_id, unit_id, grade, basis, "evidence" in data


def validate_queries(path: Path, corpus: dict[str, Any]) -> dict[str, Any]:
    """Validate graded query judgments and their links to the corpus contract."""
    data = _load_json(path)
    _scan_publishable_value(data, "query contract")
    required = {
        "contract",
        "schema_version",
        "query_set_id",
        "query_set_version",
        "corpus_ref",
        "contract_digest",
        "roadmap_scale",
        "cases",
    }
    _expect_keys(data, required, required, "query contract")
    if data["contract"] != QUERY_CONTRACT or data["schema_version"] != SCHEMA_VERSION:
        _fail("query contract identity is unsupported")
    _expect_opaque_id(data["query_set_id"], "query_set_id", "qry")
    _expect_text(data["query_set_version"], "query_set_version")
    if data["corpus_ref"] != corpus["contract_digest"]:
        _fail("query corpus_ref does not match corpus digest")
    if data["contract_digest"] != document_digest(data):
        _fail("query contract digest mismatch")
    scale = _expect_keys(
        data["roadmap_scale"], {"status", "required", "actual"}, {"status", "required", "actual"}, "query roadmap_scale"
    )
    if scale["status"] != "satisfied":
        _fail("query roadmap scale is not satisfied")
    cases = _expect_list(data["cases"], "cases")
    query_ids: list[str] = []
    counts: Counter[str] = Counter()
    resource_index = {resource["resource_id"]: resource for resource in corpus["resources"]}
    unit_index = {
        unit["unit_id"]: {**unit, "resource_id": resource["resource_id"]}
        for resource in corpus["resources"]
        for unit in resource["units"]
    }
    for item in cases:
        case = _expect_keys(
            item,
            {
                "query_id",
                "query_text",
                "case_kind",
                "mode",
                "target",
                "basis",
                "allowed",
                "cutoffs",
                "slice_tags",
                "judgments",
            },
            {
                "query_id",
                "query_text",
                "query_resource_id",
                "case_kind",
                "mode",
                "target",
                "basis",
                "allowed",
                "cutoffs",
                "slice_tags",
                "judgments",
            },
            "query case",
        )
        query_id = _expect_opaque_id(case["query_id"], "query_id", "qry")
        query_ids.append(query_id)
        query_text = _expect_text(case["query_text"], "query_text")
        _check_public_string(query_text, "query_text")
        case_kind = case["case_kind"]
        if case_kind not in CASE_KINDS or case["mode"] not in MODES or case["target"] not in TARGETS:
            _fail("query mode, target, or case_kind is invalid")
        if CASE_SHAPES.get(str(case_kind)) != (case["mode"], case["target"]):
            _fail("case_kind, mode, and target must match the frozen matrix")
        if case_kind == "resource_similarity":
            query_resource_id = _expect_opaque_id(
                case.get("query_resource_id"),
                "query_resource_id",
                "res",
            )
            if query_resource_id not in resource_index:
                _fail("similarity query references a missing resource")
        elif "query_resource_id" in case:
            _fail("query_resource_id is only valid for similarity")
        case_basis = _expect_text(case["basis"], "basis")
        allowed = _validate_allowed(case["allowed"])
        allowed_bases = {BASIS_BY_REPRESENTATION[value] for value in allowed["representation_kinds"]}
        if case_basis not in allowed_bases:
            _fail("case basis is incoherent with allowed representations")
        cutoffs = _expect_keys(
            case["cutoffs"], {"recall", "mrr", "ndcg"}, {"recall", "mrr", "ndcg"}, "cutoffs"
        )
        recall = _expect_list(cutoffs["recall"], "recall cutoffs")
        if recall != [5, 10] or cutoffs["mrr"] != 10 or cutoffs["ndcg"] != 10:
            _fail("quality cutoffs must be Recall@5/10, MRR@10, and nDCG@10")
        tags = _expect_list(case["slice_tags"], "slice_tags")
        if not tags or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            _fail("slice_tags must be non-empty strings")
        _expect_unique([str(tag) for tag in tags], "slice_tags")
        judgments = _expect_list(case["judgments"], "judgments")
        if not judgments:
            _fail("query has zero gold judgments")
        judgment_keys: list[tuple[str, str | None]] = []
        positive = 0
        has_temporal = False
        for judgment in judgments:
            resource_id, unit_id, grade, judgment_basis, has_evidence = _validate_judgment(
                judgment, resource_index, unit_index
            )
            resource = resource_index[resource_id]
            if case["target"] == "unit" and unit_id is None:
                _fail("unit-target judgment requires unit_id")
            if case["target"] == "resource" and (unit_id is not None or has_evidence):
                _fail("resource-target judgment cannot contain unit or temporal evidence")
            if judgment_basis != case_basis:
                _fail("case and judgment basis must match")
            if resource["resource_kind"] not in allowed["resource_kinds"]:
                _fail("judgment resource is outside allowed resource kinds")
            if unit_id is not None:
                unit = unit_index[unit_id]
                if judgment_basis != BASIS_BY_REPRESENTATION[unit["representation_kind"]]:
                    _fail("judgment basis is incoherent with its unit representation")
                if (
                    unit["unit_kind"] not in allowed["unit_kinds"]
                    or unit["representation_kind"] not in allowed["representation_kinds"]
                ):
                    _fail("judgment unit is outside allowed unit kinds")
            judgment_keys.append((resource_id, unit_id))
            positive += int(grade > 0)
            has_temporal = has_temporal or "evidence" in judgment
        if len(judgment_keys) != len(set(judgment_keys)):
            _fail("query contains duplicate judgments")
        if positive == 0:
            _fail("query has zero positive gold judgments")
        if case_kind == "timestamp" and (
            not has_temporal or any("evidence" not in judgment for judgment in judgments)
        ):
            _fail("timestamp judgments require timed or frame evidence")
        counts[str(case_kind)] += 1
    _expect_unique(query_ids, "query IDs")
    for kind, minimum in MIN_CASE_COUNTS.items():
        if counts[kind] < minimum:
            _fail("query set does not meet roadmap case counts")
    expected_actual = dict(counts)
    actual = _expect_keys(scale["actual"], set(MIN_CASE_COUNTS), set(MIN_CASE_COUNTS), "query actual counts")
    if actual != expected_actual:
        _fail("query actual counts do not match cases")
    required_counts = _expect_keys(
        scale["required"],
        set(MIN_CASE_COUNTS),
        set(MIN_CASE_COUNTS),
        "query required counts",
    )
    if any(_expect_int(required_counts[key], key, 1) > expected_actual[key] for key in MIN_CASE_COUNTS):
        _fail("query required counts are not met")
    return data


def validate_benchmark(
    path: Path,
    corpus: dict[str, Any],
    queries: dict[str, Any],
) -> dict[str, Any]:
    """Validate the gated, reproducible Stage 13 dataset materialization manifest."""
    data = _load_json(path)
    _scan_publishable_value(data, "benchmark contract")
    required = {
        "contract",
        "schema_version",
        "benchmark_id",
        "benchmark_version",
        "corpus_ref",
        "query_ref",
        "contract_digest",
        "materialization",
        "cells",
        "operations",
        "non_claims",
    }
    _expect_keys(data, required, required, "benchmark contract")
    if data["contract"] != BENCHMARK_CONTRACT or data["schema_version"] != SCHEMA_VERSION:
        _fail("benchmark contract identity is unsupported")
    _expect_opaque_id(data["benchmark_id"], "benchmark_id", "benchmark")
    _expect_text(data["benchmark_version"], "benchmark_version")
    if data["corpus_ref"] != corpus["contract_digest"] or data["query_ref"] != queries["contract_digest"]:
        _fail("benchmark fixture references do not match public contracts")
    if data["contract_digest"] != document_digest(data):
        _fail("benchmark contract digest mismatch")
    materialization = _expect_keys(
        data["materialization"],
        {"status", "gate", "seed", "artifact_policy"},
        {"status", "gate", "seed", "artifact_policy"},
        "materialization",
    )
    if materialization["status"] != "gated_manifest" or materialization["gate"] != "W5-B13":
        _fail("benchmark materialization must remain explicitly gated")
    _expect_int(materialization["seed"], "seed")
    if materialization["artifact_policy"] != "generated-no-binaries":
        _fail("benchmark artifact policy is unsupported")
    cells = _expect_list(data["cells"], "benchmark cells")
    expected_cells = {
        (units, dimensions)
        for units in (1_000, 10_000, 50_000, 100_000)
        for dimensions in (384, 768, 1024)
    }
    actual_cells: list[tuple[int, int]] = []
    for value in cells:
        cell = _expect_keys(value, {"units", "dimensions"}, {"units", "dimensions"}, "benchmark cell")
        actual_cells.append(
            (_expect_int(cell["units"], "units", 1), _expect_int(cell["dimensions"], "dimensions", 1))
        )
    if len(actual_cells) != len(set(actual_cells)) or set(actual_cells) != expected_cells:
        _fail("benchmark cells must freeze the complete roadmap matrix")
    operations = _expect_list(data["operations"], "operations")
    if not operations or any(not isinstance(item, str) or not item.strip() for item in operations):
        _fail("benchmark operations must be non-empty strings")
    _expect_unique([str(item) for item in operations], "benchmark operations")
    non_claims = _expect_list(data["non_claims"], "non_claims")
    if not non_claims or any(not isinstance(item, str) or not item.strip() for item in non_claims):
        _fail("benchmark non_claims must be explicit")
    return data


def validate_contracts(
    corpus_path: Path,
    queries_path: Path,
    benchmark_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate all documents and every cross-document identity."""
    corpus = validate_corpus(corpus_path)
    expected_queries_path = (
        corpus_path.parent.parent / corpus["query_set_ref"]["path"]
    ).resolve()
    if expected_queries_path != queries_path.resolve():
        _fail("query_set_ref does not match the supplied query contract")
    queries = validate_queries(queries_path, corpus)
    benchmark = validate_benchmark(benchmark_path, corpus, queries)
    return corpus, queries, benchmark
