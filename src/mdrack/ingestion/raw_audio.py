"""Provider-free direct RIFF/WAVE audio ingestion through timed transcripts."""

from __future__ import annotations

import asyncio
import io
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from mdrack.application.transcript_ingestion import TranscriptIngestionService
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

RAW_AUDIO_KIND = "audio"
RAW_AUDIO_SCHEMA = "mdrack.raw-audio-prepared-evidence.v1"
RAW_AUDIO_TIMEOUT_SECONDS = 600
MAX_STT_STDOUT_BYTES = RawInputBudget().max_prepared_text_bytes


@dataclass(frozen=True)
class RawAudioResult:
    resource_id: str
    resource_kind: str
    media_type: str
    representation_count: int
    unit_count: int
    vector_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "media_type": self.media_type,
            "representation_count": self.representation_count,
            "unit_count": self.unit_count,
            "vector_count": self.vector_count,
        }


def _wave_signature(content: bytes) -> RawSignatureFact:
    if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED)
    return RawSignatureFact(RawSignatureKind.RIFF_WAVE, "audio/wav", "audio-wave-magic-v1")


def _wave_duration_ms(content: bytes) -> int:
    try:
        with wave.open(io.BytesIO(content), "rb") as stream:
            frames = stream.getnframes()
            rate = stream.getframerate()
            if frames <= 0 or rate <= 0:
                raise ValueError
            return (frames * 1000) // rate
    except (OSError, EOFError, wave.Error, ValueError, ZeroDivisionError):
        raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED) from None


class _GuardedWritePort:
    def __init__(
        self,
        delegate: ResourceWritePort,
        snapshot: RawSourceSnapshot,
        source_path: Path,
        budget: RawInputBudget,
        provenance: dict[str, object],
    ) -> None:
        self._delegate = delegate
        self._snapshot = snapshot
        self._source_path = source_path
        self._budget = budget
        self._provenance = provenance

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        check_source_after(self._snapshot, self._source_path, self._budget)
        resource = replace(
            batch.resource,
            content_hash=self._snapshot.provenance.raw_source_sha256,
            metadata={**dict(batch.resource.metadata), "provenance": self._provenance},
        )
        self._delegate.replace_resource(replace(batch, resource=resource))

    def delete_resource(self, resource_id: str) -> None:
        self._delegate.delete_resource(resource_id)


