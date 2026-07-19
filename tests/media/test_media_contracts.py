from __future__ import annotations

import json
import random
from dataclasses import replace
from typing import Callable

import pytest

from mdrack_core import Locator
from mdrack_media import (
    MEDIA_CONTRACT_VERSION,
    TOKEN_COUNT_ESTIMATED,
    TOKEN_COUNT_EXACT,
    AggregationFingerprint,
    EmbeddingFingerprint,
    FrameBatchBuilderInput,
    FrameCaptionArtifact,
    FrameCaptionObservation,
    GrouperFingerprint,
    MediaEvent,
    MediaEventStatus,
    MediaOperation,
    MediaResourceDescriptor,
    MediaResourceKind,
    NormalizationFingerprint,
    ProducerFingerprint,
    TextNormalizationPolicy,
    TimedChunkingPolicy,
    TimedPassage,
    TimedTextAtom,
    TimeSegmentLocator,
    TokenCount,
    TokenCounter,
    TokenCounterFingerprint,
    TranscriptArtifact,
    TranscriptBatchBuilderInput,
    VideoFrameLocator,
    WholeMediaLocator,
    WholeResourceTextPolicy,
    atom_id,
    canonical_json,
    frame_id,
    passage_id,
    representation_id,
    resource_id,
    stable_media_id,
    validate_media_id,
    whole_resource_id,
)

PRODUCER = ProducerFingerprint.from_payload({"engine": "fixture", "version": 1})
NORMALIZATION = NormalizationFingerprint.from_payload(
    {"whitespace": "preserve", "version": 1}
)
GROUPER = GrouperFingerprint.from_payload({"algorithm": "timed-window", "version": 1})
COUNTER = TokenCounterFingerprint.from_payload({"counter": "fixture-exact", "version": 1})
AGGREGATION = AggregationFingerprint.from_payload({"algorithm": "full-text", "version": 1})
EMBEDDING = EmbeddingFingerprint.from_payload({"dimensions": 3, "space": "fixture-text"})
AUDIO_ID = resource_id("fixture", "private/source.wav")
VIDEO_ID = resource_id("fixture", "private/source.mp4")
AUDIO_REPRESENTATION_ID = representation_id(
    AUDIO_ID,
    "audio_transcript",
    PRODUCER.value,
    NORMALIZATION.value,
)
VIDEO_REPRESENTATION_ID = representation_id(
    VIDEO_ID,
    "frame_caption",
    PRODUCER.value,
    NORMALIZATION.value,
)
PASSAGE_REPRESENTATION_ID = representation_id(
    AUDIO_ID,
    "timed_passage",
    GROUPER.value,
    NORMALIZATION.value,
)


def _token_count(kind: str = TOKEN_COUNT_EXACT) -> TokenCount:
    return TokenCount(count=4, kind=kind, counter_fingerprint=COUNTER)


def _atom(ordinal: int = 0) -> TimedTextAtom:
    return TimedTextAtom(
        atom_id=atom_id(AUDIO_ID, PRODUCER.value, ordinal),
        resource_id=AUDIO_ID,
        start_ms=ordinal * 1_000,
        end_ms=(ordinal + 1) * 1_000,
        text=f"public transcript {ordinal}",
        ordinal=ordinal,
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
        token_count=_token_count(),
        metadata={"chapter": ordinal},
    )


def _transcript() -> TranscriptArtifact:
    return TranscriptArtifact(
        resource_id=AUDIO_ID,
        representation_id=AUDIO_REPRESENTATION_ID,
        representation_kind="audio_transcript",
        atoms=(_atom(0), _atom(1)),
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
        language="en",
        duration_ms=2_000,
        metadata={"source": "fixture"},
    )


def _frame() -> FrameCaptionObservation:
    return FrameCaptionObservation(
        frame_id=frame_id(VIDEO_ID, PRODUCER.value, 0, 1_500, "observation-1"),
        resource_id=VIDEO_ID,
        timestamp_ms=1_500,
        observation_identity="observation-1",
        caption="public frame caption",
        ordinal=0,
        token_count=_token_count(TOKEN_COUNT_ESTIMATED),
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
    )


