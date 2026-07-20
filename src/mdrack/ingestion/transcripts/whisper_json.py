"""Whisper JSON transcript reader."""

from __future__ import annotations

from collections.abc import Mapping

from mdrack_media import ProducerFingerprint

from .common import (
    JSONSource,
    RawAtom,
    TranscriptReadError,
    TranscriptReadResult,
    build_artifact,
    seconds_to_ms,
    source_bytes_and_mapping,
    source_fingerprint,
)


def read_whisper_json(
    source: JSONSource,
    *,
    resource_id: str,
    producer_fingerprint: ProducerFingerprint,
    language: str | None = None,
    strict: bool = True,
) -> TranscriptReadResult:
    """Normalize Whisper segment JSON without grouping or source I/O."""
    encoded, payload = source_bytes_and_mapping(source)
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise TranscriptReadError("missing_segments")

    raw_atoms: list[RawAtom] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raw_atoms.append(RawAtom(0, 0, None, index))
            continue
        try:
            start_ms = seconds_to_ms(segment.get("start"))
            end_ms = seconds_to_ms(segment.get("end"))
        except TranscriptReadError as error:
            if strict:
                raise TranscriptReadError(error.code, source_ordinal=index) from None
            start_ms = end_ms = 0
        raw_atoms.append(
            RawAtom(
                start_ms=start_ms,
                end_ms=end_ms,
                text=segment.get("text"),
                source_ordinal=index,
                cue_id=segment.get("id"),
                speaker=segment.get("speaker"),
                confidence=segment.get("confidence"),
                timing_precision="segment",
            )
        )

    duration_ms = None
    if "duration" in payload:
        try:
            duration_ms = seconds_to_ms(payload["duration"])
        except TranscriptReadError as error:
            if strict:
                raise error

    return build_artifact(
        raw_atoms,
        resource_id=resource_id,
        producer_fingerprint=producer_fingerprint,
        language=language if language is not None else payload.get("language"),
        detected_format="whisper_json",
        source_digest=source_fingerprint(encoded),
        strict=strict,
        duration_ms=duration_ms,
    )
