"""Scripted typed encoders; genuine CPU decode + engine/SQLite. Never model quality."""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import wave
from dataclasses import replace

import pytest
from click.testing import CliRunner

from mdrack.application.media_search import MediaSearchRequest, StoredUnitInput
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import (
    AudioInput,
    EmbeddingResult,
    EncodingRole,
    ImageInput,
    InputCapability,
    RouteCapabilities,
    TextInput,
    VideoInput,
)
from mdrack.domain.native_media import NativeMediaPolicy
from mdrack.public_api import MDRackEngine
from mdrack_core import EmbeddingSpaceRecord, LexicalBranch, SearchScope
from mdrack_media import resource_id


class ScriptedProvider:
    space = EmbeddingSpaceRecord(
        "native-component-space",
        2,
        "cosine",
        "native-component-profile",
        metadata={"vector_value_policy": "ieee754-f32-canonical-v1", "vector_codec": "ieee754-f32-le-v1"},
    )
    capabilities = RouteCapabilities(
        tuple(
            InputCapability(role, m, ("text", "image", "audio", "video"), 2 * 1024 * 1024, 5000)
            for role in EncodingRole
            for m in ("text", "image", "audio", "video")
        ),
        True,
    )

    def __init__(self):
        self.calls = []
        self.hook = None

    async def encode(self, value, role):
        self.capabilities.validate(value, role)
        assert value.byte_size > 0
        if isinstance(value, AudioInput):
            with wave.open(io.BytesIO(value.content), "rb") as f:
                assert f.getframerate() == 16000 and f.getnchannels() == 1 and f.getnframes() > 0
        elif isinstance(value, VideoInput):
            assert value.content[4:8] == b"ftyp" and len(value.content) > 100
        elif isinstance(value, ImageInput):
            assert value.content.startswith(b"\x89PNG\r\n\x1a\n")
        self.calls.append((value, role))
        if self.hook:
            await self.hook()
        return EmbeddingResult((1.0, 0.0), self.space)


