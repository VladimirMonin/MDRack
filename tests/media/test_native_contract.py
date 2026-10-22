"""Exact additive native exports; preserve accepted public40 and every old media name."""

import importlib

import pytest

import mdrack.public_api as api
import mdrack_media

EXPECTED_MEDIA = [
    "AggregationFingerprint",
    "ContentFingerprint",
    "EmbeddingFingerprint",
    "FrameBatchBuilderInput",
    "FrameCaptionArtifact",
    "FrameCaptionObservation",
    "GROUPER_ALGORITHM",
    "GrouperFingerprint",
    "GroupingMetrics",
    "GroupingPolicyVariant",
    "GroupingVariantResult",
    "ID_ATOM",
    "ID_FRAME",
    "ID_PASSAGE",
    "ID_REPRESENTATION",
    "ID_RESOURCE",
    "ID_WHOLE",
    "JOIN_POLICY",
    "JSONScalar",
    "JSONValue",
    "LOCATOR_TIME_SEGMENT",
    "LOCATOR_VIDEO_FRAME",
    "LOCATOR_WHOLE_MEDIA",
    "MEDIA_CONTRACT_VERSION",
    "MEDIA_EVENT_NAMES",
    "MEDIA_RESOURCE_KINDS",
    "MediaEvent",
    "MediaEventCategory",
    "MediaEventStatus",
    "MediaOperation",
    "MediaResourceDescriptor",
    "MediaResourceKind",
    "MediaRetrievalEvidence",
    "MediaRetrievalItem",
    "MediaRetrievalResult",
    "NativeAudioObservation",
    "NativeBatchInput",
    "NativeFrameObservation",
    "NativeVideoObservation",
    "NormalizationFingerprint",
    "OVERFLOW_CALLER_SPLIT",
    "OVERFLOW_REJECT",
    "ProducerFingerprint",
    "REPRESENTATION_AUDIO_TRANSCRIPT",
    "REPRESENTATION_FRAME_CAPTION",
    "REPRESENTATION_TIMED_PASSAGE",
    "RESOURCE_AUDIO",
    "RESOURCE_VIDEO",
    "RetrievalMode",
    "TOKEN_COUNT_ESTIMATED",
    "TOKEN_COUNT_EXACT",
    "TRACK_AUDIO",
    "TRACK_VIDEO",
    "TextNormalizationPolicy",
    "TimeSegmentLocator",
    "TimedChunkingPolicy",
    "TimedGroupingError",
    "TimedGroupingResult",
    "TimedPassage",
    "TimedTextAtom",
    "TokenCount",
    "TokenCounter",
    "TokenCounterFingerprint",
    "TranscriptArtifact",
    "TranscriptBatchBuilderInput",
    "UNSPLITTABLE_FLAG",
    "UNSPLITTABLE_REJECT",
    "VideoFrameLocator",
    "WholeMediaLocator",
    "WholeResourceTextPolicy",
    "atom_id",
    "build_audio_transcript_batch",
    "build_native_audio_batch",
    "build_native_frame_batch",
    "build_native_video_batch",
    "build_video_frame_caption_batch",
    "build_video_transcript_batch",
    "canonical_json",
    "frame_id",
    "group_timed_atoms",
    "passage_id",
    "provisional_abc_policies",
    "representation_id",
    "resource_id",
    "retrieve_media",
    "run_grouping_variants",
    "stable_media_id",
    "validate_media_id",
    "weighted_centroid",
    "whole_resource_id",
]
EXPECTED_APP = [
    "ReadyOCRError",
    "ReadyOCROperation",
    "MediaSearchRequest",
    "MediaSearchResult",
    "StoredUnitInput",
    "SourceResolver",
    "EmbeddingResult",
    "EncodingRole",
    "ImageInput",
    "InputCapability",
    "RouteCapabilities",
    "TextInput",
    "LegacyTextEmbeddingAdapter",
    "PreparedEmbeddingProvider",
    "EmbeddingCapabilities",
    "EmbeddingProfile",
    "DuplicateResourceItem",
    "DuplicateResourceResult",
    "FacetFilter",
    "HybridRetrievalService",
    "ExtractedImageText",
    "ImageEmbeddingSpace",
    "ImageExtractor",
    "ImageIngestionResult",
    "ImageSearchItem",
    "ImageSearchResult",
    "IndexingResult",
    "MDRackEngine",
    "RetrievalCandidate",
    "RetrievalItem",
    "RetrievalResult",
    "ResourceQueryScope",
    "SimilarResourceItem",
    "SimilarResourceResult",
    "SourceLocator",
    "StaticImageExtractor",
    "VisualEmbeddingProvider",
    "DurableArtifactStore",
    "PreparedEnrichmentOperation",
    "PreparedReindex",
    "AudioInput",
    "VideoInput",
    "NativeMediaPolicy",
    "NativeMediaResult",
    "NativeMediaDecoder",
]


def test_native_exact_export_contract_and_canonical_owners():
    assert mdrack_media.__all__ == EXPECTED_MEDIA
    assert api.__all__ == EXPECTED_APP
    assert len(api.__all__) == len(set(api.__all__)) == 45
    for name in (
        "NativeAudioObservation",
        "NativeBatchInput",
        "NativeFrameObservation",
        "NativeVideoObservation",
        "build_native_audio_batch",
        "build_native_frame_batch",
        "build_native_video_batch",
    ):
        assert getattr(mdrack_media, name) is getattr(importlib.import_module("mdrack_media.native"), name)
    for name, module in [
        ("AudioInput", "mdrack.domain.embedding_inputs"),
        ("VideoInput", "mdrack.domain.embedding_inputs"),
        ("NativeMediaPolicy", "mdrack.domain.native_media"),
        ("NativeMediaResult", "mdrack.application.native_media"),
        ("NativeMediaDecoder", "mdrack.ingestion.native_decoding"),
    ]:
        assert getattr(api, name) is getattr(importlib.import_module(module), name)


@pytest.mark.parametrize("damage", ["missing", "reordered", "duplicate", "extra"])
def test_native_closed_export_negative_controls(monkeypatch, damage):
    from tests.unit.test_release_publication import test_public_api_exports_v03_image_and_resource_contracts

    values = list(EXPECTED_APP)
    if damage == "missing":
        values.pop()
    elif damage == "reordered":
        values[0], values[1] = values[1], values[0]
    elif damage == "duplicate":
        values[-1] = values[0]
    else:
        values.append("UNDECLARED_NATIVE_EXPORT")
    monkeypatch.setattr(api, "__all__", values)
    with pytest.raises(AssertionError):
        test_public_api_exports_v03_image_and_resource_contracts()
