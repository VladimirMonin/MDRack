"""Immutable transcript, passage, and frame artifact records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from .common import (
    JSONValue,
    expect_keys,
    expect_mapping,
    expect_sequence,
    freeze_metadata,
    plain_json,
    require_int,
    require_probability,
    require_text,
)
from .fingerprints import (
    ContentFingerprint,
    GrouperFingerprint,
    NormalizationFingerprint,
    ProducerFingerprint,
    TokenCounterFingerprint,
)
from .identifiers import (
    ID_ATOM,
    ID_FRAME,
    ID_PASSAGE,
    ID_REPRESENTATION,
    ID_RESOURCE,
    atom_id,
    frame_id,
    representation_id,
    validate_media_id,
)

TOKEN_COUNT_EXACT = "exact"
TOKEN_COUNT_ESTIMATED = "estimated"
TOKEN_COUNT_KINDS = frozenset({TOKEN_COUNT_EXACT, TOKEN_COUNT_ESTIMATED})
REPRESENTATION_AUDIO_TRANSCRIPT = "audio_transcript"
REPRESENTATION_FRAME_CAPTION = "frame_caption"
REPRESENTATION_TIMED_PASSAGE = "timed_passage"


def _empty_metadata() -> dict[str, JSONValue]:
    return {}


@dataclass(frozen=True)
class TokenCount:
    count: int
    kind: str
    counter_fingerprint: TokenCounterFingerprint

    def __post_init__(self) -> None:
        require_int(self.count, "count")
        if self.kind not in TOKEN_COUNT_KINDS:
            raise ValueError("kind must be exact or estimated")
        if not isinstance(self.counter_fingerprint, TokenCounterFingerprint):
            raise ValueError("counter_fingerprint must be a TokenCounterFingerprint")

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "counter_fingerprint": self.counter_fingerprint.value,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, value: object) -> TokenCount:
        data = expect_keys(value, "token count", frozenset({"count", "kind", "counter_fingerprint"}))
        return cls(
            count=cast(int, data["count"]),
            kind=cast(str, data["kind"]),
            counter_fingerprint=TokenCounterFingerprint.from_dict(data["counter_fingerprint"]),
        )


@dataclass(frozen=True)
class TimedTextAtom:
    atom_id: str
    resource_id: str
    start_ms: int
    end_ms: int
    text: str
    ordinal: int
    producer_fingerprint: ProducerFingerprint
    normalization_fingerprint: NormalizationFingerprint
    token_count: TokenCount | None = None
    speaker: str | None = None
    confidence: float | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        validate_media_id(self.resource_id, "resource_id", kind=ID_RESOURCE)
        require_int(self.start_ms, "start_ms")
        require_int(self.end_ms, "end_ms")
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        require_text(self.text, "text")
        require_int(self.ordinal, "ordinal")
        if not isinstance(self.producer_fingerprint, ProducerFingerprint):
            raise ValueError("producer_fingerprint must be a ProducerFingerprint")
        if not isinstance(self.normalization_fingerprint, NormalizationFingerprint):
            raise ValueError("normalization_fingerprint must be a NormalizationFingerprint")
        expected_atom_id = atom_id(
            self.resource_id,
            self.producer_fingerprint.value,
            self.ordinal,
        )
        validate_media_id(self.atom_id, "atom_id", kind=ID_ATOM)
        if self.atom_id != expected_atom_id:
            raise ValueError("atom_id must match resource_id, producer_fingerprint, and ordinal")
        if self.token_count is not None and not isinstance(self.token_count, TokenCount):
            raise ValueError("token_count must be a TokenCount or None")
        if self.speaker is not None:
            require_text(self.speaker, "speaker")
        if self.confidence is not None:
            object.__setattr__(self, "confidence", require_probability(self.confidence, "confidence"))
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "atom_id": self.atom_id,
            "confidence": self.confidence,
            "end_ms": self.end_ms,
            "metadata": plain_json(self.metadata),
            "normalization_fingerprint": self.normalization_fingerprint.value,
            "ordinal": self.ordinal,
            "producer_fingerprint": self.producer_fingerprint.value,
            "resource_id": self.resource_id,
            "speaker": self.speaker,
            "start_ms": self.start_ms,
            "text": self.text,
            "token_count": None if self.token_count is None else self.token_count.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> TimedTextAtom:
        keys = frozenset(
            {
                "atom_id",
                "confidence",
                "end_ms",
                "metadata",
                "normalization_fingerprint",
                "ordinal",
                "producer_fingerprint",
                "resource_id",
                "speaker",
                "start_ms",
                "text",
                "token_count",
            }
        )
        data = expect_keys(value, "timed text atom", keys)
        token = data["token_count"]
        return cls(
            atom_id=cast(str, data["atom_id"]),
            resource_id=cast(str, data["resource_id"]),
            start_ms=cast(int, data["start_ms"]),
            end_ms=cast(int, data["end_ms"]),
            text=cast(str, data["text"]),
            ordinal=cast(int, data["ordinal"]),
            producer_fingerprint=ProducerFingerprint.from_dict(data["producer_fingerprint"]),
            normalization_fingerprint=NormalizationFingerprint.from_dict(
                data["normalization_fingerprint"]
            ),
            token_count=None if token is None else TokenCount.from_dict(token),
            speaker=cast(str | None, data["speaker"]),
            confidence=cast(float | None, data["confidence"]),
            metadata=cast(Mapping[str, JSONValue], expect_mapping(data["metadata"], "metadata")),
        )


@dataclass(frozen=True)
class TimedPassage:
    passage_id: str
    resource_id: str
    representation_id: str
    start_ms: int
    end_ms: int
    text: str
    ordinal: int
    token_count: TokenCount
    source_atom_ids: tuple[str, ...]
    grouper_fingerprint: GrouperFingerprint
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        validate_media_id(self.passage_id, "passage_id", kind=ID_PASSAGE)
        validate_media_id(self.resource_id, "resource_id", kind=ID_RESOURCE)
        validate_media_id(self.representation_id, "representation_id", kind=ID_REPRESENTATION)
        require_int(self.start_ms, "start_ms")
        require_int(self.end_ms, "end_ms")
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        require_text(self.text, "text")
        require_int(self.ordinal, "ordinal")
        if not isinstance(self.token_count, TokenCount):
            raise ValueError("token_count must be a TokenCount")
        if not isinstance(self.source_atom_ids, (list, tuple)) or not self.source_atom_ids:
            raise ValueError("source_atom_ids must be a non-empty sequence")
        atom_ids = tuple(
            validate_media_id(item, "source_atom_ids item", kind=ID_ATOM)
            for item in self.source_atom_ids
        )
        if len(set(atom_ids)) != len(atom_ids):
            raise ValueError("source_atom_ids must be unique")
        object.__setattr__(self, "source_atom_ids", atom_ids)
        if not isinstance(self.grouper_fingerprint, GrouperFingerprint):
            raise ValueError("grouper_fingerprint must be a GrouperFingerprint")
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "end_ms": self.end_ms,
            "grouper_fingerprint": self.grouper_fingerprint.value,
            "metadata": plain_json(self.metadata),
            "ordinal": self.ordinal,
            "passage_id": self.passage_id,
            "representation_id": self.representation_id,
            "resource_id": self.resource_id,
            "source_atom_ids": list(self.source_atom_ids),
            "start_ms": self.start_ms,
            "text": self.text,
            "token_count": self.token_count.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> TimedPassage:
        keys = frozenset(
            {
                "end_ms",
                "grouper_fingerprint",
                "metadata",
                "ordinal",
                "passage_id",
                "representation_id",
                "resource_id",
                "source_atom_ids",
                "start_ms",
                "text",
                "token_count",
            }
        )
        data = expect_keys(value, "timed passage", keys)
        return cls(
            passage_id=cast(str, data["passage_id"]),
            resource_id=cast(str, data["resource_id"]),
            representation_id=cast(str, data["representation_id"]),
            start_ms=cast(int, data["start_ms"]),
            end_ms=cast(int, data["end_ms"]),
            text=cast(str, data["text"]),
            ordinal=cast(int, data["ordinal"]),
            token_count=TokenCount.from_dict(data["token_count"]),
            source_atom_ids=cast(tuple[str, ...], expect_sequence(data["source_atom_ids"], "source_atom_ids")),
            grouper_fingerprint=GrouperFingerprint.from_dict(data["grouper_fingerprint"]),
            metadata=cast(Mapping[str, JSONValue], expect_mapping(data["metadata"], "metadata")),
        )


@dataclass(frozen=True)
class TranscriptArtifact:
    resource_id: str
    representation_id: str
    representation_kind: str
    atoms: tuple[TimedTextAtom, ...]
    producer_fingerprint: ProducerFingerprint
    normalization_fingerprint: NormalizationFingerprint
    language: str | None = None
    duration_ms: int | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        validate_media_id(self.resource_id, "resource_id", kind=ID_RESOURCE)
        validate_media_id(self.representation_id, "representation_id", kind=ID_REPRESENTATION)
        if self.representation_kind != REPRESENTATION_AUDIO_TRANSCRIPT:
            raise ValueError("representation_kind must be audio_transcript")
        if not isinstance(self.producer_fingerprint, ProducerFingerprint):
            raise ValueError("producer_fingerprint must be a ProducerFingerprint")
        if not isinstance(self.normalization_fingerprint, NormalizationFingerprint):
            raise ValueError("normalization_fingerprint must be a NormalizationFingerprint")
        expected_representation_id = representation_id(
            self.resource_id,
            self.representation_kind,
            self.producer_fingerprint.value,
            self.normalization_fingerprint.value,
        )
        if self.representation_id != expected_representation_id:
            raise ValueError(
                "representation_id must match resource_id, representation_kind, "
                "producer_fingerprint, and normalization_fingerprint"
            )
        if not isinstance(self.atoms, (list, tuple)) or any(
            not isinstance(item, TimedTextAtom) for item in self.atoms
        ):
            raise ValueError("atoms must contain only TimedTextAtom values")
        atoms = tuple(self.atoms)
        if any(item.resource_id != self.resource_id for item in atoms):
            raise ValueError("all atoms must belong to resource_id")
        if any(item.producer_fingerprint != self.producer_fingerprint for item in atoms):
            raise ValueError("all atoms must use producer_fingerprint")
        if any(
            item.normalization_fingerprint != self.normalization_fingerprint for item in atoms
        ):
            raise ValueError("all atoms must use normalization_fingerprint")
        if len({item.atom_id for item in atoms}) != len(atoms):
            raise ValueError("atom IDs must be unique")
        if tuple(item.ordinal for item in atoms) != tuple(range(len(atoms))):
            raise ValueError("atom ordinals must be contiguous and match canonical order")
        if any(
            current.start_ms < previous.end_ms
            for previous, current in zip(atoms, atoms[1:], strict=False)
        ):
            raise ValueError("atoms must be ordered and non-overlapping")
        object.__setattr__(self, "atoms", atoms)
        if self.language is not None:
            require_text(self.language, "language")
        if self.duration_ms is not None:
            require_int(self.duration_ms, "duration_ms")
            if atoms and self.duration_ms < atoms[-1].end_ms:
                raise ValueError("duration_ms must include every atom interval")
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "atoms": [item.to_dict() for item in self.atoms],
            "duration_ms": self.duration_ms,
            "language": self.language,
            "metadata": plain_json(self.metadata),
            "normalization_fingerprint": self.normalization_fingerprint.value,
            "producer_fingerprint": self.producer_fingerprint.value,
            "representation_id": self.representation_id,
            "representation_kind": self.representation_kind,
            "resource_id": self.resource_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> TranscriptArtifact:
        keys = frozenset(
            {
                "atoms",
                "duration_ms",
                "language",
                "metadata",
                "normalization_fingerprint",
                "producer_fingerprint",
                "representation_id",
                "representation_kind",
                "resource_id",
            }
        )
        data = expect_keys(value, "transcript artifact", keys)
        return cls(
            resource_id=cast(str, data["resource_id"]),
            representation_id=cast(str, data["representation_id"]),
            representation_kind=cast(str, data["representation_kind"]),
            atoms=tuple(
                TimedTextAtom.from_dict(item)
                for item in expect_sequence(data["atoms"], "atoms")
            ),
            producer_fingerprint=ProducerFingerprint.from_dict(data["producer_fingerprint"]),
            normalization_fingerprint=NormalizationFingerprint.from_dict(
                data["normalization_fingerprint"]
            ),
            language=cast(str | None, data["language"]),
            duration_ms=cast(int | None, data["duration_ms"]),
            metadata=cast(Mapping[str, JSONValue], expect_mapping(data["metadata"], "metadata")),
        )


@dataclass(frozen=True)
class FrameCaptionObservation:
    frame_id: str
    resource_id: str
    timestamp_ms: int
    observation_identity: str
    caption: str
    ordinal: int
    token_count: TokenCount
    producer_fingerprint: ProducerFingerprint
    normalization_fingerprint: NormalizationFingerprint
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_metadata)
    content_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        validate_media_id(self.resource_id, "resource_id", kind=ID_RESOURCE)
        require_int(self.timestamp_ms, "timestamp_ms")
        require_text(self.observation_identity, "observation_identity")
        require_text(self.caption, "caption")
        require_int(self.ordinal, "ordinal")
        if not isinstance(self.token_count, TokenCount):
            raise ValueError("token_count must be a TokenCount")
        if not isinstance(self.producer_fingerprint, ProducerFingerprint):
            raise ValueError("producer_fingerprint must be a ProducerFingerprint")
        if not isinstance(self.normalization_fingerprint, NormalizationFingerprint):
            raise ValueError("normalization_fingerprint must be a NormalizationFingerprint")
        if self.content_fingerprint is None:
            object.__setattr__(
                self,
                "content_fingerprint",
                ContentFingerprint.from_payload({"caption": self.caption}),
            )
        elif not isinstance(self.content_fingerprint, ContentFingerprint):
            raise ValueError("content_fingerprint must be a ContentFingerprint or None")
        expected_frame_id = frame_id(
            self.resource_id,
            self.producer_fingerprint.value,
            self.ordinal,
            self.timestamp_ms,
            self.observation_identity,
        )
        validate_media_id(self.frame_id, "frame_id", kind=ID_FRAME)
        if self.frame_id != expected_frame_id:
            raise ValueError(
                "frame_id must match resource_id, producer_fingerprint, ordinal, "
                "timestamp_ms, and observation_identity"
            )
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        assert self.content_fingerprint is not None
        return {
            "caption": self.caption,
            "content_fingerprint": self.content_fingerprint.value,
            "frame_id": self.frame_id,
            "metadata": plain_json(self.metadata),
            "normalization_fingerprint": self.normalization_fingerprint.value,
            "observation_identity": self.observation_identity,
            "ordinal": self.ordinal,
            "producer_fingerprint": self.producer_fingerprint.value,
            "resource_id": self.resource_id,
            "timestamp_ms": self.timestamp_ms,
            "token_count": self.token_count.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> FrameCaptionObservation:
        keys = frozenset(
            {
                "caption",
                "content_fingerprint",
                "frame_id",
                "metadata",
                "normalization_fingerprint",
                "observation_identity",
                "ordinal",
                "producer_fingerprint",
                "resource_id",
                "timestamp_ms",
                "token_count",
            }
        )
        data = expect_keys(value, "frame caption observation", keys)
        return cls(
            frame_id=cast(str, data["frame_id"]),
            resource_id=cast(str, data["resource_id"]),
            timestamp_ms=cast(int, data["timestamp_ms"]),
            observation_identity=cast(str, data["observation_identity"]),
            caption=cast(str, data["caption"]),
            content_fingerprint=ContentFingerprint.from_dict(data["content_fingerprint"]),
            ordinal=cast(int, data["ordinal"]),
            token_count=TokenCount.from_dict(data["token_count"]),
            producer_fingerprint=ProducerFingerprint.from_dict(data["producer_fingerprint"]),
            normalization_fingerprint=NormalizationFingerprint.from_dict(
                data["normalization_fingerprint"]
            ),
            metadata=cast(Mapping[str, JSONValue], expect_mapping(data["metadata"], "metadata")),
        )


@dataclass(frozen=True)
class FrameCaptionArtifact:
    resource_id: str
    representation_id: str
    representation_kind: str
    observations: tuple[FrameCaptionObservation, ...]
    producer_fingerprint: ProducerFingerprint
    normalization_fingerprint: NormalizationFingerprint
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        validate_media_id(self.resource_id, "resource_id", kind=ID_RESOURCE)
        validate_media_id(self.representation_id, "representation_id", kind=ID_REPRESENTATION)
        if self.representation_kind != REPRESENTATION_FRAME_CAPTION:
            raise ValueError("representation_kind must be frame_caption")
        if not isinstance(self.producer_fingerprint, ProducerFingerprint):
            raise ValueError("producer_fingerprint must be a ProducerFingerprint")
        if not isinstance(self.normalization_fingerprint, NormalizationFingerprint):
            raise ValueError("normalization_fingerprint must be a NormalizationFingerprint")
        expected_representation_id = representation_id(
            self.resource_id,
            self.representation_kind,
            self.producer_fingerprint.value,
            self.normalization_fingerprint.value,
        )
        if self.representation_id != expected_representation_id:
            raise ValueError(
                "representation_id must match resource_id, representation_kind, "
                "producer_fingerprint, and normalization_fingerprint"
            )
        if not isinstance(self.observations, (list, tuple)) or any(
            not isinstance(item, FrameCaptionObservation) for item in self.observations
        ):
            raise ValueError("observations must contain only FrameCaptionObservation values")
        observations = tuple(self.observations)
        if any(item.resource_id != self.resource_id for item in observations):
            raise ValueError("all observations must belong to resource_id")
        if any(item.producer_fingerprint != self.producer_fingerprint for item in observations):
            raise ValueError("all observations must use producer_fingerprint")
        if any(
            item.normalization_fingerprint != self.normalization_fingerprint
            for item in observations
        ):
            raise ValueError("all observations must use normalization_fingerprint")
        if len({item.frame_id for item in observations}) != len(observations):
            raise ValueError("frame IDs must be unique")
        if tuple(item.ordinal for item in observations) != tuple(range(len(observations))):
            raise ValueError("frame ordinals must be contiguous and match canonical order")
        if any(
            current.timestamp_ms < previous.timestamp_ms
            for previous, current in zip(observations, observations[1:], strict=False)
        ):
            raise ValueError("frames must be ordered by nondecreasing timestamp_ms")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "metadata": plain_json(self.metadata),
            "normalization_fingerprint": self.normalization_fingerprint.value,
            "observations": [item.to_dict() for item in self.observations],
            "producer_fingerprint": self.producer_fingerprint.value,
            "representation_id": self.representation_id,
            "representation_kind": self.representation_kind,
            "resource_id": self.resource_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> FrameCaptionArtifact:
        keys = frozenset(
            {
                "metadata",
                "normalization_fingerprint",
                "observations",
                "producer_fingerprint",
                "representation_id",
                "representation_kind",
                "resource_id",
            }
        )
        data = expect_keys(value, "frame caption artifact", keys)
        return cls(
            resource_id=cast(str, data["resource_id"]),
            representation_id=cast(str, data["representation_id"]),
            representation_kind=cast(str, data["representation_kind"]),
            observations=tuple(
                FrameCaptionObservation.from_dict(item)
                for item in expect_sequence(data["observations"], "observations")
            ),
            producer_fingerprint=ProducerFingerprint.from_dict(data["producer_fingerprint"]),
            normalization_fingerprint=NormalizationFingerprint.from_dict(
                data["normalization_fingerprint"]
            ),
            metadata=cast(Mapping[str, JSONValue], expect_mapping(data["metadata"], "metadata")),
        )
