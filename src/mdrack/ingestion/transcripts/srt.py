"""SubRip (SRT) transcript reader."""

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


def read_srt(
    source: str | bytes | bytearray,
    *,
    resource_id: str,
    producer_fingerprint: ProducerFingerprint,
    language: str | None = None,
    strict: bool = True,
) -> TranscriptReadResult:
    """Normalize SubRip cues without interpreting filename or grouping passages."""
    encoded = source_bytes(source)
    try:
        text = encoded.decode("utf-8-sig", "strict").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise TranscriptReadError("invalid_subtitle") from None

    blocks = [block for block in text.split("\n\n") if block.strip()]
    raw_atoms: list[RawAtom] = []
    for index, block in enumerate(blocks):
        lines = block.splitlines()
        timing_index = next((i for i, line in enumerate(lines[:2]) if "-->" in line), None)
        if timing_index is None:
            if strict:
                raise TranscriptReadError("invalid_subtitle", source_ordinal=index)
            raw_atoms.append(RawAtom(0, 0, None, index))
            continue
        cue_id = lines[0].strip() if timing_index == 1 else None
        left, right = lines[timing_index].split("-->", 1)
        try:
            start_ms = subtitle_timestamp_to_ms(left.strip())
            end_ms = subtitle_timestamp_to_ms(right.strip())
        except TranscriptReadError as error:
            if strict:
                raise TranscriptReadError(error.code, source_ordinal=index) from None
            start_ms = end_ms = 0
        raw_atoms.append(
            RawAtom(
                start_ms,
                end_ms,
                clean_subtitle_text("\n".join(lines[timing_index + 1 :])),
                index,
                cue_id=cue_id,
                timing_precision="millisecond",
            )
        )

    return build_artifact(
        raw_atoms,
        resource_id=resource_id,
        producer_fingerprint=producer_fingerprint,
        language=language,
        detected_format="srt",
        source_digest=source_fingerprint(encoded),
        strict=strict,
    )
