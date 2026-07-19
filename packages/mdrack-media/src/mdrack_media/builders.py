"""Validated inputs for future provider-free media batch builders."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from mdrack_core import Locator

from .common import JSONValue, expect_keys, expect_mapping, plain_json
from .fingerprints import AggregationFingerprint, EmbeddingFingerprint, GrouperFingerprint
from .identifiers import ID_REPRESENTATION, ID_RESOURCE, representation_id, validate_media_id
from .policies import TimedChunkingPolicy, WholeResourceTextPolicy
from .records import (
    REPRESENTATION_TIMED_PASSAGE,
    FrameCaptionArtifact,
    TranscriptArtifact,
)

RESOURCE_AUDIO = "audio"
RESOURCE_VIDEO = "video"
MEDIA_RESOURCE_KINDS = frozenset({RESOURCE_AUDIO, RESOURCE_VIDEO})


@dataclass(frozen=True)
class MediaResourceDescriptor:
    resource_id: str
    resource_kind: str
    media_type: str
    source_namespace: str
    locator: Locator

    def __post_init__(self) -> None:
        validate_media_id(self.resource_id, "resource_id", kind=ID_RESOURCE)
        if self.resource_kind not in MEDIA_RESOURCE_KINDS:
            raise ValueError("resource_kind must be audio or video")
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type must be non-empty")
        if not isinstance(self.source_namespace, str) or not self.source_namespace.strip():
            raise ValueError("source_namespace must be non-empty")
        if not isinstance(self.locator, Locator):
            raise ValueError("locator must be a core Locator")

    def to_dict(self) -> dict[str, object]:
        return {
            "locator": {"kind": self.locator.kind, "payload": plain_json(self.locator.payload)},
            "media_type": self.media_type,
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "source_namespace": self.source_namespace,
        }

    @classmethod
    def from_dict(cls, value: object) -> MediaResourceDescriptor:
        data = expect_keys(
            value,
            "media resource descriptor",
            frozenset({"locator", "media_type", "resource_id", "resource_kind", "source_namespace"}),
        )
        locator = expect_keys(data["locator"], "locator", frozenset({"kind", "payload"}))
        return cls(
            resource_id=cast(str, data["resource_id"]),
            resource_kind=cast(str, data["resource_kind"]),
            media_type=cast(str, data["media_type"]),
            source_namespace=cast(str, data["source_namespace"]),
            locator=Locator(
                kind=cast(str, locator["kind"]),
                payload=cast(
                    Mapping[str, JSONValue],
                    expect_mapping(locator["payload"], "locator payload"),
                ),
            ),
        )


@dataclass(frozen=True)
class TranscriptBatchBuilderInput:
    resource: MediaResourceDescriptor
    transcript: TranscriptArtifact
    passage_representation_id: str
    passage_representation_kind: str
    chunking_policy: TimedChunkingPolicy
    grouper_fingerprint: GrouperFingerprint
    embedding_fingerprint: EmbeddingFingerprint | None = None
    whole_text_policy: WholeResourceTextPolicy | None = None
    aggregation_fingerprint: AggregationFingerprint | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource, MediaResourceDescriptor):
            raise ValueError("resource must be a MediaResourceDescriptor")
        if not isinstance(self.transcript, TranscriptArtifact):
            raise ValueError("transcript must be a TranscriptArtifact")
        if self.transcript.resource_id != self.resource.resource_id:
            raise ValueError("transcript must belong to resource")
        validate_media_id(
            self.passage_representation_id,
            "passage_representation_id",
            kind=ID_REPRESENTATION,
        )
        if self.passage_representation_kind != REPRESENTATION_TIMED_PASSAGE:
            raise ValueError("passage_representation_kind must be timed_passage")
        if not isinstance(self.chunking_policy, TimedChunkingPolicy):
            raise ValueError("chunking_policy must be a TimedChunkingPolicy")
        if not isinstance(self.grouper_fingerprint, GrouperFingerprint):
            raise ValueError("grouper_fingerprint must be a GrouperFingerprint")
        expected_passage_representation_id = representation_id(
            self.resource.resource_id,
            self.passage_representation_kind,
            self.grouper_fingerprint.value,
            self.transcript.normalization_fingerprint.value,
        )
        if self.passage_representation_id != expected_passage_representation_id:
            raise ValueError(
                "passage_representation_id must match resource, representation kind, "
                "grouper fingerprint, and transcript normalization fingerprint"
            )
        if self.embedding_fingerprint is not None and not isinstance(self.embedding_fingerprint, EmbeddingFingerprint):
            raise ValueError("embedding_fingerprint must be an EmbeddingFingerprint or None")
        if self.whole_text_policy is not None and not isinstance(self.whole_text_policy, WholeResourceTextPolicy):
            raise ValueError("whole_text_policy must be a WholeResourceTextPolicy or None")
        if (self.whole_text_policy is None) != (self.aggregation_fingerprint is None):
            raise ValueError("whole_text_policy and aggregation_fingerprint must be supplied together")
        if self.aggregation_fingerprint is not None and not isinstance(
            self.aggregation_fingerprint, AggregationFingerprint
        ):
            raise ValueError("aggregation_fingerprint must be an AggregationFingerprint or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "aggregation_fingerprint": (
                None if self.aggregation_fingerprint is None else self.aggregation_fingerprint.value
            ),
            "chunking_policy": self.chunking_policy.to_dict(),
            "embedding_fingerprint": (None if self.embedding_fingerprint is None else self.embedding_fingerprint.value),
            "grouper_fingerprint": self.grouper_fingerprint.value,
            "passage_representation_id": self.passage_representation_id,
            "passage_representation_kind": self.passage_representation_kind,
            "resource": self.resource.to_dict(),
            "transcript": self.transcript.to_dict(),
            "whole_text_policy": (None if self.whole_text_policy is None else self.whole_text_policy.to_dict()),
        }

    @classmethod
    def from_dict(cls, value: object) -> TranscriptBatchBuilderInput:
        keys = frozenset(
            {
                "aggregation_fingerprint",
                "chunking_policy",
                "embedding_fingerprint",
                "grouper_fingerprint",
                "passage_representation_id",
                "passage_representation_kind",
                "resource",
                "transcript",
                "whole_text_policy",
            }
        )
        data = expect_keys(value, "transcript builder input", keys)
        embedding = data["embedding_fingerprint"]
        aggregation = data["aggregation_fingerprint"]
        whole_policy = data["whole_text_policy"]
        return cls(
            resource=MediaResourceDescriptor.from_dict(data["resource"]),
            transcript=TranscriptArtifact.from_dict(data["transcript"]),
            passage_representation_id=cast(str, data["passage_representation_id"]),
            passage_representation_kind=cast(str, data["passage_representation_kind"]),
            chunking_policy=TimedChunkingPolicy.from_dict(data["chunking_policy"]),
            grouper_fingerprint=GrouperFingerprint.from_dict(data["grouper_fingerprint"]),
            embedding_fingerprint=(None if embedding is None else EmbeddingFingerprint.from_dict(embedding)),
            whole_text_policy=(None if whole_policy is None else WholeResourceTextPolicy.from_dict(whole_policy)),
            aggregation_fingerprint=(None if aggregation is None else AggregationFingerprint.from_dict(aggregation)),
        )


@dataclass(frozen=True)
class FrameBatchBuilderInput:
    resource: MediaResourceDescriptor
    frames: FrameCaptionArtifact
    embedding_fingerprint: EmbeddingFingerprint | None = None
    whole_text_policy: WholeResourceTextPolicy | None = None
    aggregation_fingerprint: AggregationFingerprint | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource, MediaResourceDescriptor):
            raise ValueError("resource must be a MediaResourceDescriptor")
        if self.resource.resource_kind != RESOURCE_VIDEO:
            raise ValueError("frame batches require a video resource")
        if not isinstance(self.frames, FrameCaptionArtifact):
            raise ValueError("frames must be a FrameCaptionArtifact")
        if self.frames.resource_id != self.resource.resource_id:
            raise ValueError("frames must belong to resource")
        if self.embedding_fingerprint is not None and not isinstance(self.embedding_fingerprint, EmbeddingFingerprint):
            raise ValueError("embedding_fingerprint must be an EmbeddingFingerprint or None")
        if self.whole_text_policy is not None and not isinstance(self.whole_text_policy, WholeResourceTextPolicy):
            raise ValueError("whole_text_policy must be a WholeResourceTextPolicy or None")
        if (self.whole_text_policy is None) != (self.aggregation_fingerprint is None):
            raise ValueError("whole_text_policy and aggregation_fingerprint must be supplied together")
        if self.aggregation_fingerprint is not None and not isinstance(
            self.aggregation_fingerprint, AggregationFingerprint
        ):
            raise ValueError("aggregation_fingerprint must be an AggregationFingerprint or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "aggregation_fingerprint": (
                None if self.aggregation_fingerprint is None else self.aggregation_fingerprint.value
            ),
            "embedding_fingerprint": (None if self.embedding_fingerprint is None else self.embedding_fingerprint.value),
            "frames": self.frames.to_dict(),
            "resource": self.resource.to_dict(),
            "whole_text_policy": (None if self.whole_text_policy is None else self.whole_text_policy.to_dict()),
        }

    @classmethod
    def from_dict(cls, value: object) -> FrameBatchBuilderInput:
        data = expect_mapping(value, "frame builder input")
        allowed = {"embedding_fingerprint", "frames", "resource", "aggregation_fingerprint", "whole_text_policy"}
        if set(data) not in (allowed - {"aggregation_fingerprint", "whole_text_policy"}, allowed):
            raise ValueError("frame builder input has unsupported or incomplete keys")
        embedding = data["embedding_fingerprint"]
        aggregation = data.get("aggregation_fingerprint")
        whole_policy = data.get("whole_text_policy")
        return cls(
            resource=MediaResourceDescriptor.from_dict(data["resource"]),
            frames=FrameCaptionArtifact.from_dict(data["frames"]),
            embedding_fingerprint=(None if embedding is None else EmbeddingFingerprint.from_dict(embedding)),
            whole_text_policy=(None if whole_policy is None else WholeResourceTextPolicy.from_dict(whole_policy)),
            aggregation_fingerprint=(None if aggregation is None else AggregationFingerprint.from_dict(aggregation)),
        )
