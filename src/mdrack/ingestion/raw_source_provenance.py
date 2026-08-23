"""Pure, privacy-safe provenance contract for future raw local resources.

This module deliberately contains no extractor, adapter, provider, database, or
CLI behavior. Paths and source bytes exist only in the transient snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from mdrack_core import Locator

RAW_SOURCE_PROVENANCE_SCHEMA = "mdrack.raw-source-provenance.v1"
RAW_SOURCE_LOCATOR_KIND = "raw_local_source"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _require_sha256(value: object) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)


class RawMediaKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class RawSignatureKind(StrEnum):
    UTF8_TEXT = "utf8_text"
    PNG = "png"
    JPEG = "jpeg"
    GIF = "gif"
    WEBP = "webp"
    RIFF_WAVE = "riff_wave"
    ISO_BMFF = "iso_bmff"
    MATROSKA_EBML = "matroska_ebml"


class RawSourceErrorCode(StrEnum):
    SOURCE_REF_INVALID = "source_ref_invalid"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_NOT_REGULAR = "source_not_regular"
    SOURCE_EMPTY = "source_empty"
    SOURCE_TOO_LARGE = "source_too_large"
    SIGNATURE_UNSUPPORTED = "signature_unsupported"
    MIME_SIGNATURE_MISMATCH = "mime_signature_mismatch"
    DURATION_LIMIT_EXCEEDED = "duration_limit_exceeded"
    SELECTED_FRAME_LIMIT_EXCEEDED = "selected_frame_limit_exceeded"
    PREPARED_TEXT_LIMIT_EXCEEDED = "prepared_text_limit_exceeded"
    PREPARED_EVIDENCE_INVALID = "prepared_evidence_invalid"
    SOURCE_CHANGED = "source_changed"


class RawSourceError(ValueError):
    """Fixed, payload-free error suitable for safe diagnostics."""

    def __init__(self, code: RawSourceErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class RawInputBudget:
    max_source_bytes: int = 33_554_432
    max_duration_ms: int = 3_600_000
    max_selected_frames: int = 600
    max_prepared_text_bytes: int = 8_388_608
    max_whole_resource_tokens: int = 8000

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (
                self.max_source_bytes,
                self.max_duration_ms,
                self.max_selected_frames,
                self.max_prepared_text_bytes,
                self.max_whole_resource_tokens,
            )
        ):
            raise ValueError("budget values must be non-negative integers")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_duration_ms": self.max_duration_ms,
            "max_prepared_text_bytes": self.max_prepared_text_bytes,
            "max_selected_frames": self.max_selected_frames,
            "max_source_bytes": self.max_source_bytes,
            "max_whole_resource_tokens": self.max_whole_resource_tokens,
        }

    @property
    def fingerprint(self) -> str:
        return sha256_digest(canonical_json(self.to_dict()))


@dataclass(frozen=True)
class RawSignatureFact:
    kind: RawSignatureKind
    verified_media_type: str
    verifier_id: str

    def __post_init__(self) -> None:
        try:
            kind = RawSignatureKind(self.kind)
        except (TypeError, ValueError):
            raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED) from None
        object.__setattr__(self, "kind", kind)
        if not isinstance(self.verified_media_type, str) or not self.verified_media_type:
            raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED)
        if not isinstance(self.verifier_id, str) or not self.verifier_id:
            raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED)
        if self.verified_media_type not in _SIGNATURE_MIME_TYPES.get(kind, frozenset()):
            raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "verified_media_type": self.verified_media_type,
            "verifier_id": self.verifier_id,
        }


_SIGNATURE_MIME_TYPES: dict[RawSignatureKind, frozenset[str]] = {
    RawSignatureKind.UTF8_TEXT: frozenset({"text/plain", "text/markdown", "text/csv", "application/json"}),
    RawSignatureKind.PNG: frozenset({"image/png"}),
    RawSignatureKind.JPEG: frozenset({"image/jpeg"}),
    RawSignatureKind.GIF: frozenset({"image/gif"}),
    RawSignatureKind.WEBP: frozenset({"image/webp"}),
    RawSignatureKind.RIFF_WAVE: frozenset({"audio/wav", "audio/x-wav"}),
    RawSignatureKind.ISO_BMFF: frozenset({"audio/mp4", "video/mp4", "video/quicktime"}),
    RawSignatureKind.MATROSKA_EBML: frozenset({"audio/x-matroska", "video/x-matroska"}),
}


@dataclass(frozen=True)
class RawSourceProvenance:
    source_ref: str
    media_kind: RawMediaKind
    raw_source_sha256: str
    byte_size: int
    signature: RawSignatureFact
    duration_ms: int | None
    selected_frame_count: int
    prepared_evidence_sha256: str | None
    budget_fingerprint: str

    def __post_init__(self) -> None:
        try:
            media_kind = RawMediaKind(self.media_kind)
        except (TypeError, ValueError):
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
        object.__setattr__(self, "media_kind", media_kind)
        validate_source_ref(self.source_ref)
        if not isinstance(self.signature, RawSignatureFact):
            raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)
        validate_signature_for_media(media_kind, self.signature)
        _require_sha256(self.raw_source_sha256)
        if self.prepared_evidence_sha256 is not None:
            _require_sha256(self.prepared_evidence_sha256)
        _require_sha256(self.budget_fingerprint)
        if not isinstance(self.byte_size, int) or isinstance(self.byte_size, bool) or self.byte_size <= 0:
            raise RawSourceError(RawSourceErrorCode.SOURCE_EMPTY)
        if not isinstance(self.selected_frame_count, int) or isinstance(self.selected_frame_count, bool):
            raise RawSourceError(RawSourceErrorCode.SELECTED_FRAME_LIMIT_EXCEEDED)
        if not isinstance(self.duration_ms, (int, type(None))) or isinstance(self.duration_ms, bool):
            raise RawSourceError(RawSourceErrorCode.DURATION_LIMIT_EXCEEDED)
        if self.media_kind in (RawMediaKind.TEXT, RawMediaKind.IMAGE):
            if self.duration_ms is not None or self.selected_frame_count != 0:
                raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        elif self.duration_ms is None or self.duration_ms < 0:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        if self.selected_frame_count < 0:
            raise RawSourceError(RawSourceErrorCode.SELECTED_FRAME_LIMIT_EXCEEDED)
        if self.media_kind is RawMediaKind.VIDEO and self.selected_frame_count == 0:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        if self.media_kind is not RawMediaKind.VIDEO and self.selected_frame_count != 0:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)

    @property
    def locator(self) -> Locator:
        return Locator(RAW_SOURCE_LOCATOR_KIND, {"source_ref": self.source_ref})

    @property
    def content_hash(self) -> str:
        return self.raw_source_sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_size": self.byte_size,
            "budget_fingerprint": self.budget_fingerprint,
            "duration_ms": self.duration_ms,
            "media_kind": self.media_kind.value,
            "prepared_evidence_sha256": self.prepared_evidence_sha256,
            "raw_source_sha256": self.raw_source_sha256,
            "schema": RAW_SOURCE_PROVENANCE_SCHEMA,
            "selected_frame_count": self.selected_frame_count,
            "signature": self.signature.to_dict(),
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RawSourceProvenance:
        if set(value) != {
            "byte_size", "budget_fingerprint", "duration_ms", "media_kind",
            "prepared_evidence_sha256", "raw_source_sha256", "schema",
            "selected_frame_count", "signature", "source_ref",
        } or value.get("schema") != RAW_SOURCE_PROVENANCE_SCHEMA:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        signature = value.get("signature")
        if not isinstance(signature, Mapping):
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        try:
            if (
                set(signature) != {"kind", "verified_media_type", "verifier_id"}
                or not isinstance(value["schema"], str)
                or not isinstance(value["source_ref"], str)
                or not isinstance(value["media_kind"], str)
                or not isinstance(value["raw_source_sha256"], str)
                or not isinstance(value["byte_size"], int)
                or isinstance(value["byte_size"], bool)
                or not isinstance(value["duration_ms"], (int, type(None)))
                or isinstance(value["duration_ms"], bool)
                or not isinstance(value["selected_frame_count"], int)
                or isinstance(value["selected_frame_count"], bool)
                or not isinstance(value["prepared_evidence_sha256"], (str, type(None)))
                or not isinstance(value["budget_fingerprint"], str)
                or not isinstance(signature["kind"], str)
                or not isinstance(signature["verified_media_type"], str)
                or not isinstance(signature["verifier_id"], str)
            ):
                raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
            fact = RawSignatureFact(
                RawSignatureKind(signature["kind"]),
                signature["verified_media_type"],
                signature["verifier_id"],
            )
            return cls(
                source_ref=value["source_ref"],
                media_kind=RawMediaKind(value["media_kind"]),
                raw_source_sha256=value["raw_source_sha256"],
                byte_size=value["byte_size"],  # type: ignore[arg-type]
                signature=fact,
                duration_ms=value["duration_ms"],  # type: ignore[arg-type]
                selected_frame_count=value["selected_frame_count"],  # type: ignore[arg-type]
                prepared_evidence_sha256=value["prepared_evidence_sha256"],  # type: ignore[arg-type]
                budget_fingerprint=value["budget_fingerprint"],
            )
        except RawSourceError:
            raise
        except (KeyError, TypeError, ValueError):
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None


@dataclass(frozen=True, repr=False)
class RawSourceSnapshot:
    """Transient source copy; intentionally has no serialization method."""

    provenance: RawSourceProvenance
    temporary_path: Path = field(repr=False)
    source_bytes: bytes = field(repr=False, compare=False)
    capture_max_source_bytes: int = field(
        default=RawInputBudget().max_source_bytes, repr=False, compare=False
    )

    def __repr__(self) -> str:
        return "RawSourceSnapshot(<transient>)"

    def cleanup(self) -> None:
        try:
            self.temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def validate_source_ref(source_ref: str) -> str:
    if not isinstance(source_ref, str) or not source_ref or "\\" in source_ref:
        raise RawSourceError(RawSourceErrorCode.SOURCE_REF_INVALID)
    if source_ref.startswith("/") or ":" in source_ref or "//" in source_ref:
        raise RawSourceError(RawSourceErrorCode.SOURCE_REF_INVALID)
    path = PurePosixPath(source_ref)
    if str(path) != source_ref or any(part in {"", ".", ".."} for part in path.parts):
        raise RawSourceError(RawSourceErrorCode.SOURCE_REF_INVALID)
    return source_ref


def sha256_digest(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_signature_for_media(media_kind: RawMediaKind, signature: RawSignatureFact) -> None:
    try:
        kind = RawMediaKind(media_kind)
    except (TypeError, ValueError):
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
    if not isinstance(signature, RawSignatureFact):
        raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)
    if kind is RawMediaKind.TEXT and signature.kind is not RawSignatureKind.UTF8_TEXT:
        raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)
    if kind is RawMediaKind.IMAGE and signature.kind not in {
        RawSignatureKind.PNG, RawSignatureKind.JPEG, RawSignatureKind.GIF, RawSignatureKind.WEBP,
    }:
        raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)
    if kind is RawMediaKind.AUDIO and signature.kind not in {
        RawSignatureKind.RIFF_WAVE, RawSignatureKind.ISO_BMFF, RawSignatureKind.MATROSKA_EBML,
    }:
        raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)
    if kind is RawMediaKind.VIDEO and signature.kind not in {
        RawSignatureKind.ISO_BMFF, RawSignatureKind.MATROSKA_EBML,
    }:
        raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)
    if kind is RawMediaKind.TEXT:
        return
    mime_prefix = {
        RawMediaKind.IMAGE: "image/",
        RawMediaKind.AUDIO: "audio/",
        RawMediaKind.VIDEO: "video/",
    }[kind]
    if not signature.verified_media_type.startswith(mime_prefix):
        raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)


def validate_budget(provenance: RawSourceProvenance, budget: RawInputBudget = RawInputBudget(), *,
                    prepared_text: str | bytes | None = None, whole_resource_tokens: int | None = None) -> None:
    if provenance.byte_size > budget.max_source_bytes:
        raise RawSourceError(RawSourceErrorCode.SOURCE_TOO_LARGE)
    if provenance.duration_ms is not None and provenance.duration_ms > budget.max_duration_ms:
        raise RawSourceError(RawSourceErrorCode.DURATION_LIMIT_EXCEEDED)
    if provenance.selected_frame_count > budget.max_selected_frames:
        raise RawSourceError(RawSourceErrorCode.SELECTED_FRAME_LIMIT_EXCEEDED)
    if prepared_text is not None:
        size = len(prepared_text.encode("utf-8") if isinstance(prepared_text, str) else prepared_text)
        if size > budget.max_prepared_text_bytes:
            raise RawSourceError(RawSourceErrorCode.PREPARED_TEXT_LIMIT_EXCEEDED)
    if whole_resource_tokens is not None and (
        not isinstance(whole_resource_tokens, int) or isinstance(whole_resource_tokens, bool)
        or whole_resource_tokens < 0
    ):
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    if whole_resource_tokens is not None and whole_resource_tokens > budget.max_whole_resource_tokens:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)


def capture_source(source_path: Path, source_ref: str, media_kind: RawMediaKind, signature: RawSignatureFact,
                   budget: RawInputBudget = RawInputBudget(), *, duration_ms: int | None = None,
                   selected_frame_count: int = 0) -> RawSourceSnapshot:
    validate_source_ref(source_ref)
    validate_signature_for_media(media_kind, signature)
    try:
        if not source_path.is_file():
            raise RawSourceError(RawSourceErrorCode.SOURCE_NOT_REGULAR)
        with source_path.open("rb") as source_file:
            source_bytes = source_file.read(budget.max_source_bytes + 1)
    except RawSourceError:
        raise
    except OSError:
        raise RawSourceError(RawSourceErrorCode.SOURCE_UNAVAILABLE) from None
    if not source_bytes:
        raise RawSourceError(RawSourceErrorCode.SOURCE_EMPTY)
    if len(source_bytes) > budget.max_source_bytes:
        raise RawSourceError(RawSourceErrorCode.SOURCE_TOO_LARGE)
    provenance = RawSourceProvenance(
        source_ref, media_kind, sha256_digest(source_bytes), len(source_bytes), signature,
        duration_ms, selected_frame_count, None, budget.fingerprint,
    )
    validate_budget(provenance, budget)
    try:
        handle = tempfile.NamedTemporaryFile(prefix="mdrack-raw-", suffix=".snapshot", delete=False)
        with handle:
            handle.write(source_bytes)
    except OSError:
        raise RawSourceError(RawSourceErrorCode.SOURCE_UNAVAILABLE) from None
    return RawSourceSnapshot(
        provenance, Path(handle.name), source_bytes, budget.max_source_bytes
    )


def check_source_after(snapshot: RawSourceSnapshot, source_path: Path,
                       budget: RawInputBudget | None = None) -> None:
    if budget is not None and budget.max_source_bytes != snapshot.capture_max_source_bytes:
        raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED)
    max_source_bytes = (
        snapshot.capture_max_source_bytes
        if budget is None
        else budget.max_source_bytes
    )
    try:
        if not source_path.is_file():
            raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED)
        with source_path.open("rb") as source_file:
            after = source_file.read(max_source_bytes + 1)
    except RawSourceError:
        raise
    except OSError:
        raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED) from None
    if (
        len(after) > max_source_bytes
        or len(after) != snapshot.provenance.byte_size
        or sha256_digest(after) != snapshot.provenance.raw_source_sha256
    ):
        raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED)


def resource_metadata(provenance: RawSourceProvenance) -> dict[str, object]:
    """Return the only allowlisted metadata shape for a future catalog adapter."""
    return {"provenance": provenance.to_dict()}


__all__ = [
    "RAW_SOURCE_LOCATOR_KIND", "RAW_SOURCE_PROVENANCE_SCHEMA", "RawInputBudget",
    "RawMediaKind", "RawSignatureFact", "RawSignatureKind", "RawSourceError",
    "RawSourceErrorCode", "RawSourceProvenance", "RawSourceSnapshot", "canonical_json",
    "capture_source", "check_source_after", "resource_metadata", "sha256_digest",
    "validate_budget", "validate_signature_for_media", "validate_source_ref",
]
