"""Native image integration: prepared input, original locator and zero-call reuse."""

from __future__ import annotations

import base64
import json
import os
import struct
import zlib

import pytest
from click.testing import CliRunner

from mdrack.application.media_search import MediaSearchRequest
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import EncodingRole, ImageInput, TextInput
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.ingestion.image_preprocessing import (
    ImagePreparationError,
    ImagePreprocessor,
    ImageProcessPolicy,
    ImageProcessRecipe,
    sha256_digest,
)
from mdrack.ingestion.raw_source_provenance import sha256_digest as source_digest
from mdrack.public_api import MDRackEngine
from mdrack_sqlite import SQLiteCatalog

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows WIC decoder")

JPEG_ORIENTATION_6 = base64.b64decode(
    "/9j/4QAiRXhpZgAASUkqAAgAAAABABIBAwABAAAABgAAAAAAAAD//gAPTGF2YzYzLjEuMTAxAP/bAEMACAQE"
    "BAQEBQUFBQUFBgYGBgYGBgYGBgYGBgcHBwgICAcHBwYGBwcICAgICQkJCAgICAkJCgoKDAwLCw4ODhERFP/E"
    "AFoAAQEAAAAAAAAAAAAAAAAAAAAHAQEBAQAAAAAAAAAAAAAAAAAABQcQAAEFAQAAAAAAAAAAAAAAAAAEAgWz"
    "NYMRAAEFAQEAAAAAAAAAAAAAAAAEBwU3tYWE/8AAEQgAAgAEAwESAAISAAMSAP/aAAwDAQACEQMRAD8AlE9r"
    "KOVTBPayjlUw1RkqwgOjpqwyVYQHR01ZQd+xJvwZ6YO/Yk34M9Mf/9k="
)


class PreparingSyntheticProvider(SyntheticPreparedProvider):
    def __init__(self, recipe=None, policy=None) -> None:
        super().__init__()
        self.preprocessor = ImagePreprocessor(recipe=recipe, policy=policy)
        self.prepared = []

    def prepare_image(self, content: bytes, media_type: str, source_revision: str | None = None) -> ImageInput:
        revision = source_revision or sha256_digest(content)
        prepared = self.preprocessor.prepare(content, media_type, revision)
        self.prepared.append(prepared)
        return ImageInput(prepared.content, prepared.media_type, prepared.identity)


@pytest.mark.asyncio
async def test_native_ingest_uses_original_locator_and_reuses_unchanged_derived(tmp_path) -> None:
    source = tmp_path / "ordinary.jpg"
    source.write_bytes(JPEG_ORIENTATION_6)
    provider = PreparingSyntheticProvider()
    with MDRackEngine(root=tmp_path, config=MDRackConfig(), prepared_embedding_provider=provider) as engine:
        result = await engine.ingest_native_image(
            source, resource_id="image", source_namespace="host", source_ref="photos/original.jpg"
        )
        assert len(provider.prepared) == 1
        prepared = provider.prepared[0]
        assert prepared.source_revision == sha256_digest(JPEG_ORIENTATION_6)
        assert prepared.identity != prepared.source_revision
        assert prepared.source_width == 4 and prepared.source_height == 2 and prepared.orientation == 6
        assert len(prepared.content) <= 1_048_576

        found = await engine.search_media(MediaSearchRequest(
            TextInput("a red image", "query"), mode="semantic", limit=5,
        ))
        assert found.results
        evidence = found.results[0]["evidence"][0]
        assert evidence["source_revision"] == source_digest(JPEG_ORIENTATION_6)
        locator = evidence["locator"]
        assert locator["kind"] == "whole_image"
        assert locator["payload"] == {"source_ref": "photos/original.jpg"}

        calls = len(provider.prepared)
        repeated = await engine.ingest_native_image(
            source, resource_id="image", source_namespace="host", source_ref="photos/original.jpg"
        )
        assert len(provider.prepared) == calls
        assert repeated.unit_ids == result.unit_ids
    assert source.read_bytes() == JPEG_ORIENTATION_6