def test_contract_identity_fingerprints_and_ids_are_frozen() -> None:
    assert MEDIA_CONTRACT_VERSION == "1.0.0-rc.1"
    assert PRODUCER.value == "sha256:5647ad5a23b9c1310dfeb6119e2abbb13f35f43d3c18fba5299e038cc380330e"
    assert NORMALIZATION.value == "sha256:8c9972d05a3e7e42f18b7af0ada27216371962f94e4a68e2c38bf371c6650832"
    assert GROUPER.value == "sha256:47f53915977ac263640f75b84e7e5a13df2dcf9069f0445df4dbb882a8d79099"
    assert COUNTER.value == "sha256:3b8b4196839acb96de853b59c8cd7ddedf0983abc50afce91fa8c70a8a46b5b5"
    assert AGGREGATION.value == "sha256:322539b70f47c0888c34629130aec6acaa73f5b57a6b6d7eda3b3a964f7ad4dd"
    assert EMBEDDING.value == "sha256:b4d1c64a3082cda7ce99638affe8e34557e81eecbccc6a3e9c906f2e06b7fa27"
    assert AUDIO_ID == "resource_54cc277c789235aa73804a84296b6588a09b8f0c69c36c0d468a3bdfbeaec35c"
    assert AUDIO_REPRESENTATION_ID == (
        "representation_7885d96aa78ef8ca9efc15da5e4934aaad1db7392c339a1b5cf23cb2008c915c"
    )
    assert atom_id(AUDIO_ID, PRODUCER.value, 0) == (
        "atom_0b6c53e69d014c7ce4c431693fb301a77a1aee832b3cb8019f1928273e439c9c"
    )
    assert "private" not in AUDIO_ID
    assert ProducerFingerprint(PRODUCER.value) != NormalizationFingerprint(PRODUCER.value)


def test_identifier_kinds_are_not_interchangeable() -> None:
    validate_media_id(AUDIO_ID, "resource_id", kind="resource")
    with pytest.raises(ValueError, match="canonical passage ID"):
        validate_media_id(AUDIO_ID, "passage_id", kind="passage")
    with pytest.raises(ValueError, match="source_namespace must be non-empty"):
        resource_id("", "source")


@pytest.mark.parametrize(
    "payload",
    [
        {1: "value"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": object()},
        {"value": "\ud800"},
    ],
)
def test_canonical_identity_rejects_values_outside_json_grammar(payload: object) -> None:
    with pytest.raises(ValueError):
        canonical_json(payload)
    with pytest.raises(ValueError):
        ProducerFingerprint.from_payload(payload)
    with pytest.raises(ValueError):
        stable_media_id("resource", [payload])


def _direct_list_cycle() -> object:
    value: list[object] = []
    value.append(value)
    return value


def _direct_mapping_cycle() -> object:
    value: dict[str, object] = {}
    value["child"] = value
    return value


def _mutual_list_cycle() -> object:
    first: list[object] = []
    second: list[object] = [first]
    first.append(second)
    return first


def _mutual_mapping_cycle() -> object:
    first: dict[str, object] = {}
    second: dict[str, object] = {"first": first}
    first["second"] = second
    return first


def _mixed_container_cycle() -> object:
    sequence: list[object] = []
    mapping: dict[str, object] = {"sequence": sequence}
    sequence.append(mapping)
    return mapping


@pytest.mark.parametrize(
    "cycle_factory",
    [
        _direct_list_cycle,
        _direct_mapping_cycle,
        _mutual_list_cycle,
        _mutual_mapping_cycle,
        _mixed_container_cycle,
    ],
    ids=["direct-list", "direct-mapping", "mutual-list", "mutual-mapping", "mixed"],
)
def test_canonical_identity_rejects_cyclic_containers_privately(
    cycle_factory: Callable[[], object],
) -> None:
    sentinel = "/private/cyclic-sentinel.wav"
    cycle = cycle_factory()
    expected = "JSON containers must be acyclic and at most 64 levels deep"

    operations = (
        lambda: canonical_json({sentinel: cycle}),
        lambda: ProducerFingerprint.from_payload({sentinel: cycle}),
        lambda: stable_media_id("resource", [{sentinel: cycle}]),
    )
    for operation in operations:
        with pytest.raises(ValueError, match=f"^{expected}$") as captured:
            operation()
        assert sentinel not in str(captured.value)


def _nested_lists(depth: int) -> object:
    value: object = "leaf"
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    ("operation", "payload_depth_offset"),
    [
        (canonical_json, 0),
        (lambda value: ProducerFingerprint.from_payload({"nested": value}), 1),
        (lambda value: stable_media_id("resource", [value]), 2),
    ],
    ids=["canonical-json", "fingerprint", "stable-media-id"],
)
def test_canonical_identity_enforces_deterministic_container_depth(
    operation: Callable[[object], object],
    payload_depth_offset: int,
) -> None:
    expected = "JSON containers must be acyclic and at most 64 levels deep"

    operation(_nested_lists(63 - payload_depth_offset))
    operation(_nested_lists(64 - payload_depth_offset))
    with pytest.raises(ValueError, match=f"^{expected}$"):
        operation(_nested_lists(65 - payload_depth_offset))


