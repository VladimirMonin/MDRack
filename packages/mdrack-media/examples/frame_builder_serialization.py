from __future__ import annotations

import json

from mdrack_core import Locator
from mdrack_media import (
    REPRESENTATION_FRAME_CAPTION,
    TOKEN_COUNT_ESTIMATED,
    FrameBatchBuilderInput,
    FrameCaptionArtifact,
    FrameCaptionObservation,
    MediaResourceDescriptor,
    NormalizationFingerprint,
    ProducerFingerprint,
    TokenCount,
    TokenCounterFingerprint,
    frame_id,
    representation_id,
    resource_id,
)

producer = ProducerFingerprint.from_payload({"engine": "example", "version": 1})
normalization = NormalizationFingerprint.from_payload({"policy": "preserve", "version": 1})
counter = TokenCounterFingerprint.from_payload({"counter": "example-estimate", "version": 1})
resource_identifier = resource_id("example", "video-001")
representation_identifier = representation_id(
    resource_identifier,
    REPRESENTATION_FRAME_CAPTION,
    producer.value,
    normalization.value,
)
observation_identity = "sample-000"
observation = FrameCaptionObservation(
    frame_id=frame_id(
        resource_identifier,
        producer.value,
        0,
        1_500,
        observation_identity,
    ),
    resource_id=resource_identifier,
    timestamp_ms=1_500,
    observation_identity=observation_identity,
    caption="Example frame caption.",
    ordinal=0,
    token_count=TokenCount(
        count=4,
        kind=TOKEN_COUNT_ESTIMATED,
        counter_fingerprint=counter,
    ),
    producer_fingerprint=producer,
    normalization_fingerprint=normalization,
)
frames = FrameCaptionArtifact(
    resource_id=resource_identifier,
    representation_id=representation_identifier,
    representation_kind=REPRESENTATION_FRAME_CAPTION,
    observations=(observation,),
    producer_fingerprint=producer,
    normalization_fingerprint=normalization,
)
builder_input = FrameBatchBuilderInput(
    resource=MediaResourceDescriptor(
        resource_id=resource_identifier,
        resource_kind="video",
        media_type="video/mp4",
        source_namespace="example",
        locator=Locator(kind="host_ref", payload={"opaque": "video-001"}),
    ),
    frames=frames,
)
serialized = builder_input.to_dict()
assert FrameBatchBuilderInput.from_dict(serialized) == builder_input
print(json.dumps(serialized, ensure_ascii=False, sort_keys=True))
