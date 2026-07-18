"""Resource, representation, unit, locator, and facet records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .common import (
    JSONValue,
    freeze_json_mapping,
    require_finite_number,
    require_integer,
    require_non_empty,
    require_optional_non_empty,
    require_utf8_encodable,
)

RESOURCE_DOCUMENT = "document"
RESOURCE_IMAGE = "image"
RESOURCE_AUDIO = "audio"
RESOURCE_VIDEO = "video"

MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"

REPRESENTATION_RETRIEVAL_TEXT = "retrieval_text"
REPRESENTATION_OCR_TEXT = "ocr_text"
REPRESENTATION_CAPTION_TEXT = "caption_text"
REPRESENTATION_VISUAL = "visual"
REPRESENTATION_TRANSCRIPT_TEXT = "transcript_text"
REPRESENTATION_AUDIO_TRANSCRIPT = "audio_transcript"
REPRESENTATION_FRAME_CAPTION = "frame_caption"

UNIT_TEXT_CHUNK = "text_chunk"
UNIT_WHOLE_RESOURCE = "whole_resource"
UNIT_PAGE = "page"
UNIT_REGION = "region"
UNIT_FRAME = "frame"
UNIT_TIME_SEGMENT = "time_segment"

TOKEN_COUNT_EXACT = "exact"
TOKEN_COUNT_ESTIMATED = "estimated"
TOKEN_COUNT_KINDS = frozenset({TOKEN_COUNT_EXACT, TOKEN_COUNT_ESTIMATED})

FACET_ORIGIN_USER = "user"
FACET_ORIGIN_SOURCE = "source"
FACET_ORIGIN_EXTRACTOR = "extractor"
FACET_ORIGIN_CLASSIFIER = "classifier"


def _empty_mapping() -> dict[str, JSONValue]:
    return {}


def _validate_token_count(
    token_count: object,
    token_count_kind: object,
) -> tuple[int | None, str | None]:
    if token_count is None and token_count_kind is None:
        return None, None
    if token_count is None or token_count_kind is None:
        raise ValueError("token_count and token_count_kind must be supplied together")
    count = require_integer(token_count, "token_count", minimum=0)
    kind = require_non_empty(token_count_kind, "token_count_kind")
    if kind not in TOKEN_COUNT_KINDS:
        raise ValueError("token_count_kind must be exact or estimated")
    return count, kind


@dataclass(frozen=True)
class Locator:
    kind: str
    payload: Mapping[str, JSONValue] = field(default_factory=_empty_mapping)

    def __post_init__(self) -> None:
        require_non_empty(self.kind, "kind")
        object.__setattr__(self, "payload", freeze_json_mapping(self.payload, "payload"))


@dataclass(frozen=True)
class ResourceRecord:
    resource_id: str
    resource_kind: str
    media_type: str
    source_namespace: str
    locator: Locator
    content_hash: str | None = None
    title: str | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_mapping)

    def __post_init__(self) -> None:
        require_non_empty(self.resource_id, "resource_id")
        require_non_empty(self.resource_kind, "resource_kind")
        require_non_empty(self.media_type, "media_type")
        require_non_empty(self.source_namespace, "source_namespace")
        if not isinstance(self.locator, Locator):
            raise ValueError("locator must be a Locator")
        require_optional_non_empty(self.content_hash, "content_hash")
        if self.title is not None:
            require_utf8_encodable(self.title, "title")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata, "metadata"))


@dataclass(frozen=True)
class RepresentationRecord:
    representation_id: str
    resource_id: str
    representation_kind: str
    modality: str
    text: str | None = None
    language: str | None = None
    producer_fingerprint: str | None = None
    token_count: int | None = None
    token_count_kind: str | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_mapping)

    def __post_init__(self) -> None:
        require_non_empty(self.representation_id, "representation_id")
        require_non_empty(self.resource_id, "resource_id")
        require_non_empty(self.representation_kind, "representation_kind")
        require_non_empty(self.modality, "modality")
        if self.text is not None:
            require_utf8_encodable(self.text, "text")
        require_optional_non_empty(self.language, "language")
        require_optional_non_empty(self.producer_fingerprint, "producer_fingerprint")
        count, kind = _validate_token_count(self.token_count, self.token_count_kind)
        object.__setattr__(self, "token_count", count)
        object.__setattr__(self, "token_count_kind", kind)
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata, "metadata"))


@dataclass(frozen=True)
class SearchUnitRecord:
    unit_id: str
    resource_id: str
    representation_id: str
    unit_kind: str
    modality: str
    text: str | None
    evidence_locator: Locator
    ordinal: int
    token_count: int | None = None
    token_count_kind: str | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_mapping)

    def __post_init__(self) -> None:
        require_non_empty(self.unit_id, "unit_id")
        require_non_empty(self.resource_id, "resource_id")
        require_non_empty(self.representation_id, "representation_id")
        require_non_empty(self.unit_kind, "unit_kind")
        require_non_empty(self.modality, "modality")
        if self.text is not None:
            require_utf8_encodable(self.text, "text")
        if not isinstance(self.evidence_locator, Locator):
            raise ValueError("evidence_locator must be a Locator")
        require_integer(self.ordinal, "ordinal", minimum=0)
        count, kind = _validate_token_count(self.token_count, self.token_count_kind)
        object.__setattr__(self, "token_count", count)
        object.__setattr__(self, "token_count_kind", kind)
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata, "metadata"))


@dataclass(frozen=True)
class Facet:
    namespace: str
    value: str

    def __post_init__(self) -> None:
        require_non_empty(self.namespace, "namespace")
        require_non_empty(self.value, "value")


@dataclass(frozen=True)
class ResourceFacet:
    resource_id: str
    facet: Facet
    origin: str
    producer_fingerprint: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.resource_id, "resource_id")
        if not isinstance(self.facet, Facet):
            raise ValueError("facet must be a Facet")
        require_non_empty(self.origin, "origin")
        require_optional_non_empty(self.producer_fingerprint, "producer_fingerprint")
        if self.confidence is not None:
            confidence = require_finite_number(self.confidence, "confidence")
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence must be between 0 and 1")
            object.__setattr__(self, "confidence", confidence)