def test_canonical_identity_allows_reused_acyclic_containers() -> None:
    shared = {"value": [1, 2, 3]}
    payload = {"first": shared, "second": shared}

    assert canonical_json(payload) == (
        '{"first":{"value":[1,2,3]},"second":{"value":[1,2,3]}}'
    )


def test_canonical_identity_is_stable_and_collision_safe_for_valid_json_objects() -> None:
    rng = random.Random(731)
    payloads: list[dict[str, object]] = []
    for ordinal in range(200):
        keys = ["contract", "nested", "ordinal"]
        rng.shuffle(keys)
        values = {
            "contract": "collision-probe-v1",
            "nested": {"enabled": ordinal % 2 == 0, "values": [ordinal, ordinal + 1]},
            "ordinal": ordinal,
        }
        payloads.append({key: values[key] for key in keys})

    canonical = [canonical_json(payload) for payload in payloads]
    fingerprints = [ProducerFingerprint.from_payload(payload).value for payload in payloads]
    identifiers = [stable_media_id("resource", [payload]) for payload in payloads]

    assert len(set(canonical)) == len(payloads)
    assert len(set(fingerprints)) == len(payloads)
    assert len(set(identifiers)) == len(payloads)
    assert canonical_json({"b": 2, "a": {"y": 2, "x": 1}}) == canonical_json(
        {"a": {"x": 1, "y": 2}, "b": 2}
    )
    with pytest.raises(ValueError, match="JSON object"):
        ProducerFingerprint.from_payload(["not", "an", "object"])


def test_canonical_identity_matches_independent_acyclic_json_oracle() -> None:
    rng = random.Random(4_731)

    def generate(depth: int = 0) -> object:
        scalar_factories: tuple[Callable[[], object], ...] = (
            lambda: None,
            lambda: rng.choice((False, True)),
            lambda: rng.randint(-1_000_000, 1_000_000),
            lambda: rng.uniform(-1_000.0, 1_000.0),
            lambda: rng.choice(("", "plain", "кириллица", "line\nfeed", "🧂")),
        )
        if depth >= 8 or rng.random() < 0.45:
            return rng.choice(scalar_factories)()
        if rng.random() < 0.5:
            return [generate(depth + 1) for _ in range(rng.randrange(5))]
        keys = rng.sample(["a", "b", "nested", "ключ", "emoji🧂"], rng.randrange(6))
        return {key: generate(depth + 1) for key in keys}

    canonical_to_oracle: dict[str, str] = {}
    for _ in range(1_000):
        payload = generate()
        canonical = canonical_json(payload)
        oracle = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert json.loads(canonical) == payload
        if canonical in canonical_to_oracle:
            assert canonical_to_oracle[canonical] == oracle
        else:
            canonical_to_oracle[canonical] = oracle


def test_canonical_identity_errors_do_not_expose_payload_keys_or_values() -> None:
    sentinel = "/private/sentinel.wav"
    with pytest.raises(ValueError) as captured:
        ProducerFingerprint.from_payload({sentinel: object()})
    assert sentinel not in str(captured.value)