def _run_stt(command: str, source_bytes: bytes) -> bytes:
    if not command or "\x00" in command:
        raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="mdrack-stt-", suffix=".stdout", delete=False) as stream:
            output_path = Path(stream.name)
        overflow = threading.Event()
        writer_failed = threading.Event()
        try:
            process = subprocess.Popen(
                [command],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except OSError:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
        stdin = process.stdin
        stdout = process.stdout
        assert stdin is not None
        assert stdout is not None

        def write_input() -> None:
            try:
                stdin.write(source_bytes)
            except (BrokenPipeError, OSError):
                writer_failed.set()
            finally:
                try:
                    stdin.close()
                except OSError:
                    pass

        def read_bounded_output() -> None:
            try:
                with output_path.open("wb") as output:
                    while True:
                        chunk = stdout.read(64 * 1024)
                        if not chunk:
                            break
                        remaining = MAX_STT_STDOUT_BYTES + 1 - output.tell()
                        if remaining > 0:
                            output.write(chunk[:remaining])
                        if len(chunk) > remaining or output.tell() > MAX_STT_STDOUT_BYTES:
                            overflow.set()
                            process.kill()
                            break
            except OSError:
                writer_failed.set()
                process.kill()

        input_thread = threading.Thread(target=write_input, daemon=True)
        output_thread = threading.Thread(target=read_bounded_output, daemon=True)
        input_thread.start()
        output_thread.start()
        try:
            returncode = process.wait(timeout=RAW_AUDIO_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
        finally:
            input_thread.join()
            output_thread.join()
        if overflow.is_set():
            raise RawSourceError(RawSourceErrorCode.PREPARED_TEXT_LIMIT_EXCEEDED)
        if writer_failed.is_set() or returncode != 0:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        try:
            return output_path.read_bytes()
        except OSError:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
    finally:
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass


class RawAudioIngestionService:
    """Capture one WAVE source, obtain strict timed JSON, and replace one graph."""

    def __init__(self, catalog: ResourceWritePort, *, budget: RawInputBudget | None = None) -> None:
        if not callable(getattr(catalog, "replace_resource", None)):
            raise TypeError("catalog must support complete resource replacement")
        self._catalog = catalog
        self._budget = budget or RawInputBudget()

    def ingest(
        self,
        source_path: Path,
        *,
        source_ref: str,
        root: Path,
        stt_command: str,
        allow_external_stt: bool,
        language: str | None = None,
        producer: str = "caller-supplied",
    ) -> RawAudioResult:
        validate_source_ref(source_ref)
        try:
            source_path.resolve().relative_to(root.resolve())
        except ValueError:
            pass
        else:
            raise RawSourceError(RawSourceErrorCode.SOURCE_REF_INVALID)
        if not allow_external_stt or not stt_command:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        if not isinstance(producer, str) or not producer.strip():
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)

        snapshot = capture_source(
            source_path,
            source_ref,
            RawMediaKind.AUDIO,
            signature_probe=_wave_signature,
            budget=self._budget,
            duration_ms=1,
        )
        try:
            duration_ms = _wave_duration_ms(snapshot.source_bytes)
            if duration_ms <= 0:
                raise RawSourceError(RawSourceErrorCode.DURATION_LIMIT_EXCEEDED)
            if duration_ms > self._budget.max_duration_ms:
                raise RawSourceError(RawSourceErrorCode.DURATION_LIMIT_EXCEEDED)
            snapshot = replace(
                snapshot,
                provenance=replace(snapshot.provenance, duration_ms=duration_ms),
            )
            fingerprint = ProducerFingerprint.from_payload(
                {
                    "protocol": "mdrack-raw-audio-stdin-v1",
                    "producer": producer,
                    "signature": snapshot.provenance.signature.kind.value,
                    "media_type": snapshot.provenance.signature.verified_media_type,
                }
            )
            rid = resource_id("raw-local", source_ref)
            stt_output = _run_stt(stt_command, snapshot.source_bytes)
            try:
                read_result = read_timed_json(
                    stt_output,
                    resource_id=rid,
                    producer_fingerprint=fingerprint,
                    language=language,
                    strict=True,
                )
            except (TranscriptReadError, UnicodeError, ValueError):
                raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID) from None
            artifact = read_result.artifact
            if artifact.duration_ms is not None and artifact.duration_ms > duration_ms:
                raise RawSourceError(RawSourceErrorCode.DURATION_LIMIT_EXCEEDED)
            prepared_text = "\n\n".join(atom.text for atom in artifact.atoms)
            validate_budget(
                snapshot.provenance,
                self._budget,
                prepared_text=prepared_text,
                whole_resource_tokens=len(prepared_text.split()),
            )
            evidence = {
                "schema": RAW_AUDIO_SCHEMA,
                "protocol": "mdrack-raw-audio-stdin-v1",
                "media_type": snapshot.provenance.signature.verified_media_type,
                "duration_ms": duration_ms,
                "artifact": artifact.to_dict(),
                "producer_fingerprint": fingerprint.value,
            }
            prepared_digest = sha256_digest(canonical_json(evidence))
            metadata_provenance = resource_metadata(
                replace(snapshot.provenance, prepared_evidence_sha256=prepared_digest)
            )
            service = TranscriptIngestionService(
                cast(ResourceWritePort, _GuardedWritePort(
                    self._catalog,
                    snapshot,
                    source_path,
                    self._budget,
                    cast(dict[str, object], metadata_provenance["provenance"]),
                )),
            )
            result = asyncio.run(
                service.ingest(
                    artifact,
                    resource_kind=RAW_AUDIO_KIND,
                    media_type=snapshot.provenance.signature.verified_media_type,
                    source_namespace="raw_local",
                    source_locator=Locator("raw_local_source", {"source_ref": source_ref}),
                    embeddings=False,
                )
            )
            return RawAudioResult(
                result.resource_id,
                result.resource_kind,
                snapshot.provenance.signature.verified_media_type,
                result.representation_count,
                result.unit_count,
                0,
            )
        finally:
            snapshot.cleanup()


__all__ = ["RawAudioIngestionService", "RawAudioResult", "RAW_AUDIO_KIND"]
