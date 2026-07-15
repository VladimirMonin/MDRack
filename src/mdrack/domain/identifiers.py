"""Deterministic identifiers for portable MDRack provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

_WHITESPACE = re.compile(r"\s+")


def normalize_heading_path(parts: Iterable[str]) -> tuple[str, ...]:
    """Normalize a heading path without exposing it outside persisted content."""
    return tuple(_WHITESPACE.sub(" ", part).strip().casefold() for part in parts)


def content_fingerprint(content: str) -> str:
    """Return a stable SHA-256 fingerprint for source-derived content."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def logical_id(kind: str, *parts: object) -> str:
    """Build a namespaced deterministic logical identifier."""
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{kind}_{digest[:32]}"


def safe_file_ref(root_id: str, relative_path: str) -> str:
    """Return a non-reversible reference suitable for logs."""
    return logical_id("file", root_id, relative_path)
