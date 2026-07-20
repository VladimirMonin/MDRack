"""Deterministic application-owned transcript readers.

Readers accept in-memory text/bytes or decoded JSON objects. They do not access the
filesystem, call providers, group passages, or mutate source data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from mdrack_media import ProducerFingerprint

from .common import (
    NORMALIZATION_FINGERPRINT,
    JSONSource,
    TranscriptDiagnostic,
    TranscriptReadError,
    TranscriptReadResult,
)
from .srt import read_srt
from .timed_json import TIMED_TRANSCRIPT_SCHEMA, read_timed_json
from .webvtt import read_vtt
from .whisper_json import read_whisper_json

_SRT_TIMING = re.compile(r"(?m)^\s*\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+")


def read_transcript(
    source: JSONSource,
    *,
    resource_id: str,
    producer_fingerprint: ProducerFingerprint,
    language: str | None = None,
    strict: bool = True,
) -> TranscriptReadResult:
    """Autodetect one supported format from content and call its bounded reader."""
    if isinstance(source, Mapping):
        if source.get("schema") == TIMED_TRANSCRIPT_SCHEMA:
            return read_timed_json(
                source,
                resource_id=resource_id,
                producer_fingerprint=producer_fingerprint,
                language=language,
                strict=strict,
            )
        if "segments" in source:
            return read_whisper_json(
                source,
                resource_id=resource_id,
                producer_fingerprint=producer_fingerprint,
                language=language,
                strict=strict,
            )
        raise TranscriptReadError("unknown_format")

    if isinstance(source, str):
        text = source
    elif isinstance(source, (bytes, bytearray)):
        try:
            text = bytes(source).decode("utf-8-sig", "strict")
        except UnicodeDecodeError:
            raise TranscriptReadError("unknown_format") from None
    else:
        raise TranscriptReadError("unknown_format")

    if text.lstrip("\ufeff").startswith("WEBVTT"):
        return read_vtt(
            source,
            resource_id=resource_id,
            producer_fingerprint=producer_fingerprint,
            language=language,
            strict=strict,
        )
    if _SRT_TIMING.search(text):
        return read_srt(
            source,
            resource_id=resource_id,
            producer_fingerprint=producer_fingerprint,
            language=language,
            strict=strict,
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise TranscriptReadError("unknown_format") from None
    if not isinstance(payload, Mapping):
        raise TranscriptReadError("unknown_format")
    if payload.get("schema") == TIMED_TRANSCRIPT_SCHEMA:
        return read_timed_json(
            source,
            resource_id=resource_id,
            producer_fingerprint=producer_fingerprint,
            language=language,
            strict=strict,
        )
    if "segments" in payload:
        return read_whisper_json(
            source,
            resource_id=resource_id,
            producer_fingerprint=producer_fingerprint,
            language=language,
            strict=strict,
        )
    raise TranscriptReadError("unknown_format")


__all__ = [
    "NORMALIZATION_FINGERPRINT",
    "TIMED_TRANSCRIPT_SCHEMA",
    "TranscriptDiagnostic",
    "TranscriptReadError",
    "TranscriptReadResult",
    "read_srt",
    "read_timed_json",
    "read_transcript",
    "read_vtt",
    "read_whisper_json",
]
