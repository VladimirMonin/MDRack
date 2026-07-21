"""Strict complete-video manifest parsing without provider or media access."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from mdrack.application.manifest import MAX_MANIFEST_BYTES
from mdrack.ingestion.frame_captions import validate_frame_caption_artifact
from mdrack_core import JSONValue, Locator
from mdrack_media import FrameCaptionArtifact, TranscriptArtifact

VIDEO_RESOURCE_SCHEMA = "mdrack.video-resource.v1"
_ROOT_KEYS = frozenset({"frame_captions", "resource", "schema", "transcript"})
_RESOURCE_KEYS = frozenset(
    {"locator", "media_type", "resource_id", "source_metadata", "source_namespace", "title"}
)


class MediaManifestError(ValueError):
    """A fixed, payload-free complete-media manifest failure."""


@dataclass(frozen=True)
class VideoResourceManifest:
    transcript: TranscriptArtifact
    frame_captions: FrameCaptionArtifact
    media_type: str
    source_namespace: str
    source_locator: Locator
    source_metadata: Mapping[str, JSONValue]
    title: str | None = None


def read_video_resource_manifest(source: bytes) -> VideoResourceManifest:
    """Parse canonical artifacts for one complete provider-free video replacement."""
    if not isinstance(source, bytes) or len(source) > MAX_MANIFEST_BYTES:
        raise MediaManifestError("media_manifest_invalid")
    try:
        data = json.loads(source.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MediaManifestError("media_manifest_invalid") from None
    if not isinstance(data, dict) or set(data) != _ROOT_KEYS:
        raise MediaManifestError("media_manifest_invalid")
    if data.get("schema") != VIDEO_RESOURCE_SCHEMA:
        raise MediaManifestError("media_manifest_invalid")
    resource = data.get("resource")
    if not isinstance(resource, dict) or set(resource) != _RESOURCE_KEYS:
        raise MediaManifestError("media_manifest_invalid")
    locator = resource.get("locator")
    metadata = resource.get("source_metadata")
    title = resource.get("title")
    if (
        not isinstance(locator, dict)
        or set(locator) != {"kind", "payload"}
        or not isinstance(locator.get("kind"), str)
        or not isinstance(locator.get("payload"), dict)
        or not isinstance(metadata, dict)
        or (title is not None and not isinstance(title, str))
    ):
        raise MediaManifestError("media_manifest_invalid")
    try:
        transcript = TranscriptArtifact.from_dict(data.get("transcript"))
        frames = validate_frame_caption_artifact(
            FrameCaptionArtifact.from_dict(data.get("frame_captions"))
        )
        resource_id = resource.get("resource_id")
        media_type = resource.get("media_type")
        namespace = resource.get("source_namespace")
        if (
            not isinstance(resource_id, str)
            or transcript.resource_id != resource_id
            or frames.resource_id != resource_id
            or not isinstance(media_type, str)
            or not media_type
            or not isinstance(namespace, str)
            or not namespace
        ):
            raise MediaManifestError("media_manifest_invalid")
        return VideoResourceManifest(
            transcript=transcript,
            frame_captions=frames,
            media_type=media_type,
            source_namespace=namespace,
            source_locator=Locator(
                cast(str, locator["kind"]),
                cast(Mapping[str, JSONValue], locator["payload"]),
            ),
            source_metadata=cast(Mapping[str, JSONValue], metadata),
            title=title,
        )
    except MediaManifestError:
        raise
    except (TypeError, ValueError):
        raise MediaManifestError("media_manifest_invalid") from None


__all__ = [
    "MediaManifestError",
    "VIDEO_RESOURCE_SCHEMA",
    "VideoResourceManifest",
    "read_video_resource_manifest",
]