def test_integer_millisecond_locators_round_trip_to_core() -> None:
    segment = TimeSegmentLocator(start_ms=1_000, end_ms=2_000, track="audio")
    assert TimeSegmentLocator.from_dict(segment.to_dict()) == segment
    assert segment.to_core_locator() == Locator(
        kind="time_segment",
        payload={"end_ms": 2_000, "start_ms": 1_000, "track": "audio"},
    )
    frame = VideoFrameLocator(timestamp_ms=1_500, frame_id=_frame().frame_id)
    assert VideoFrameLocator.from_dict(frame.to_dict()) == frame
    assert WholeMediaLocator.from_dict({}).to_core_locator() == Locator(
        kind="whole_media", payload={}
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TimeSegmentLocator(start_ms=-1, end_ms=1),
        lambda: TimeSegmentLocator(start_ms=1, end_ms=1),
        lambda: VideoFrameLocator(timestamp_ms=-1, frame_id=_frame().frame_id),
    ],
)
def test_locators_reject_invalid_times(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_token_counts_require_kind_and_counter_identity() -> None:
    assert TokenCount.from_dict(_token_count().to_dict()) == _token_count()
    with pytest.raises(ValueError, match="exact or estimated"):
        TokenCount(count=1, kind="approximate", counter_fingerprint=COUNTER)
    with pytest.raises(ValueError, match="TokenCounterFingerprint"):
        TokenCount(count=1, kind=TOKEN_COUNT_EXACT, counter_fingerprint=PRODUCER)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="greater than or equal"):
        TokenCount(count=-1, kind=TOKEN_COUNT_EXACT, counter_fingerprint=COUNTER)


def test_token_counter_protocol_requires_count_and_fingerprint() -> None:
    class FixtureCounter:
        fingerprint = COUNTER

        def count(self, text: str) -> int:
            return len(text.split())

    counter = FixtureCounter()
    assert isinstance(counter, TokenCounter)
    assert counter.count("two tokens") == 2


def test_transcript_passage_and_frame_artifacts_have_canonical_round_trips() -> None:
    transcript = _transcript()
    assert TranscriptArtifact.from_dict(transcript.to_dict()) == transcript
    assert canonical_json(TranscriptArtifact.from_dict(transcript.to_dict()).to_dict()) == canonical_json(
        transcript.to_dict()
    )

    atoms = transcript.atoms
    passage = TimedPassage(
        passage_id=passage_id(
            AUDIO_ID,
            GROUPER.value,
            0,
            atoms[0].atom_id,
            atoms[-1].atom_id,
            0,
            2_000,
            "sha256:text-fixture",
        ),
        resource_id=AUDIO_ID,
        representation_id=AUDIO_REPRESENTATION_ID,
        start_ms=0,
        end_ms=2_000,
        text="public transcript 0 public transcript 1",
        ordinal=0,
        token_count=TokenCount(count=8, kind=TOKEN_COUNT_EXACT, counter_fingerprint=COUNTER),
        source_atom_ids=tuple(item.atom_id for item in atoms),
        grouper_fingerprint=GROUPER,
    )
    assert TimedPassage.from_dict(passage.to_dict()) == passage

    frames = FrameCaptionArtifact(
        resource_id=VIDEO_ID,
        representation_id=VIDEO_REPRESENTATION_ID,
        representation_kind="frame_caption",
        observations=(_frame(),),
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
    )
    assert FrameCaptionArtifact.from_dict(frames.to_dict()) == frames


def test_records_reject_invalid_text_relationships_and_unknown_fields() -> None:
    atom = _atom()
    with pytest.raises(ValueError, match="text must be non-empty"):
        TimedTextAtom(
            atom_id=atom.atom_id,
            resource_id=atom.resource_id,
            start_ms=0,
            end_ms=1,
            text=" ",
            ordinal=0,
            producer_fingerprint=PRODUCER,
            normalization_fingerprint=NORMALIZATION,
        )
    with pytest.raises(ValueError, match="all atoms must belong"):
        TranscriptArtifact(
            resource_id=VIDEO_ID,
            representation_id=representation_id(
                VIDEO_ID,
                "audio_transcript",
                PRODUCER.value,
                NORMALIZATION.value,
            ),
            representation_kind="audio_transcript",
            atoms=(atom,),
            producer_fingerprint=PRODUCER,
            normalization_fingerprint=NORMALIZATION,
        )
    payload = atom.to_dict()
    payload["private_path"] = "/private/sentinel.wav"
    with pytest.raises(ValueError, match="must contain exactly"):
        TimedTextAtom.from_dict(payload)


