"""Bounded CPU decoding of owned snapshots. No extractor/model/network runtime."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import tempfile
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from mdrack.domain.embedding_inputs import AudioInput, ImageInput, VideoInput
from mdrack.domain.native_media import NativeMediaKind, NativeMediaPolicy
from mdrack.ingestion.raw_source_provenance import (
    RawSourceError,
    RawSourceErrorCode,
    _read_source_bytes,
    _snapshot_file,
    canonical_json,
    sha256_digest,
)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("native_decoder_response_invalid")
    return dict(value)


def _milliseconds(value: object) -> int:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("native_decoder_response_invalid")
    try:
        result = Decimal(str(value)) * 1000
        if not result.is_finite():
            raise ValueError("native_decoder_response_invalid")
        return int(result)
    except (InvalidOperation, OverflowError):
        raise ValueError("native_decoder_response_invalid") from None


@dataclass(frozen=True)
class DecodedNativeUnit:
    value: ImageInput | AudioInput | VideoInput
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class DecodedNativeSource:
    source_revision: str
    source_bytes: int
    resource_kind: str
    media_type: str
    duration_ms: int
    start_ms: int
    end_ms: int
    recipe_fingerprint: str
    decoder_fingerprint: str
    units: tuple[DecodedNativeUnit, ...]
    selected_path: Path
    max_source_bytes: int

    def check(self) -> None:
        try:
            content = _read_source_bytes(self.selected_path, self.max_source_bytes)
        except RawSourceError:
            raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED) from None
        if len(content) != self.source_bytes or sha256_digest(content) != self.source_revision:
            raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED)


class NativeMediaDecoder:
    """FFmpeg/ffprobe are explicit CPU tools, not provider capabilities.

    Output is PNG, mono PCM16 WAV at 16kHz, or visual-only MP4 clips. Frame PTS
    are read from the decoded stream; requested sampling times are not fabricated PTS.
    Container-relative integer milliseconds use floor conversion. Non-zero stream
    origins are refused in v1 instead of silently rebasing inconsistent tracks.
    """

    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    async def _run(self, tool: str, args: list[str], policy: NativeMediaPolicy, limit: int) -> bytes:
        executable = shutil.which(tool)
        if executable is None:
            raise ValueError("native_decoder_unavailable")
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert process.stdout is not None
        data = bytearray()
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                while chunk := await process.stdout.read(min(65536, limit + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError("native_prepared_budget_exceeded")
                if await process.wait() != 0:
                    raise ValueError("native_decode_failed")
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        return bytes(data)

    async def _probe(self, path: Path, policy: NativeMediaPolicy, *, frames: bool = False) -> dict[str, object]:
        args = ["-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams"]
        if frames:
            args += [
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time:format=duration,start_time:stream=codec_type,start_time",
            ]
        result = await self._run(self.ffprobe, [*args, "-of", "json", str(path)], policy, 4 * 1024 * 1024)
        try:
            value: object = json.loads(result)
            return _mapping(value)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("native_decoder_response_invalid") from None

    async def _fingerprint(self, policy: NativeMediaPolicy) -> str:
        versions = [await self._run(tool, ["-version"], policy, 65536) for tool in (self.ffmpeg, self.ffprobe)]
        return sha256_digest(b"\n".join(v.splitlines()[0] for v in versions))

    @asynccontextmanager
    async def prepare(
        self,
        path: Path,
        kind: NativeMediaKind,
        policy: NativeMediaPolicy,
        *,
        max_input_bytes: int,
        expected_revision: str | None = None,
    ) -> AsyncIterator[DecodedNativeSource]:
        if kind not in {"frames", "audio", "video"}:
            raise ValueError("native_kind_invalid")
        content = _read_source_bytes(path, policy.max_source_bytes)
        revision = sha256_digest(content)
        if expected_revision is not None and revision != expected_revision:
            raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED)
        wave_source = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE"
        video_source = len(content) >= 12 and content[4:8] == b"ftyp"
        if not wave_source and not video_source:
            raise ValueError("native_format_unsupported")
        # No shell, playlists, URLs, or source-file decoder access: tools see a private byte snapshot.
        snapshot = _snapshot_file(content)
        try:
            probe = await self._probe(snapshot, policy)
            streams = probe.get("streams")
            if not isinstance(streams, list):
                raise ValueError("native_decoder_response_invalid")
            facts = [_mapping(s) for s in streams]
            if any(_milliseconds(s.get("start_time", "0")) != 0 for s in facts):
                raise ValueError("native_stream_timeline_unsupported")
            types = {s.get("codec_type") for s in facts}
            if (kind == "audio" and "audio" not in types) or (kind != "audio" and "video" not in types):
                raise ValueError("native_track_unavailable")
            format_info = _mapping(probe.get("format"))
            if _milliseconds(format_info.get("start_time", "0")) != 0:
                raise ValueError("native_stream_timeline_unsupported")
            duration = _milliseconds(format_info.get("duration"))
            start, end = policy.range(duration)
            decoder = await self._fingerprint(policy)
            recipe = sha256_digest(
                canonical_json(
                    {
                        "contract": "mdrack.native-decoding.v1",
                        "kind": kind,
                        "policy": policy.fingerprint,
                        "decoder": decoder,
                        "timing": "original-pts-floor-ms",
                        "audio": "pcm16-mono-16000",
                        "video": "visual-only-mpeg4-trim-no-audio",
                        "frame": "first-actual-pts-at-or-after-grid",
                    }
                )
            )
            timestamps: list[tuple[int, int]] = []
            if kind != "audio":
                frame_probe = await self._probe(snapshot, policy, frames=True)
                frames = frame_probe.get("frames")
                if not isinstance(frames, list):
                    raise ValueError("native_decoder_response_invalid")
                previous = -1
                for index, item in enumerate(frames):
                    stamp = _milliseconds(_mapping(item).get("best_effort_timestamp_time"))
                    if stamp < previous:
                        raise ValueError("native_frame_timeline_unsupported")
                    previous = stamp
                    if start <= stamp < end and (not timestamps or stamp > timestamps[-1][1]):
                        timestamps.append((index, stamp))
                if not timestamps:
                    raise ValueError("native_range_has_no_frames")
            limit = min(max_input_bytes, policy.max_prepared_bytes)
            units: list[DecodedNativeUnit] = []
            value: ImageInput | AudioInput | VideoInput
            total = 0
            with tempfile.TemporaryDirectory(prefix="mdrack-native-") as folder:
                output = Path(folder) / ("part.wav" if kind == "audio" else "part.mp4")
                base = [
                    "-v",
                    "error",
                    "-nostdin",
                    "-threads",
                    "1",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(snapshot),
                    "-threads",
                    "1",
                ]
                scale = (
                    f"scale={policy.max_width}:{policy.max_height}:"
                    "force_original_aspect_ratio=decrease:force_divisible_by=2"
                )
                if kind == "frames":
                    chosen = []
                    threshold = start
                    for index, stamp in timestamps:
                        if stamp >= threshold:
                            chosen.append((index, stamp))
                            threshold = start + ((stamp - start) // policy.frame_step_ms + 1) * policy.frame_step_ms
                    if len(chosen) > policy.max_units:
                        raise ValueError("native_unit_budget_exceeded")
                    for index, stamp in chosen:
                        payload = await self._run(
                            self.ffmpeg,
                            [
                                *base,
                                "-map",
                                "0:v:0",
                                "-an",
                                "-vf",
                                f"select=eq(n\\,{index}),{scale}",
                                "-frames:v",
                                "1",
                                "-f",
                                "image2pipe",
                                "-c:v",
                                "png",
                                "pipe:1",
                            ],
                            policy,
                            limit,
                        )
                        value = ImageInput(payload, "image/png", sha256_digest(payload))
                        units.append(DecodedNativeUnit(value, stamp, stamp))
                        total += value.byte_size
                        if total > policy.max_prepared_bytes:
                            raise ValueError("native_prepared_budget_exceeded")
                else:
                    for left, right in policy.windows(duration):
                        output.unlink(missing_ok=True)
                        if kind == "audio":
                            args = [
                                "-map",
                                "0:a:0",
                                "-vn",
                                "-af",
                                f"aresample=16000,atrim=start_sample={left * 16}:end_sample={right * 16},"
                                "asetpts=PTS-STARTPTS",
                                "-ac",
                                "1",
                                "-ar",
                                "16000",
                                "-c:a",
                                "pcm_s16le",
                            ]
                        else:
                            selected = [(i, t) for i, t in timestamps if left <= t < right]
                            if not selected:
                                raise ValueError("native_range_has_no_frames")
                            left = selected[0][1]
                            args = [
                                "-map",
                                "0:v:0",
                                "-an",
                                "-vf",
                                f"trim=start={left / 1000}:end={right / 1000},setpts=PTS-STARTPTS,{scale}",
                                "-c:v",
                                "mpeg4",
                                "-q:v",
                                "3",
                                "-pix_fmt",
                                "yuv420p",
                            ]
                        await self._run(
                            self.ffmpeg, [*base, *args, "-fs", str(limit + 1), "-y", str(output)], policy, 65536
                        )
                        if output.stat().st_size > limit:
                            raise ValueError("native_prepared_budget_exceeded")
                        payload = output.read_bytes()
                        if kind == "audio":
                            with wave.open(io.BytesIO(payload), "rb") as audio:
                                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                                    raise ValueError("native_audio_decode_invalid")
                                if audio.getnframes() != (right - left) * 16:
                                    raise ValueError("native_audio_coverage_incomplete")
                            value = AudioInput(payload, sha256_digest(payload), right - left)
                        else:
                            encoded = await self._probe(output, policy)
                            encoded_duration = _milliseconds(_mapping(encoded.get("format")).get("duration"))
                            value = VideoInput(payload, sha256_digest(payload), encoded_duration)
                        units.append(DecodedNativeUnit(value, left, right))
                        total += value.byte_size
                        if total > policy.max_prepared_bytes:
                            raise ValueError("native_prepared_budget_exceeded")
            result = DecodedNativeSource(
                revision,
                len(content),
                "video" if "video" in types else "audio",
                "video/mp4" if "video" in types else "audio/wav" if wave_source else "audio/mp4",
                duration,
                start,
                end,
                recipe,
                decoder,
                tuple(units),
                path,
                policy.max_source_bytes,
            )
            result.check()
            yield result  # Publication checks run immediately before CAS; no post-commit filesystem veto.
        finally:
            snapshot.unlink(missing_ok=True)
