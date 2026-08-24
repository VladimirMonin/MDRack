"""Provider-free direct ISO-BMFF video ingestion through one local extractor."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from mdrack.application.video_composition import VideoCompositionService
from mdrack.ingestion.frame_captions import FrameCaptionManifestError, read_frame_captions
from mdrack.ingestion.raw_source_provenance import (
    RawInputBudget,
    RawMediaKind,
    RawSignatureFact,
    RawSignatureKind,
    RawSourceError,
    RawSourceErrorCode,
    RawSourceSnapshot,
    canonical_json,
    capture_source,
    check_source_after,
    resource_metadata,
    sha256_digest,
    validate_budget,
    validate_source_ref,
)
from mdrack.ingestion.transcripts import TranscriptReadError, read_timed_json
from mdrack_core import Locator, PreparedResourceBatch
from mdrack_core.ports.catalog import ResourceWritePort
from mdrack_media import ProducerFingerprint, resource_id

RAW_VIDEO_KIND = "video"
RAW_VIDEO_SCHEMA = "mdrack.raw-video-extraction.v1"
RAW_VIDEO_PROTOCOL = "mdrack-raw-video-extractor-stdin-v1"
RAW_VIDEO_TIMEOUT_SECONDS = 600
MAX_RAW_VIDEO_STDOUT_BYTES = RawInputBudget().max_prepared_text_bytes


@dataclass(frozen=True)
class RawVideoResult:
    resource_id: str
    resource_kind: str
    media_type: str
    representation_count: int
    transcript_unit_count: int
    frame_unit_count: int
    vector_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "media_type": self.media_type,
            "representation_count": self.representation_count,
            "transcript_unit_count": self.transcript_unit_count,
            "frame_unit_count": self.frame_unit_count,
            "vector_count": self.vector_count,
        }


def _iso_bmff_signature(content: bytes) -> RawSignatureFact:
    if len(content) < 8 or content[4:8] != b"ftyp":
        raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED)
    return RawSignatureFact(RawSignatureKind.ISO_BMFF, "video/mp4", "iso-bmff-ftyp-magic-v1")


class _GuardedWritePort:
    def __init__(self, delegate: ResourceWritePort, snapshot: RawSourceSnapshot, path: Path,
                 budget: RawInputBudget, provenance: dict[str, object]) -> None:
        self._delegate, self._snapshot, self._path = delegate, snapshot, path
        self._budget, self._provenance = budget, provenance

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        check_source_after(self._snapshot, self._path, self._budget)
        resource = replace(batch.resource, content_hash=self._snapshot.provenance.raw_source_sha256,
                           metadata={**dict(batch.resource.metadata), "provenance": cast(Any, self._provenance)})
        self._delegate.replace_resource(replace(batch, resource=resource))

    def delete_resource(self, resource_id: str) -> None:
        self._delegate.delete_resource(resource_id)


def _run_extractor(command: str, source_bytes: bytes) -> bytes:
    if not command or "\x00" in command:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="mdrack-video-", suffix=".stdout", delete=False) as stream:
            output_path = Path(stream.name)
        overflow = threading.Event()
        io_failed = threading.Event()
        try:
            process = subprocess.Popen([command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, shell=False)
        except OSError:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
        stdin, stdout = process.stdin, process.stdout
        assert stdin is not None and stdout is not None

        def send() -> None:
            try:
                stdin.write(source_bytes)
            except (BrokenPipeError, OSError):
                io_failed.set()
            finally:
                try:
                    stdin.close()
                except OSError:
                    pass

        def receive() -> None:
            try:
                with output_path.open("wb") as output:
                    while True:
                        chunk = stdout.read(64 * 1024)
                        if not chunk:
                            break
                        remaining = MAX_RAW_VIDEO_STDOUT_BYTES + 1 - output.tell()
                        if remaining > 0:
                            output.write(chunk[:remaining])
                        if len(chunk) > remaining or output.tell() > MAX_RAW_VIDEO_STDOUT_BYTES:
                            overflow.set()
                            process.kill()
                            break
            except OSError:
                io_failed.set()
                process.kill()

        sender = threading.Thread(target=send, daemon=True)
        receiver = threading.Thread(target=receive, daemon=True)
        sender.start()
        receiver.start()
        try:
            returncode = process.wait(timeout=RAW_VIDEO_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
        finally:
            sender.join()
            receiver.join()
        if overflow.is_set():
            raise RawSourceError(RawSourceErrorCode.PREPARED_TEXT_LIMIT_EXCEEDED)
        if io_failed.is_set() or returncode != 0:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        return output_path.read_bytes()
    except OSError:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
    finally:
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass


def _strict_extraction(payload: bytes, rid: str, producer: str, duration_ms: int) -> tuple[Any, Any, str, str]:
    try:
        data = json.loads(payload.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
    if not isinstance(data, dict) or set(data) != {"schema", "media_type", "duration_ms", "transcript", "frames"}:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    if data["schema"] != RAW_VIDEO_SCHEMA or data["media_type"] != "video/mp4":
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    root_duration = data["duration_ms"]
    if type(root_duration) is not int or root_duration <= 0 or root_duration != duration_ms:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    transcript_data = data["transcript"]
    frames_data = data["frames"]
    if not isinstance(transcript_data, dict) or not isinstance(frames_data, list):
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    if set(transcript_data) - {"schema", "duration_ms", "atoms", "language"}:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    if transcript_data.get("duration_ms") != duration_ms or not frames_data or len(frames_data) > 600:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    transcript_fp = ProducerFingerprint.from_payload({"protocol": RAW_VIDEO_PROTOCOL, "producer": producer,
                                                        "component": "transcript", "media_type": "video/mp4"})
    frame_fp = ProducerFingerprint.from_payload({"protocol": RAW_VIDEO_PROTOCOL, "producer": producer,
                                                   "component": "frame_captions", "media_type": "video/mp4"})
    try:
        transcript = read_timed_json(canonical_json(transcript_data), resource_id=rid,
                                     producer_fingerprint=transcript_fp, strict=True).artifact
        if not transcript.atoms or transcript.duration_ms != duration_ms:
            raise ValueError
        if any(atom.end_ms > duration_ms for atom in transcript.atoms):
            raise ValueError
        frame_items: list[dict[str, object]] = []
        frame_payload: dict[str, Any] = {
            "schema": "mdrack.frame-captions.v1", "resource_id": rid,
            "producer_fingerprint": frame_fp.value, "normalization_fingerprint": None,
            "metadata": {}, "frames": frame_items,
        }
        previous_timestamp = -1
        for ordinal, item in enumerate(frames_data):
            if not isinstance(item, dict) or set(item) != {"timestamp_ms", "caption"}:
                raise ValueError
            timestamp, caption = item["timestamp_ms"], item["caption"]
            if (
                type(timestamp) is not int
                or not 0 <= timestamp <= duration_ms
                or timestamp < previous_timestamp
                or not isinstance(caption, str)
                or not caption.strip()
            ):
                raise ValueError
            previous_timestamp = timestamp
            frame_items.append({"frame_id": f"raw-video-frame-{ordinal}",
                                "timestamp_ms": timestamp, "caption": caption, "metadata": {}})
        frames = read_frame_captions(canonical_json(frame_payload)).artifact
    except (TranscriptReadError, FrameCaptionManifestError, TypeError, ValueError, KeyError):
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
    return transcript, frames, transcript_fp.value, frame_fp.value


class RawVideoIngestionService:
    def __init__(self, catalog: ResourceWritePort, *, budget: RawInputBudget | None = None) -> None:
        if not callable(getattr(catalog, "replace_resource", None)):
            raise TypeError("catalog must support complete resource replacement")
        self._catalog, self._budget = catalog, budget or RawInputBudget()

    def ingest(self, source_path: Path, *, source_ref: str, root: Path,
               video_extractor_command: str, allow_external_video_extractor: bool,
               producer: str = "caller-supplied") -> RawVideoResult:
        validate_source_ref(source_ref)
        try:
            source_path.resolve().relative_to(root.resolve())
        except ValueError:
            pass
        else:
            raise RawSourceError(RawSourceErrorCode.SOURCE_REF_INVALID)
        if (
            not allow_external_video_extractor
            or not video_extractor_command
            or not isinstance(producer, str)
            or not producer.strip()
        ):
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        snapshot = capture_source(source_path, source_ref, RawMediaKind.VIDEO,
                                  signature_probe=_iso_bmff_signature, budget=self._budget,
                                  duration_ms=1, selected_frame_count=1)
        try:
            rid = resource_id("raw-local", source_ref)
            output = _run_extractor(video_extractor_command, snapshot.source_bytes)
            # Duration is supplied by the extractor; validate it before constructing R0 provenance.
            try:
                raw = json.loads(output.decode("utf-8", "strict"))
                duration = raw["duration_ms"]
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
            if type(duration) is not int or duration <= 0 or duration > self._budget.max_duration_ms:
                raise RawSourceError(RawSourceErrorCode.DURATION_LIMIT_EXCEEDED)
            transcript, frames, transcript_fp, frame_fp = _strict_extraction(output, rid, producer, duration)
            snapshot = replace(snapshot, provenance=replace(snapshot.provenance, duration_ms=duration,
                                                            selected_frame_count=len(frames.observations)))
            prepared_text = "\n\n".join(
                [*(atom.text for atom in transcript.atoms), *(item.caption for item in frames.observations)]
            )
            validate_budget(snapshot.provenance, self._budget, prepared_text=prepared_text,
                            whole_resource_tokens=len(prepared_text.split()))
            evidence = {"schema": RAW_VIDEO_SCHEMA, "protocol": RAW_VIDEO_PROTOCOL,
                        "media_type": "video/mp4", "duration_ms": duration,
                        "transcript": transcript.to_dict(), "frames": frames.to_dict(),
                        "transcript_producer_fingerprint": transcript_fp,
                        "frame_producer_fingerprint": frame_fp}
            digest = sha256_digest(canonical_json(evidence))
            metadata = resource_metadata(replace(snapshot.provenance, prepared_evidence_sha256=digest))
            service = VideoCompositionService(cast(ResourceWritePort, _GuardedWritePort(
                self._catalog, snapshot, source_path, self._budget,
                cast(dict[str, object], metadata["provenance"]))))
            result = asyncio.run(service.ingest(
                transcript, frames, media_type="video/mp4", source_namespace="raw_local",
                source_locator=Locator("raw_local_source", {"source_ref": source_ref}),
                source_metadata=cast(Any, metadata), embeddings=False))
            return RawVideoResult(result.resource_id, "video", "video/mp4", result.representation_count,
                                  result.transcript_unit_count, result.frame_unit_count, 0)
        finally:
            snapshot.cleanup()


__all__ = ["RawVideoIngestionService", "RawVideoResult", "RAW_VIDEO_KIND"]