def test_transcript_rejects_invalid_identity_and_timeline_pairs() -> None:
    transcript = _transcript()
    wrong_resource = resource_id("fixture", "other.wav")
    other_producer = ProducerFingerprint.from_payload({"engine": "other", "version": 1})

    invalid_ids = (
        atom_id(wrong_resource, PRODUCER.value, 0),
        atom_id(AUDIO_ID, other_producer.value, 0),
        atom_id(AUDIO_ID, PRODUCER.value, 1),
    )
    for identifier in invalid_ids:
        with pytest.raises(ValueError, match="atom_id must match"):
            replace(transcript.atoms[0], atom_id=identifier)

    with pytest.raises(ValueError, match="representation_id must match"):
        replace(
            transcript,
            representation_id=representation_id(
                wrong_resource,
                transcript.representation_kind,
                PRODUCER.value,
                NORMALIZATION.value,
            ),
        )
    with pytest.raises(ValueError, match="representation_id must match"):
        replace(
            transcript,
            representation_id=representation_id(
                AUDIO_ID,
                transcript.representation_kind,
                other_producer.value,
                NORMALIZATION.value,
            ),
        )
    with pytest.raises(ValueError, match="audio_transcript"):
        replace(transcript, representation_kind="frame_caption")

    timeline_cases = (
        {"atoms": tuple(reversed(transcript.atoms))},
        {"atoms": (transcript.atoms[0], replace(transcript.atoms[1], start_ms=999))},
        {
            "atoms": (
                transcript.atoms[0],
                replace(
                    transcript.atoms[1],
                    ordinal=0,
                    atom_id=atom_id(AUDIO_ID, PRODUCER.value, 0),
                ),
            )
        },
        {
            "atoms": (
                replace(
                    transcript.atoms[0],
                    ordinal=1,
                    atom_id=atom_id(AUDIO_ID, PRODUCER.value, 1),
                ),
            )
        },
        {"duration_ms": transcript.atoms[-1].end_ms - 1},
    )
    for changes in timeline_cases:
        with pytest.raises(ValueError):
            replace(transcript, **changes)


def test_frame_records_reject_wrong_resource_producer_ordinal_and_representation() -> None:
    frame = _frame()
    wrong_resource = resource_id("fixture", "other.mp4")
    other_producer = ProducerFingerprint.from_payload({"engine": "other", "version": 1})
    invalid_ids = (
        frame_id(wrong_resource, PRODUCER.value, 0, frame.timestamp_ms, frame.observation_identity),
        frame_id(VIDEO_ID, other_producer.value, 0, frame.timestamp_ms, frame.observation_identity),
        frame_id(VIDEO_ID, PRODUCER.value, 1, frame.timestamp_ms, frame.observation_identity),
    )
    for identifier in invalid_ids:
        with pytest.raises(ValueError, match="frame_id must match"):
            replace(frame, frame_id=identifier)

    artifact = FrameCaptionArtifact(
        resource_id=VIDEO_ID,
        representation_id=VIDEO_REPRESENTATION_ID,
        representation_kind="frame_caption",
        observations=(frame,),
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
    )
    with pytest.raises(ValueError, match="representation_id must match"):
        replace(
            artifact,
            representation_id=representation_id(
                wrong_resource,
                artifact.representation_kind,
                PRODUCER.value,
                NORMALIZATION.value,
            ),
        )
    with pytest.raises(ValueError, match="representation_id must match"):
        replace(
            artifact,
            representation_id=representation_id(
                VIDEO_ID,
                artifact.representation_kind,
                other_producer.value,
                NORMALIZATION.value,
            ),
        )
    with pytest.raises(ValueError, match="frame_caption"):
        replace(artifact, representation_kind="audio_transcript")


def test_policies_validate_order_and_round_trip_without_execution() -> None:
    chunking = TimedChunkingPolicy()
    normalization = TextNormalizationPolicy()
    whole = WholeResourceTextPolicy()
    assert TimedChunkingPolicy.from_dict(chunking.to_dict()) == chunking
    assert TextNormalizationPolicy.from_dict(normalization.to_dict()) == normalization
    assert WholeResourceTextPolicy.from_dict(whole.to_dict()) == whole
    assert GrouperFingerprint.from_payload(chunking.to_dict()) == GrouperFingerprint.from_payload(
        chunking.to_dict()
    )
    with pytest.raises(ValueError, match="non-decreasing"):
        TimedChunkingPolicy(soft_min_tokens=500, target_tokens=100)
    with pytest.raises(ValueError, match="reject or caller_split"):
        WholeResourceTextPolicy(overflow="truncate")


