"""Generic mdrack.timed-transcript.v1 JSON reader."""

from __future__ import annotations

from collections.abc import Mapping

from mdrack_media import ProducerFingerprint

from .common import (
    JSONSource,
    RawAtom,
    TranscriptReadError,
    TranscriptReadResult,
    build_artifact,
    exact_ms,
    source_bytes_and_mapping,
    source_fingerprint,
)

TIMED_TRANSCRIPT_SCHEMA = "mdrack.timed-transcript.v1"


def read_timed_json(
    source: JSONSource,
    *,
    resource_id: str,
    producer_fingerprint: ProducerFingerprint,
    language: str | None = None,
    strict: bool = True,
) -> TranscriptReadResult:
    """Normalize the frozen generic timed JSON v1 shape into existing media records."""
    encoded, payload = source_bytes_and_mapping(source)
    if payload.get("schema") != TIMED_TRANSCRIPT_SCHEMA:
        raise TranscriptReadError("unsupported_schema")
    source_atoms = payload.get("atoms")
    if not isinstance(source_atoms, list):
        raise TranscriptReadError("missing_atoms")

    raw_atoms: list[RawAtom] = []
    for index, atom in enumerate(source_atoms):
        if not isinstance(atom, Mapping):
            raw_atoms.append(RawAtom(0, 0, None, index))
            continue
        try:
            start_ms = exact_ms(atom.get("start_ms"))
            end_ms = exact_ms(atom.get("end_ms"))
        except TranscriptReadError as error:
            if strict:
                raise TranscriptReadError(error.code, source_ordinal=index) from None
            start_ms = end_ms = 0
        raw_atoms.append(
            RawAtom(
                start_ms=start_ms,
                end_ms=end_ms,
                text=atom.get("text"),
                source_ordinal=index,
                cue_id=atom.get("atom_id"),
                speaker=atom.get("speaker"),
                confidence=atom.get("confidence"),
                timing_precision=atom.get("timing_precision"),
            )
        )

    duration_ms = None
    if "duration_ms" in payload:
        try:
            duration_ms = exact_ms(payload["duration_ms"])
        except TranscriptReadError as error:
            if strict:
                raise error

    return build_artifact(
        raw_atoms,
        resource_id=resource_id,
        producer_fingerprint=producer_fingerprint,
        language=language if language is not None else payload.get("language"),
        detected_format="timed_json_v1",
        source_digest=source_fingerprint(encoded),
        strict=strict,
        duration_ms=duration_ms,
    )
