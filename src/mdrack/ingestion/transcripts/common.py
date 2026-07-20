"""Shared, source-neutral support for application-owned transcript readers."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import TypeAlias, cast

from mdrack_media import (
    REPRESENTATION_AUDIO_TRANSCRIPT,
    JSONValue,
    NormalizationFingerprint,
    ProducerFingerprint,
    TimedTextAtom,
    TranscriptArtifact,
    atom_id,
    canonical_json,
    representation_id,
)

JSONSource: TypeAlias = str | bytes | bytearray | Mapping[str, object]

_NORMALIZATION_PAYLOAD = {
    "line_endings": "unicode-whitespace-collapse",
    "text": "strip-and-collapse-v1",
    "version": 1,
}
NORMALIZATION_FINGERPRINT = NormalizationFingerprint.from_payload(_NORMALIZATION_PAYLOAD)
_TIMESTAMP_PATTERN = re.compile(
    r"^(?:(?P<hours>\d+):)?(?P<minutes>\d{2}):(?P<seconds>\d{2})[.,](?P<millis>\d{3})$"
)
_SUBTITLE_TAG_PATTERN = re.compile(r"</?[^>]+>")
_ERROR_MESSAGES = {
    "invalid_json": "transcript source is not valid JSON",
    "invalid_root": "transcript source has an invalid root shape",
    "missing_segments": "transcript source does not contain a segments array",
    "missing_atoms": "transcript source does not contain an atoms array",
    "invalid_atom": "transcript atom has an invalid shape",
    "invalid_timing": "transcript atom has invalid timing",
    "empty_text": "transcript atom text is empty",
    "overlapping_interval": "transcript atoms overlap or are out of order",
    "unsupported_schema": "timed transcript schema is unsupported",
    "invalid_subtitle": "subtitle source has an invalid cue structure",
    "unknown_format": "transcript format could not be detected",
}


@dataclass(frozen=True)
class TranscriptDiagnostic:
    """Privacy-safe diagnostic: category and ordinal only, never source values."""

    code: str
    source_ordinal: int | None = None


class TranscriptReadError(ValueError):
    """Fail-closed reader error whose message never includes source content."""

    def __init__(self, code: str, *, source_ordinal: int | None = None) -> None:
        self.code = code
        self.source_ordinal = source_ordinal
        super().__init__(_ERROR_MESSAGES.get(code, "transcript source is invalid"))


@dataclass(frozen=True)
class TranscriptReadResult:
    artifact: TranscriptArtifact
    detected_format: str
    source_fingerprint: str
    diagnostics: tuple[TranscriptDiagnostic, ...] = ()


@dataclass(frozen=True)
class RawAtom:
    start_ms: int
    end_ms: int
    text: object
    source_ordinal: int
    cue_id: object = None
    speaker: object = None
    confidence: object = None
    timing_precision: object = None


def source_bytes_and_mapping(source: JSONSource) -> tuple[bytes, Mapping[str, object]]:
    if isinstance(source, Mapping):
        try:
            encoded = canonical_json(source).encode("utf-8")
        except (UnicodeError, ValueError):
            raise TranscriptReadError("invalid_json") from None
        return encoded, source
    if isinstance(source, str):
        try:
            encoded = source.encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise TranscriptReadError("invalid_json") from None
    elif isinstance(source, (bytes, bytearray)):
        encoded = bytes(source)
    else:
        raise TranscriptReadError("invalid_json")
    try:
        decoded = encoded.decode("utf-8-sig", "strict")
        value = json.loads(decoded)
    except (UnicodeError, json.JSONDecodeError):
        raise TranscriptReadError("invalid_json") from None
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TranscriptReadError("invalid_root")
    return encoded, cast(Mapping[str, object], value)


def source_bytes(source: str | bytes | bytearray) -> bytes:
    if isinstance(source, str):
        try:
            return source.encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise TranscriptReadError("invalid_subtitle") from None
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    raise TranscriptReadError("invalid_subtitle")


def source_fingerprint(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def seconds_to_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise TranscriptReadError("invalid_timing")
    try:
        seconds = Decimal(str(value))
        if not seconds.is_finite() or seconds < 0:
            raise TranscriptReadError("invalid_timing")
        return int((seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (DecimalException, OverflowError, ValueError):
        raise TranscriptReadError("invalid_timing") from None


def exact_ms(value: object) -> int:
    if type(value) is not int or value < 0:
        raise TranscriptReadError("invalid_timing")
    return value


def subtitle_timestamp_to_ms(value: str) -> int:
    match = _TIMESTAMP_PATTERN.fullmatch(value.strip())
    if match is None:
        raise TranscriptReadError("invalid_timing")
    try:
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes"))
        seconds = int(match.group("seconds"))
        millis = int(match.group("millis"))
    except (OverflowError, ValueError):
        raise TranscriptReadError("invalid_timing") from None
    if minutes >= 60 or seconds >= 60:
        raise TranscriptReadError("invalid_timing")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def normalize_text(value: object) -> str:
    if not isinstance(value, str):
        raise TranscriptReadError("invalid_atom")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise TranscriptReadError("invalid_atom") from None
    normalized = " ".join(value.split())
    if not normalized:
        raise TranscriptReadError("empty_text")
    return normalized


def clean_subtitle_text(value: str) -> str:
    """Remove cue markup before canonical whitespace normalization."""
    return html.unescape(_SUBTITLE_TAG_PATTERN.sub("", value))


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TranscriptReadError("invalid_atom")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise TranscriptReadError("invalid_atom") from None
    del field
    return value.strip()


def _optional_probability(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptReadError("invalid_atom")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise TranscriptReadError("invalid_atom")
    return result


def _safe_atom_metadata(raw: RawAtom) -> dict[str, JSONValue]:
    metadata: dict[str, JSONValue] = {}
    if isinstance(raw.cue_id, (str, int)) and not isinstance(raw.cue_id, bool):
        metadata["source_cue_id"] = raw.cue_id
    if isinstance(raw.timing_precision, str) and raw.timing_precision.strip():
        metadata["timing_precision"] = raw.timing_precision.strip()
    return metadata


def build_artifact(
    raw_atoms: Sequence[RawAtom],
    *,
    resource_id: str,
    producer_fingerprint: ProducerFingerprint,
    language: object,
    detected_format: str,
    source_digest: str,
    strict: bool,
    duration_ms: int | None = None,
) -> TranscriptReadResult:
    if not isinstance(producer_fingerprint, ProducerFingerprint):
        raise ValueError("producer_fingerprint must be a ProducerFingerprint")
    resolved_language = _optional_text(language, "language")
    diagnostics: list[TranscriptDiagnostic] = []
    atoms: list[TimedTextAtom] = []
    previous_end = -1

    for raw in raw_atoms:
        try:
            start_ms = exact_ms(raw.start_ms)
            end_ms = exact_ms(raw.end_ms)
            if end_ms <= start_ms:
                raise TranscriptReadError("invalid_timing")
            text = normalize_text(raw.text)
            if start_ms < previous_end:
                raise TranscriptReadError("overlapping_interval")
            speaker = _optional_text(raw.speaker, "speaker")
            confidence = _optional_probability(raw.confidence)
        except TranscriptReadError as error:
            if strict:
                raise TranscriptReadError(error.code, source_ordinal=raw.source_ordinal) from None
            diagnostics.append(TranscriptDiagnostic(error.code, raw.source_ordinal))
            continue

        ordinal = len(atoms)
        atoms.append(
            TimedTextAtom(
                atom_id=atom_id(resource_id, producer_fingerprint.value, ordinal),
                resource_id=resource_id,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                ordinal=ordinal,
                producer_fingerprint=producer_fingerprint,
                normalization_fingerprint=NORMALIZATION_FINGERPRINT,
                speaker=speaker,
                confidence=confidence,
                metadata=_safe_atom_metadata(raw),
            )
        )
        previous_end = end_ms

    computed_duration = atoms[-1].end_ms if atoms else None
    if duration_ms is not None:
        exact_ms(duration_ms)
        if computed_duration is not None and duration_ms < computed_duration:
            if strict:
                raise TranscriptReadError("invalid_timing")
            diagnostics.append(TranscriptDiagnostic("invalid_timing"))
        else:
            computed_duration = duration_ms

    representation = representation_id(
        resource_id,
        REPRESENTATION_AUDIO_TRANSCRIPT,
        producer_fingerprint.value,
        NORMALIZATION_FINGERPRINT.value,
    )
    artifact = TranscriptArtifact(
        resource_id=resource_id,
        representation_id=representation,
        representation_kind=REPRESENTATION_AUDIO_TRANSCRIPT,
        atoms=tuple(atoms),
        producer_fingerprint=producer_fingerprint,
        normalization_fingerprint=NORMALIZATION_FINGERPRINT,
        language=resolved_language,
        duration_ms=computed_duration,
        metadata={
            "diagnostic_count": len(diagnostics),
            "reader_format": detected_format,
            "source_fingerprint": source_digest,
        },
    )
    return TranscriptReadResult(
        artifact=artifact,
        detected_format=detected_format,
        source_fingerprint=source_digest,
        diagnostics=tuple(diagnostics),
    )