@pytest.fixture
def media_files(tmp_path):
    audio = tmp_path / "owned.wav"
    with wave.open(str(audio), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x10\x00" * 32000)
    video = tmp_path / "owned.mp4"
    exe = shutil.which("ffmpeg")
    assert exe, "local CPU FFmpeg required for this lane"
    subprocess.run(
        [
            exe,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x24:rate=4:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=2",
            "-threads",
            "1",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    return audio, video


def engine(tmp_path, provider):
    root = tmp_path / "collection"
    root.mkdir()
    result = CliRunner().invoke(main, ["--root", str(root), "init"])
    assert result.exit_code == 0, result.output
    return MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider)


def blobs(e):
    return {
        (r[0], r[1]): bytes(r[2])
        for r in e.storage.resource_store.connection.execute(
            "SELECT unit_id,space_id,embedding FROM core_unit_embeddings"
        )
    }


@pytest.mark.parametrize("kind", ["frames", "audio", "video"])
async def test_native_decode_engine_search_reopen_and_repeat(tmp_path, media_files, kind):
    audio, video = media_files
    path = audio if kind == "audio" else video
    before = path.read_bytes()
    provider = ScriptedProvider()
    e = engine(tmp_path, provider)
    rid = resource_id("native-fixture", kind)
    policy = NativeMediaPolicy(
        start_ms=250, end_ms=1750, window_ms=750, hop_ms=750, frame_step_ms=500, max_width=32, max_height=24
    )
    try:
        result = await e.ingest_native_media(
            path, kind=kind, resource_id=rid, source_namespace="native-fixture", source_ref=path.name, policy=policy
        )
        assert result.publication_status == "published"
        state = e.storage.resource_store.read_snapshot(rid)
        assert state.state.status == "published" and state.batch is not None
        assert all(r.text is None for r in state.batch.representations)
        assert all(u.text is None for u in state.batch.units)
        assert all(u.modality == ("image" if kind == "frames" else kind) for u in state.batch.units)
        assert state.batch.resource.content_hash == "sha256:" + hashlib.sha256(before).hexdigest()
        assert not e.storage.resource_store.search_lexical(LexicalBranch("q", "owned"), scope=SearchScope())
        saved = blobs(e)
        calls = len(provider.calls)
        repeated = await e.ingest_native_media(
            path, kind=kind, resource_id=rid, source_namespace="native-fixture", source_ref=path.name, policy=policy
        )
        assert repeated.publication_status == "already_published"
        assert repeated.provider_call_count == 0 and len(provider.calls) == calls
        assert blobs(e) == saved and e.storage.resource_store.read_snapshot(rid) == state
        query = await e.search_media(
            MediaSearchRequest(
                TextInput("scripted semantic input", "query"),
                SearchScope(source_namespaces=("native-fixture",)),
                mode="semantic",
            )
        )
        assert len(query.results) == 1 and query.results[0]["resource_id"] == rid
        assert all(item["text"] is None for item in query.results[0]["evidence"])
        count = len(provider.calls)
        unit = state.batch.units[0]
        stored = await e.search_media(
            MediaSearchRequest(
                StoredUnitInput(unit.unit_id), SearchScope(source_namespaces=("native-fixture",)), mode="semantic"
            )
        )
        assert stored.results and len(provider.calls) == count
        query_policy = replace(policy, start_ms=500, end_ms=750, window_ms=250, hop_ms=250)
        raw = await e.search_native_media(
            path,
            kind=kind,
            policy=query_policy,
            scope=SearchScope(source_namespaces=("native-fixture",)),
            mode="hybrid",
        )
        assert raw.effective_mode == "semantic" and raw.results
        assert path.read_bytes() == before
        root = e.root
    finally:
        e.close()
    with MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider) as reopened:
        assert blobs(reopened) == saved
        assert reopened.storage.resource_store.read_snapshot(rid) == state


async def test_native_capability_refuses_before_source_decoder_catalog_and_no_frame_video_confusion(tmp_path):
    from mdrack.application.native_media import ingest_native_media

    provider = ScriptedProvider()
    provider.capabilities = RouteCapabilities((InputCapability(EncodingRole.DOCUMENT, "image", ("image",), 100000),))

    def forbidden():
        raise AssertionError("catalog created for unsupported route")

    for kind in ("audio", "video"):
        with pytest.raises(ValueError, match="unsupported_embedding_route"):
            await ingest_native_media(
                provider,
                forbidden,
                tmp_path / "absent",
                kind=kind,
                resource_id=resource_id("a", "b"),
                source_namespace="a",
                source_ref="b",
            )
    assert not provider.calls


async def test_native_source_mutation_and_invalid_vector_preserve_prior_state(tmp_path, media_files):
    audio, _ = media_files
    provider = ScriptedProvider()
    e = engine(tmp_path, provider)
    rid = resource_id("a", "audio")
    try:
        await e.ingest_native_media(
            audio,
            kind="audio",
            resource_id=rid,
            source_namespace="a",
            source_ref="clip",
            policy=NativeMediaPolicy(end_ms=1000),
        )
        state = e.storage.resource_store.read_snapshot(rid)
        saved = blobs(e)

        async def mutate():
            audio.write_bytes(audio.read_bytes() + b"changed")

        provider.hook = mutate
        with pytest.raises(ValueError, match="source_changed"):
            await e.ingest_native_media(
                audio,
                kind="audio",
                resource_id=rid,
                source_namespace="a",
                source_ref="clip",
                policy=NativeMediaPolicy(start_ms=1000, end_ms=2000),
            )
        assert e.storage.resource_store.read_snapshot(rid) == state and blobs(e) == saved
        provider.hook = None
        provider.space = replace(provider.space, fingerprint="mismatched-profile")

        async def invalid(value, role):
            return EmbeddingResult((float("nan"), 0.0), provider.space)

        provider.encode = invalid
        with pytest.raises(ValueError):
            await e.ingest_native_media(
                audio,
                kind="audio",
                resource_id=rid,
                source_namespace="a",
                source_ref="clip",
                policy=NativeMediaPolicy(end_ms=1000),
            )
        assert e.storage.resource_store.read_snapshot(rid) == state and blobs(e) == saved
    finally:
        e.close()
