"""Caller/host authority only. Catalog locators are never open permissions."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from mdrack.domain.embedding_inputs import ImageInput
from mdrack.ingestion.images import _image_signature_probe
from mdrack.ingestion.raw_source_provenance import (
    RawInputBudget,
    RawMediaKind,
    RawSourceError,
    RawSourceErrorCode,
    RawSourceSnapshot,
    capture_source,
    check_source_after,
    validate_source_ref,
)


class SourceResolver:
    """Host owns durable bindings; this object never learns paths from a DB."""

    def __init__(self, mappings: Mapping[tuple[str, str], Path] | None = None) -> None:
        self._mappings = dict(mappings or {})
        for (namespace, ref), path in self._mappings.items():
            if not namespace or not isinstance(path, Path):
                raise ValueError("invalid_source_authority")
            validate_source_ref(ref)

    def selected_path(self, *, namespace: str, source_ref: str, selected: Path | None = None) -> Path:
        """Resolve only caller/host authority, never a catalog locator."""
        validate_source_ref(source_ref)
        if not namespace:
            raise ValueError("invalid_source_authority")
        path = selected if selected is not None else self._mappings.get((namespace, source_ref))
        if not isinstance(path, Path):
            raise ValueError("source_authority_missing")
        return path

    @contextmanager
    def image(
        self, *, namespace: str, source_ref: str, selected: Path | None = None,
        expected_revision: str | None = None, budget: RawInputBudget = RawInputBudget(),
        byte_range: tuple[int, int] | None = None,
    ) -> Iterator[tuple[Path, RawSourceSnapshot, ImageInput]]:
        validate_source_ref(source_ref)
        if not namespace:
            raise ValueError("invalid_source_authority")
        # Image range/crop preparation is not declared. Refuse, never silently truncate.
        if byte_range is not None:
            raise ValueError("unsupported_source_range")
        path = self.selected_path(namespace=namespace, source_ref=source_ref, selected=selected)
        snapshot = capture_source(path, source_ref, RawMediaKind.IMAGE, budget=budget,
                                  signature_probe=_image_signature_probe)
        try:
            revision = snapshot.provenance.raw_source_sha256
            if expected_revision is not None and expected_revision != revision:
                raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED)
            value = ImageInput(snapshot.source_bytes, snapshot.provenance.signature.verified_media_type, revision)
            yield path, snapshot, value
            check_source_after(snapshot, path, budget)
        finally:
            snapshot.cleanup()