def test_builder_inputs_round_trip_but_do_not_execute() -> None:
    audio = MediaResourceDescriptor(
        resource_id=AUDIO_ID,
        resource_kind="audio",
        media_type="audio/wav",
        source_namespace="fixture",
        locator=Locator(kind="host_ref", payload={"opaque": "resource-a"}),
    )
    transcript_input = TranscriptBatchBuilderInput(
        resource=audio,
        transcript=_transcript(),
        passage_representation_id=PASSAGE_REPRESENTATION_ID,
        passage_representation_kind="timed_passage",
        chunking_policy=TimedChunkingPolicy(),
        grouper_fingerprint=GROUPER,
        embedding_fingerprint=EMBEDDING,
        whole_text_policy=WholeResourceTextPolicy(),
        aggregation_fingerprint=AGGREGATION,
    )
    assert TranscriptBatchBuilderInput.from_dict(transcript_input.to_dict()) == transcript_input

    frames = FrameCaptionArtifact(
        resource_id=VIDEO_ID,
        representation_id=VIDEO_REPRESENTATION_ID,
        representation_kind="frame_caption",
        observations=(_frame(),),
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
    )
    video = MediaResourceDescriptor(
        resource_id=VIDEO_ID,
        resource_kind="video",
        media_type="video/mp4",
        source_namespace="fixture",
        locator=Locator(kind="host_ref", payload={"opaque": "resource-v"}),
    )
    frame_input = FrameBatchBuilderInput(video, frames, EMBEDDING)
    assert FrameBatchBuilderInput.from_dict(frame_input.to_dict()) == frame_input
    assert not hasattr(frame_input, "build")

    with pytest.raises(ValueError, match="supplied together"):
        TranscriptBatchBuilderInput(
            resource=audio,
            transcript=_transcript(),
            passage_representation_id=PASSAGE_REPRESENTATION_ID,
            passage_representation_kind="timed_passage",
            chunking_policy=TimedChunkingPolicy(),
            grouper_fingerprint=GROUPER,
            whole_text_policy=WholeResourceTextPolicy(),
        )

    with pytest.raises(ValueError, match="passage_representation_id must match"):
        replace(transcript_input, passage_representation_id=AUDIO_REPRESENTATION_ID)


def test_whole_resource_identity_changes_with_aggregation_fingerprint() -> None:
    first = whole_resource_id(AUDIO_ID, AUDIO_REPRESENTATION_ID, AGGREGATION.value)
    second = whole_resource_id(
        AUDIO_ID,
        AUDIO_REPRESENTATION_ID,
        AggregationFingerprint.from_payload({"algorithm": "centroid", "version": 1}).value,
    )
    assert first != second
    assert first == "whole_feaeec7897b67dbc39689625a5fc641ef4b620e0c2675c3855122b5b4e8412f4"


def test_events_fail_closed_and_do_not_expose_ids_locators_or_text() -> None:
    event = MediaEvent(
        "media.build.completed",
        {
            "status": MediaEventStatus.COMPLETED,
            "operation": MediaOperation.BUILD_TRANSCRIPT,
            "resource_kind": MediaResourceKind.AUDIO,
            "atom_count": 2,
            "producer_fingerprint": PRODUCER,
        },
    )
    message = event.to_log_message()
    assert json.loads(message.split(" ", 1)[1]) == {
        "atom_count": 2,
        "operation": "build_transcript",
        "producer_fingerprint": PRODUCER.value,
        "resource_kind": "audio",
        "status": "completed",
    }
    for private_value in (AUDIO_ID, "/private/sentinel.wav", "secret transcript"):
        assert private_value not in message

    redacted = MediaEvent("media.build.failed", {"operation": "secret transcript"})
    assert "secret transcript" not in redacted.to_log_message()
    assert "[redacted]" in redacted.to_log_message()
    with pytest.raises(ValueError, match="outside the safe"):
        MediaEvent("media.build.failed", {"resource_id": AUDIO_ID})