@pytest.mark.asyncio
async def test_native_ingest_refuses_corrupt_before_publication(tmp_path) -> None:
    source = tmp_path / "broken.jpg"
    corrupt = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
    source.write_bytes(corrupt)
    provider = PreparingSyntheticProvider()
    with MDRackEngine(root=tmp_path, config=MDRackConfig(), prepared_embedding_provider=provider) as engine:
        with pytest.raises(Exception):
            await engine.ingest_native_image(
                source, resource_id="image", source_namespace="host", source_ref="photos/broken.jpg"
            )
        assert provider.prepared == []
        assert source.read_bytes() == corrupt
    path = tmp_path / ".mdrack" / "catalog.sqlite3"
    if path.exists():
        with SQLiteCatalog.open_readonly(path) as catalog:
            assert catalog.connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 0


def test_provider_prepare_image_is_not_a_model_call() -> None:
    provider = PreparingSyntheticProvider()
    value = provider.prepare_image(JPEG_ORIENTATION_6, "image/jpeg", sha256_digest(JPEG_ORIENTATION_6))
    assert isinstance(value, ImageInput) and value.media_type == "image/png"
    assert EncodingRole.DOCUMENT.value == "document"



def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _png(width: int, height: int, color=(200, 30, 40, 255)) -> bytes:
    rows = (b"\x00" + bytes(color) * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


@pytest.mark.asyncio
async def test_changed_recipe_forces_new_preparation_after_reopen(tmp_path) -> None:
    source = tmp_path / "ordinary.png"
    source.write_bytes(_png(64, 48))
    first = PreparingSyntheticProvider(ImageProcessRecipe(version="first-party-recipe-v1"))
    with MDRackEngine(root=tmp_path, config=MDRackConfig(), prepared_embedding_provider=first) as engine:
        await engine.ingest_native_image(
            source, resource_id="image", source_namespace="host", source_ref="photos/original.png"
        )
    second = PreparingSyntheticProvider(ImageProcessRecipe(version="first-party-recipe-v2"))
    with MDRackEngine(root=tmp_path, config=MDRackConfig(), prepared_embedding_provider=second) as engine:
        try:
            await engine.ingest_native_image(
                source, resource_id="image", source_namespace="host", source_ref="photos/original.png"
            )
        except (ValueError, ImagePreparationError):
            return
        assert len(second.prepared) == 1, "changed recipe disappeared from replay admission"


@pytest.mark.asyncio
async def test_tighter_policy_forces_validation_or_refusal_after_reopen(tmp_path) -> None:
    source = tmp_path / "ordinary.png"
    source.write_bytes(_png(640, 480))
    first = PreparingSyntheticProvider()
    with MDRackEngine(root=tmp_path, config=MDRackConfig(), prepared_embedding_provider=first) as engine:
        await engine.ingest_native_image(
            source, resource_id="image", source_namespace="host", source_ref="photos/original.png"
        )
    second = PreparingSyntheticProvider(policy=ImageProcessPolicy(max_dimension=100))
    with MDRackEngine(root=tmp_path, config=MDRackConfig(), prepared_embedding_provider=second) as engine:
        with pytest.raises((ValueError, ImagePreparationError)):
            await engine.ingest_native_image(
                source, resource_id="image", source_namespace="host", source_ref="photos/original.png"
            )
        assert second.prepared == []


@pytest.mark.parametrize("failure", ["corrupt", "dimension"])
def test_cli_preserves_preparation_error_and_next_operation(tmp_path, monkeypatch, failure: str) -> None:
    import mdrack.cli.commands.images as cli_images

    source = tmp_path / "source.png"
    original = b"\x89PNG\r\n\x1a\nnot-a-complete-png" if failure == "corrupt" else _png(100, 80)
    source.write_bytes(original)
    policy = ImageProcessPolicy(max_dimension=50) if failure == "dimension" else None
    provider = PreparingSyntheticProvider(policy=policy)

    def local_engine(ctx, provider_name, host_config=None):
        return MDRackEngine(root=tmp_path, config=MDRackConfig(), prepared_embedding_provider=provider), None

    monkeypatch.setattr(cli_images, "_native_engine", local_engine)
    runner = CliRunner()
    args = [
        "--root", str(tmp_path), "image", "native-ingest", str(source), "--resource-id", "image",
        "--source-namespace", "host", "--source-ref", "photos/original.png", "--provider", "external",
    ]
    result = runner.invoke(main, args)
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    expected = "IMAGE_DECODE_FAILED" if failure == "corrupt" else "IMAGE_DIMENSIONS_TOO_LARGE"
    assert payload["error"]["code"] == expected
    assert provider.prepared == [] and source.read_bytes() == original
    source.write_bytes(_png(20, 20))
    next_result = runner.invoke(main, args)
    assert next_result.exit_code == 0, next_result.stdout
