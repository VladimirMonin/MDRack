"""Offline ordinary-document integration through the prepared native port.

The synthetic provider here is deterministic byte-hash orchestration evidence,
not a model, CUDA, or quality claim. Real native execution is a separate smoke.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.application.compatibility import create_application_storage
from mdrack.application.media_search import MediaSearchRequest
from mdrack.application.source_resolution import SourceResolver
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import EncodingRole, InputCapability, RouteCapabilities, TextInput
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.ingestion.raw_source_provenance import RawSourceError
from mdrack.public_api import MDRackEngine
from mdrack_core import SearchScope


class CountingProvider(SyntheticPreparedProvider):
    def __init__(self, dimensions: int = 8) -> None:
        super().__init__(dimensions)
        self.calls: list[tuple[str, EncodingRole]] = []
        self.fail_after = -1

    async def encode(self, value, role):
        self.calls.append((value.modality, role))
        if self.fail_after >= 0 and len(self.calls) > self.fail_after:
            raise RuntimeError("PRIVATE_PROVIDER_SENTINEL")
        return await super().encode(value, role)


class OtherSpaceProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.space = replace(self.space, space_id="synthetic-native-other", fingerprint="other-space")


class TinyCapabilityProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.capabilities = RouteCapabilities((
            InputCapability(EncodingRole.DOCUMENT, "text", ("text",), 1),
            InputCapability(EncodingRole.QUERY, "text", ("text",), 1),
        ))


def document_text() -> str:
    return (
        "# Unicode документ\n\n"
        + ("Первый абзац: ёжик, café, 漢字, naïve. " * 80)
        + "\n\n## Второй раздел\n\n"
        + ("Второй абзац с другим текстом для проверки. " * 80)
        + "\n"
    )


def make_engine(root: Path, provider) -> MDRackEngine:
    return MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider)


@pytest.mark.asyncio
async def test_ordinary_document_unicode_batch_unchanged_reopen_and_query(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(document_text(), encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    provider = CountingProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await engine.ingest_native_text(
            source, source_namespace="fixture", source_ref="docs/unicode.md",
        )
        assert result.status == "success" and result.publication_status == "published"
        assert result.unchanged is False and result.model_call_count == result.chunk_count
        assert result.model_call_count >= 2 and len(result.unit_ids) > result.chunk_count
        assert all(role is EncodingRole.DOCUMENT for _, role in provider.calls)
        assert result.space_id == provider.space.space_id
        assert result.source_revision == "sha256:" + before
        read = engine.read_unit(result.unit_ids[0])
        assert read is not None and "ёжик" in str(read["text"])

        calls_before = len(provider.calls)
        repeated = await engine.ingest_native_text(
            source, source_namespace="fixture", source_ref="docs/unicode.md",
        )
        assert repeated.unchanged is True and repeated.publication_status == "already_published"
        assert repeated.model_call_count == 0 and len(provider.calls) == calls_before
        assert repeated.unit_ids == result.unit_ids
        resources = engine.storage.connection.execute(
            "SELECT COUNT(*) FROM core_resources WHERE resource_kind='document'"
        ).fetchone()[0]
        assert resources == 1
        engine.close()

    reopened = make_engine(tmp_path, provider)
    try:
        calls = len(provider.calls)
        found = await reopened.search_media(MediaSearchRequest(
            TextInput("ёжик café", "query"), SearchScope(modalities=("text",)), mode="semantic",
        ))
        assert len(provider.calls) == calls + 1
        assert found.space_id == provider.space.space_id
        assert found.results and found.results[0]["resource_id"] == result.resource_id
        evidence = found.results[0]["evidence"][0]
        assert evidence["source_revision"] == result.source_revision
        assert "ёжик" in str(evidence["text"])
    finally:
        reopened.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
async def test_incompatible_document_space_is_refused_before_model_and_writes(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(document_text(), encoding="utf-8")
    first = CountingProvider()
    with make_engine(tmp_path, first) as engine:
        result = await engine.ingest_native_text(source, source_namespace="fixture", source_ref="docs/a.md")
        manifest = engine.export_resource_manifest(result.resource_id)
    other = OtherSpaceProvider()
    with make_engine(tmp_path, other) as engine:
        with pytest.raises(IncompatibleEmbeddingProfileError):
            await engine.ingest_native_text(source, source_namespace="fixture", source_ref="docs/b.md")
        assert other.calls == []
        assert engine.storage.connection.execute(
            "SELECT COUNT(*) FROM core_resources WHERE resource_kind='document'"
        ).fetchone()[0] == 1
    with make_engine(tmp_path, first) as reopened:
        assert reopened.export_resource_manifest(result.resource_id) == manifest


@pytest.mark.asyncio
async def test_malformed_and_overlimit_refuse_without_graph_or_publication(tmp_path: Path) -> None:
    malformed = tmp_path / "bad.md"
    malformed.write_bytes(b"# valid\n\n\xff\xfe")
    provider = CountingProvider()
    with make_engine(tmp_path, provider) as engine:
        with pytest.raises(RawSourceError):
            await engine.ingest_native_text(malformed, source_namespace="fixture", source_ref="docs/bad.md")
        assert provider.calls == []
        assert engine.storage.connection.execute(
            "SELECT COUNT(*) FROM core_resources WHERE resource_kind='document'"
        ).fetchone()[0] == 0

    tiny = TinyCapabilityProvider()
    valid = tmp_path / "valid.md"
    valid.write_text(document_text(), encoding="utf-8")
    with make_engine(tmp_path, tiny) as engine:
        with pytest.raises(ValueError, match="embedding_input_too_large"):
            await engine.ingest_native_text(valid, source_namespace="fixture", source_ref="docs/valid.md")
        assert tiny.calls == []
        assert engine.storage.connection.execute(
            "SELECT COUNT(*) FROM core_resources WHERE resource_kind='document'"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_failure_preserves_previous_document_and_next_query(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    first.write_text(document_text(), encoding="utf-8")
    second = tmp_path / "second.md"
    second.write_text("# second\n\n" + ("different body " * 400), encoding="utf-8")
    provider = CountingProvider()
    with make_engine(tmp_path, provider) as engine:
        kept = await engine.ingest_native_text(first, source_namespace="fixture", source_ref="docs/first.md")
        calls = len(provider.calls)
        provider.fail_after = calls
        with pytest.raises(RuntimeError, match="PRIVATE_PROVIDER_SENTINEL"):
            await engine.ingest_native_text(second, source_namespace="fixture", source_ref="docs/second.md")
        provider.fail_after = -1
        assert engine.read_unit(kept.unit_ids[0]) is not None
        assert engine.storage.connection.execute(
            "SELECT COUNT(*) FROM core_resources WHERE resource_kind='document'"
        ).fetchone()[0] == 1
        found = await engine.search_media(MediaSearchRequest(
            TextInput("ёжик", "query"), SearchScope(modalities=("text",)), mode="semantic",
        ))
        assert found.results and found.results[0]["resource_id"] == kept.resource_id


def test_registered_cli_help_and_synthetic_ingest_query(tmp_path: Path) -> None:
    runner = CliRunner()
    help_result = runner.invoke(main, ["native", "text-ingest", "--help"])
    assert help_result.exit_code == 0
    assert "--source-namespace" in help_result.output and "--host-config" in help_result.output
    search_help = runner.invoke(main, ["native", "text-search", "--help"])
    assert search_help.exit_code == 0 and "--candidates" in search_help.output

    source = tmp_path / "source.md"
    source.write_text(document_text(), encoding="utf-8")
    ingest = runner.invoke(main, [
        "--root", str(tmp_path), "native", "text-ingest", str(source),
        "--source-namespace", "fixture", "--source-ref", "docs/cli.md", "--provider", "synthetic",
    ])
    assert ingest.exit_code == 0, ingest.output
    data = json.loads(ingest.output)["data"]
    assert data["schema"] == "mdrack.native-text-ingest.v1"
    assert data["model_call_count"] >= 2 and data["unit_ids"]

    query = runner.invoke(main, [
        "--root", str(tmp_path), "native", "text-search", "--text", "ёжик",
        "--candidates", "text", "--provider", "synthetic", "--mode", "semantic",
    ])
    assert query.exit_code == 0, query.output
    found = json.loads(query.output)["data"]
    assert found["results"] and found["results"][0]["resource_id"] == data["resource_id"]
    assert found["space_id"] == data["space_id"]


@pytest.mark.asyncio
async def test_explicit_native_space_and_provenance_survive_projection(tmp_path: Path) -> None:
    from mdrack.application.compatibility import prepared_file_to_resource_batch
    from mdrack.application.indexing import IndexingService
    from mdrack.application.vector_values import value_policy_from_space_metadata
    from mdrack_core import EmbeddingSpaceRecord

    source = tmp_path / "source.md"
    source.write_text("# t\n\nbody", encoding="utf-8")
    storage = create_application_storage(tmp_path, MDRackConfig(), create=True)
    try:
        service = IndexingService(tmp_path, MDRackConfig(), storage, provider=None, root_id="fixture")
        prepared = service.prepare_selected_document(
            source_ref="docs/space.md", content="# t\n\nbody", document_logical_id="doc-space",
            record_id="record-space", run_id="run-space",
        )
        space = EmbeddingSpaceRecord(
            "explicit-space", 8, "cosine", "explicit-fingerprint",
            {"vector_codec": "ieee754-f32-le-v1", "vector_value_policy": "ieee754-f32-canonical-v1"},
        )
        prepared = replace(
            prepared, vectors=((1.0,) + (0.0,) * 7,), embedding_space=space,
            source_provenance={"provenance": {"raw_source_sha256": "sha256:" + "a" * 64}},
        )
        batch = prepared_file_to_resource_batch(prepared)
        assert batch.spaces == (space,)
        assert value_policy_from_space_metadata(batch.spaces[0].metadata) == "ieee754-f32-canonical-v1"
        assert batch.resource.metadata["provenance"]["raw_source_sha256"] == "sha256:" + "a" * 64
        assert batch.representations[0].producer_fingerprint != ""
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_stable_unchanged_replay_does_not_write_or_call(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(document_text(), encoding="utf-8")
    provider = CountingProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await engine.ingest_native_text(
            source, source_namespace="fixture", source_ref="docs/stable.md",
        )
        before = engine.storage.resource_store.read_snapshot(result.resource_id)
        calls = len(provider.calls)
        repeated = await engine.ingest_native_text(
            source, source_namespace="fixture", source_ref="docs/stable.md",
        )
        after = engine.storage.resource_store.read_snapshot(result.resource_id)
        assert repeated.unchanged is True and repeated.model_call_count == 0
        assert len(provider.calls) == calls
        assert after == before  # no write, no state-token advance
        assert repeated.unit_ids == result.unit_ids


@pytest.mark.asyncio
async def test_unchanged_replay_after_concurrent_delete_is_not_success(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(document_text(), encoding="utf-8")
    provider = CountingProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await engine.ingest_native_text(
            source, source_namespace="fixture", source_ref="docs/delete.md",
        )
        calls = len(provider.calls)

        class DeleteAtCapture(SourceResolver):
            def selected_path(self, **kwargs):
                with make_engine(tmp_path, provider) as other:
                    other.delete_resource(result.resource_id)
                return super().selected_path(**kwargs)

        with pytest.raises(Exception):
            await engine.ingest_native_text(
                source, source_namespace="fixture", source_ref="docs/delete.md",
                resolver=DeleteAtCapture(),
            )
        after = engine.storage.resource_store.read_snapshot(result.resource_id)
        assert after.state.status == "deleted"
        assert len(provider.calls) == calls
        with make_engine(tmp_path, provider) as reopened:
            assert reopened.read_unit(result.unit_ids[0]) is None
            found = await reopened.search_media(MediaSearchRequest(
                TextInput("ёжик", "query"), SearchScope(modalities=("text",)), mode="semantic",
            ))
            assert not found.results


@pytest.mark.asyncio
async def test_unchanged_replay_after_concurrent_withdraw_is_not_success(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(document_text(), encoding="utf-8")
    provider = CountingProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await engine.ingest_native_text(
            source, source_namespace="fixture", source_ref="docs/withdraw.md",
        )
        calls = len(provider.calls)

        class WithdrawAtCapture(SourceResolver):
            def selected_path(self, **kwargs):
                with make_engine(tmp_path, provider) as other:
                    other.withdraw_resource(result.resource_id)
                return super().selected_path(**kwargs)

        with pytest.raises(Exception):
            await engine.ingest_native_text(
                source, source_namespace="fixture", source_ref="docs/withdraw.md",
                resolver=WithdrawAtCapture(),
            )
        after = engine.storage.resource_store.read_snapshot(result.resource_id)
        assert after.state.status == "live_without_projections" and after.batch is None
        assert len(provider.calls) == calls


@pytest.mark.asyncio
async def test_unchanged_replay_after_concurrent_recreate_is_not_stale_success(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(document_text(), encoding="utf-8")
    provider = CountingProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await engine.ingest_native_text(
            source, source_namespace="fixture", source_ref="docs/recreate.md",
        )
        before = engine.storage.resource_store.read_snapshot(result.resource_id)
        manifest = engine.export_resource_manifest(result.resource_id)
        calls = len(provider.calls)

        class RecreateAtCapture(SourceResolver):
            def selected_path(self, **kwargs):
                with make_engine(tmp_path, provider) as other:
                    other.delete_resource(result.resource_id)
                    other.import_resource_manifest(manifest)
                return super().selected_path(**kwargs)

        with pytest.raises(Exception):
            await engine.ingest_native_text(
                source, source_namespace="fixture", source_ref="docs/recreate.md",
                resolver=RecreateAtCapture(),
            )
        after = engine.storage.resource_store.read_snapshot(result.resource_id)
        assert after.state.status == "published"
        assert after.state.lifecycle_id != before.state.lifecycle_id
        assert len(provider.calls) == calls
