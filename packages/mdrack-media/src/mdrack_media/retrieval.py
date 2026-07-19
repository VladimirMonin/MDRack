"""Deterministic retrieval composition for prepared transcript and frame batches."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from mdrack_core import PreparedResourceBatch, SearchScope, SearchUnitRecord

RetrievalMode = Literal["transcript", "frame", "hybrid"]
_TRANSCRIPT_UNIT = "time_segment"
_FRAME_UNIT = "frame"
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class MediaRetrievalItem:
    """One stable media unit returned by offline retrieval."""

    unit_id: str
    resource_id: str
    unit_kind: str
    score: float
    rank: int
    evidence_locator: object
    metadata: dict[str, object] = field(default_factory=dict)
    evidence: "MediaRetrievalEvidence | None" = None

    def to_dict(self) -> dict[str, object]:
        if self.evidence is None:
            raise ValueError("retrieval item has no assembled public evidence")
        return {
            "unit_id": self.unit_id,
            "resource_id": self.resource_id,
            "unit_kind": self.unit_kind,
            "score": self.score,
            "rank": self.rank,
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True)
class MediaRetrievalEvidence:
    """Privacy-safe public evidence for one media retrieval hit."""

    unit_id: str
    resource_id: str
    representation_id: str
    source_type: str
    unit_kind: str
    timestamp_ms: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    timestamp_unit: str = "ms"
    frame_id: str | None = None
    replacement: Mapping[str, object] = field(default_factory=dict)
    provenance: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "resource_id": self.resource_id,
            "representation_id": self.representation_id,
            "source_type": self.source_type,
            "unit_kind": self.unit_kind,
            "timestamp_ms": self.timestamp_ms,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "timestamp_unit": self.timestamp_unit,
            "frame_id": self.frame_id,
            "replacement": dict(self.replacement),
            "provenance": [dict(item) for item in self.provenance],
        }


@dataclass(frozen=True)
class MediaRetrievalResult:
    """Core results plus explicitly requested nearby frame evidence."""

    query: str
    mode: RetrievalMode
    items: tuple[MediaRetrievalItem, ...]
    nearby_frames: tuple[MediaRetrievalItem, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "mode": self.mode,
            "items": [item.to_dict() for item in self.items],
            "nearby_frames": [item.to_dict() for item in self.nearby_frames],
        }


def retrieve_media(
    batches: Sequence[PreparedResourceBatch],
    query: str,
    *,
    mode: RetrievalMode = "hybrid",
    scope: SearchScope | None = None,
    limit: int = 20,
    transcript_weight: float = 1.0,
    frame_weight: float = 1.0,
    nearby_frame_limit: int = 0,
) -> MediaRetrievalResult:
    """Search prepared media units without reading media or resolving locators.

    Categorical scope and mode narrowing are applied before scoring and limiting.
    Hybrid results use weighted reciprocal rank fusion (``rrf_k=60``); the two
    weights are intentionally caller-configurable and are not production defaults.
    Nearby frames are returned separately from the limited core result.
    """
    if mode not in {"transcript", "frame", "hybrid"}:
        raise ValueError("mode must be transcript, frame, or hybrid")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if type(nearby_frame_limit) is not int or nearby_frame_limit < 0:
        raise ValueError("nearby_frame_limit must be a non-negative integer")
    _validate_weight(transcript_weight, "transcript_weight")
    _validate_weight(frame_weight, "frame_weight")
    if mode == "hybrid" and transcript_weight == 0 and frame_weight == 0:
        raise ValueError("at least one hybrid weight must be positive")
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    effective_scope = scope if scope is not None else SearchScope()
    if not isinstance(effective_scope, SearchScope):
        raise TypeError("scope must be a SearchScope")
    if not query.strip():
        return MediaRetrievalResult(query, mode, ())

    units = _units(batches, mode, effective_scope)
    terms = tuple(_WORD.findall(query.casefold()))
    branch_units = {
        "transcript": tuple(unit for unit in units if unit.unit_kind == _TRANSCRIPT_UNIT),
        "frame": tuple(unit for unit in units if unit.unit_kind == _FRAME_UNIT),
    }
    ranked = {
        branch: _rank(branch_units[branch], terms)
        for branch in ("transcript", "frame")
        if (mode == "hybrid" or mode == branch)
    }
    if mode == "transcript":
        selected = _project(ranked.get("transcript", ()), limit, "transcript")
    elif mode == "frame":
        selected = _project(ranked.get("frame", ()), limit, "frame")
    else:
        selected = _hybrid(ranked, transcript_weight, frame_weight, limit)
    nearby = _nearby_frames(units, selected, nearby_frame_limit)
    return MediaRetrievalResult(query, mode, tuple(selected), nearby)


def _validate_weight(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")


def _units(
    batches: Sequence[PreparedResourceBatch], mode: RetrievalMode, scope: SearchScope
) -> tuple[SearchUnitRecord, ...]:
    if not isinstance(batches, (list, tuple)):
        raise TypeError("batches must be a sequence")
    allowed = {_TRANSCRIPT_UNIT, _FRAME_UNIT} if mode == "hybrid" else {
        _TRANSCRIPT_UNIT if mode == "transcript" else _FRAME_UNIT
    }
    if scope.unit_kinds:
        allowed.intersection_update(scope.unit_kinds)
    result: list[SearchUnitRecord] = []
    for batch in batches:
        resource = batch.resource
        representations = {item.representation_id: item for item in batch.representations}
        facets = {item.facet for item in batch.facets}
        if scope.resource_kinds and resource.resource_kind not in scope.resource_kinds:
            continue
        if scope.media_types and resource.media_type not in scope.media_types:
            continue
        if scope.source_namespaces and resource.source_namespace not in scope.source_namespaces:
            continue
        if scope.facets_any and facets.isdisjoint(scope.facets_any):
            continue
        if scope.facets_all and not set(scope.facets_all).issubset(facets):
            continue
        if scope.facets_none and not facets.isdisjoint(scope.facets_none):
            continue
        for unit in batch.units:
            representation = representations[unit.representation_id]
            if unit.unit_kind not in allowed:
                continue
            if scope.representation_kinds and representation.representation_kind not in scope.representation_kinds:
                continue
            if scope.modalities and unit.modality not in scope.modalities:
                continue
            result.append(unit)
    return tuple(result)


def _rank(units: Sequence[SearchUnitRecord], terms: tuple[str, ...]) -> tuple[tuple[SearchUnitRecord, float], ...]:
    scored = []
    for unit in units:
        text = (unit.text or "").casefold()
        score = float(sum(text.count(term) for term in terms))
        if score:
            scored.append((unit, score))
    scored.sort(key=lambda item: (-item[1], item[0].unit_id))
    return tuple(scored)


def _project(
    ranked: Sequence[tuple[SearchUnitRecord, float]], limit: int, branch_id: str
) -> list[MediaRetrievalItem]:
    return [
        _item(
            unit,
            score,
            rank,
            provenance=({"branch_id": branch_id, "branch_rank": rank, "branch_score": score},),
        )
        for rank, (unit, score) in enumerate(ranked[:limit], start=1)
    ]


def _hybrid(
    ranked: dict[str, tuple[tuple[SearchUnitRecord, float], ...]],
    transcript_weight: float,
    frame_weight: float,
    limit: int,
) -> list[MediaRetrievalItem]:
    scores: dict[str, float] = {}
    units: dict[str, SearchUnitRecord] = {}
    provenance: dict[str, list[Mapping[str, object]]] = {}
    for branch, weight in (("transcript", transcript_weight), ("frame", frame_weight)):
        if weight == 0:
            continue
        for rank, (unit, branch_score) in enumerate(ranked.get(branch, ()), start=1):
            scores[unit.unit_id] = scores.get(unit.unit_id, 0.0) + weight / (60 + rank)
            units[unit.unit_id] = unit
            provenance.setdefault(unit.unit_id, []).append(
                {
                    "branch_id": branch,
                    "branch_rank": rank,
                    "branch_weight": weight,
                    "branch_score": branch_score,
                }
            )
    ordered = sorted(scores, key=lambda unit_id: (-scores[unit_id], unit_id))[:limit]
    return [
        _item(
            units[unit_id],
            scores[unit_id],
            rank,
            provenance=tuple(provenance[unit_id]),
        )
        for rank, unit_id in enumerate(ordered, 1)
    ]


def _item(
    unit: SearchUnitRecord,
    score: float,
    rank: int,
    *,
    provenance: tuple[Mapping[str, object], ...] = (),
) -> MediaRetrievalItem:
    return MediaRetrievalItem(
        unit.unit_id,
        unit.resource_id,
        unit.unit_kind,
        score,
        rank,
        unit.evidence_locator,
        dict(unit.metadata),
        _evidence(unit, provenance=provenance),
    )


_PUBLIC_METADATA_KEYS = frozenset(
    {
        "content_fingerprint",
        "observation_identity",
        "source_atom_ids",
        "similarity_basis",
        "grouper_fingerprint",
        "aggregation_fingerprint",
        "normalization_fingerprint",
        "producer_fingerprint",
    }
)


def _evidence(
    unit: SearchUnitRecord,
    *,
    provenance: tuple[Mapping[str, object], ...],
) -> MediaRetrievalEvidence:
    payload = unit.evidence_locator.payload
    source_type = "frame" if unit.unit_kind == _FRAME_UNIT else "transcript"
    if unit.unit_kind == "whole_resource":
        source_type = "whole_resource"
    replacement = {
        key: value for key, value in unit.metadata.items() if key in _PUBLIC_METADATA_KEYS
    }
    timestamp = payload.get("timestamp_ms")
    start = payload.get("start_ms")
    end = payload.get("end_ms")
    frame_value = payload.get("frame_id")
    return MediaRetrievalEvidence(
        unit_id=unit.unit_id,
        resource_id=unit.resource_id,
        representation_id=unit.representation_id,
        source_type=source_type,
        unit_kind=unit.unit_kind,
        timestamp_ms=timestamp if type(timestamp) is int else None,
        start_ms=start if type(start) is int else None,
        end_ms=end if type(end) is int else None,
        frame_id=frame_value if isinstance(frame_value, str) else None,
        replacement=replacement,
        provenance=provenance,
    )


def _nearby_frames(
    units: Sequence[SearchUnitRecord], selected: Sequence[MediaRetrievalItem], limit: int
) -> tuple[MediaRetrievalItem, ...]:
    if limit == 0:
        return ()
    selected_ids = {item.unit_id for item in selected}
    selected_times: dict[str, list[int]] = {}
    for item in selected:
        timestamp = _timestamp(item.evidence_locator)
        if timestamp is not None:
            selected_times.setdefault(item.resource_id, []).append(timestamp)
    candidates = []
    for unit in units:
        if unit.unit_kind != _FRAME_UNIT or unit.unit_id in selected_ids:
            continue
        timestamp = _timestamp(unit.evidence_locator)
        if timestamp is None or unit.resource_id not in selected_times:
            continue
        distance = min(
            abs(timestamp - selected_time)
            for selected_time in selected_times[unit.resource_id]
        )
        candidates.append((distance, unit))
    candidates.sort(key=lambda item: (item[0], item[1].unit_id))
    return tuple(
        _item(
            unit,
            0.0,
            rank,
            provenance=(
                {"relation": "nearby_frame", "distance_ms": distance},
            ),
        )
        for rank, (distance, unit) in enumerate(candidates[:limit], 1)
    )


def _timestamp(locator: object) -> int | None:
    payload = getattr(locator, "payload", None)
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("timestamp_ms")
    if type(value) is int:
        return value
    start_ms = payload.get("start_ms")
    return start_ms if type(start_ms) is int else None


__all__ = ["MediaRetrievalItem", "MediaRetrievalResult", "RetrievalMode", "retrieve_media"]
