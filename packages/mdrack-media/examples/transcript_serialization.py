from __future__ import annotations

import json

from mdrack_core import Locator
from mdrack_media import (
    REPRESENTATION_AUDIO_TRANSCRIPT,
    REPRESENTATION_TIMED_PASSAGE,
    GrouperFingerprint,
    MediaResourceDescriptor,
    NormalizationFingerprint,
    ProducerFingerprint,
    TimedChunkingPolicy,
    TimedTextAtom,
    TranscriptArtifact,
    TranscriptBatchBuilderInput,
    atom_id,
    representation_id,
    resource_id,
)

producer = ProducerFingerprint.from_payload({"engine": "example", "version": 1})
normalization = NormalizationFingerprint.from_payload({"policy": "preserve", "version": 1})
grouper = GrouperFingerprint.from_payload({"policy": "timed-window", "version": 1})
resource_identifier = resource_id("example", "audio-001")
transcript_representation = representation_id(
    resource_identifier,
    REPRESENTATION_AUDIO_TRANSCRIPT,
    producer.value,
    normalization.value,
)
atom = TimedTextAtom(
    atom_id=atom_id(resource_identifier, producer.value, 0),
    resource_id=resource_identifier,
    start_ms=0,
    end_ms=1_000,
    text="Example transcript.",
    ordinal=0,
    producer_fingerprint=producer,
    normalization_fingerprint=normalization,
)
transcript = TranscriptArtifact(
    resource_id=resource_identifier,
    representation_id=transcript_representation,
    representation_kind=REPRESENTATION_AUDIO_TRANSCRIPT,
    atoms=(atom,),
    producer_fingerprint=producer,
    normalization_fingerprint=normalization,
    duration_ms=1_000,
)
passage_representation = representation_id(
    resource_identifier,
    REPRESENTATION_TIMED_PASSAGE,
    grouper.value,
    normalization.value,
)
builder_input = TranscriptBatchBuilderInput(
    resource=MediaResourceDescriptor(
        resource_id=resource_identifier,
        resource_kind="audio",
        media_type="audio/wav",
        source_namespace="example",
        locator=Locator(kind="host_ref", payload={"opaque": "audio-001"}),
    ),
    transcript=transcript,
    passage_representation_id=passage_representation,
    passage_representation_kind=REPRESENTATION_TIMED_PASSAGE,
    chunking_policy=TimedChunkingPolicy(),
    grouper_fingerprint=grouper,
)
serialized = builder_input.to_dict()
assert TranscriptBatchBuilderInput.from_dict(serialized) == builder_input
print(json.dumps(serialized, ensure_ascii=False, sort_keys=True))
