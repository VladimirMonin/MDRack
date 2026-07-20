"""WebVTT transcript reader."""

from __future__ import annotations

from mdrack_media import ProducerFingerprint

from .common import (
    RawAtom,
    TranscriptReadError,
    TranscriptReadResult,
    build_artifact,
    clean_subtitle_text,
    source_bytes,
    source_fingerprint,
    subtitle_timestamp_to_ms,
)


def read_vtt(
    source: str | bytes | bytearray,
    *,
    resource_id: str,
    producer_fingerprint: ProducerFingerprint,
    language: str | None = None,
    strict: bool = True,
) -> TranscriptReadResult:
    """Normalize WebVTT cues; NOTE/STYLE/REGION blocks are not transcript atoms."""
    encoded = source_bytes(source)
    try:
        text = encoded.decode("utf-8-sig", "strict").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise TranscriptReadError("invalid_subtitle") from None
    if not text.startswith("WEBVTT"):
        raise TranscriptReadError("invalid_subtitle")

    _, separator, body = text.partition("\n\n")
    if not separator:
        body = ""
    blocks = [block for block in body.split("\n\n") if block.strip()]
    raw_atoms: list[RawAtom] = []
    cue_index = 0
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        if lines[0].strip().startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = next((i for i, line in enumerate(lines[:2]) if "-->" in line), None)
        if timing_index is None:
            if strict:
                raise TranscriptReadError("invalid_subtitle", source_ordinal=cue_index)
            raw_atoms.append(RawAtom(0, 0, None, cue_index))
            cue_index += 1
            continue
        cue_id = lines[0].strip() if timing_index == 1 else None
        timing = lines[timing_index]
        left, right = timing.split("-->", 1)
        right_timestamp = right.strip().split(maxsplit=1)[0]
        try:
            start_ms = subtitle_timestamp_to_ms(left.strip())
            end_ms = subtitle_timestamp_to_ms(right_timestamp)
        except TranscriptReadError as error:
            if strict:
                raise TranscriptReadError(error.code, source_ordinal=cue_index) from None
            start_ms = end_ms = 0
        raw_atoms.append(
            RawAtom(
                start_ms,
                end_ms,
                clean_subtitle_text("\n".join(lines[timing_index + 1 :])),
                cue_index,
                cue_id=cue_id,
                timing_precision="millisecond",
            )
        )
        cue_index += 1

    return build_artifact(
        raw_atoms,
        resource_id=resource_id,
        producer_fingerprint=producer_fingerprint,
        language=language,
        detected_format="webvtt",
        source_digest=source_fingerprint(encoded),
        strict=strict,
    )
