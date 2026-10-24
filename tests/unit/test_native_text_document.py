"""Offline v2 contract controls; scripted ports are NOT native/CUDA evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mdrack.domain.embedding_inputs import EncodingRole, ImageInput, TextInput
from mdrack.hosts.cuda_encoder import _ROLE_RECIPE, CUDAEncoderProvider
from mdrack.ports.embeddings import EmbeddingError
from tests.unit.test_cuda_encoder_host import config


def extended_config(path: Path) -> Path:
    p = config(path)
    doc = json.loads(p.read_bytes())
    doc["protocol"] = "mdrack-cuda-retrieval-v2"
    metadata = doc["encoder_profile"]["metadata"]
    metadata.update(protocol=doc["protocol"], role_recipe=_ROLE_RECIPE)
    doc["encoder_profile"]["fingerprint"] = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    p.write_text(json.dumps(doc))
    return p


@pytest.mark.parametrize("candidates", [("text",), ("image",), ("image", "text"), ()])
def test_v2_one_query_row_covers_union(tmp_path: Path, candidates: tuple[str, ...]) -> None:
    provider = CUDAEncoderProvider(extended_config(tmp_path))
    row = provider.capabilities.require(EncodingRole.QUERY, "text", candidates)
    assert row.candidate_modalities == ("image", "text")
    assert len([r for r in provider.capabilities.inputs if r.role is EncodingRole.QUERY]) == 1
    assert not provider.capabilities.reuse_document_vector
    assert provider.calls == 0


def test_prefixes_once_body_unchanged_and_utf8_bounds(tmp_path: Path) -> None:
    provider = CUDAEncoderProvider(extended_config(tmp_path))
    body = "  title: none | text: task: search result | query: e\u0301 Ж  "
    document, kind = provider._prepare(TextInput(body, "not-used-as-model-content"), EncodingRole.DOCUMENT)
    query, qkind = provider._prepare(TextInput(body, "other"), EncodingRole.QUERY)
    assert document == ("title: none | text: " + body).encode() and kind == "document"
    assert query == ("task: search result | query: " + body).encode() and qkind == "query"
    for role, prefix in [(EncodingRole.DOCUMENT, "title: none | text: "),
                         (EncodingRole.QUERY, "task: search result | query: ")]:
        limit = 65536 - len(prefix.encode())
        value = TextInput("a" * limit, "id")
        assert len(provider._prepare(value, role)[0]) == 65536
        with pytest.raises(ValueError, match="embedding_input_too_large"):
            provider._prepare(TextInput("Ж" * (limit // 2 + 1), "id"), role)


@pytest.mark.parametrize("role,modality", [(EncodingRole.QUERY, "image"), (EncodingRole.SIMILARITY, "text"),
                                         (EncodingRole.DOCUMENT, "audio"), (EncodingRole.DOCUMENT, "video")])
def test_no_unsupported_directions(tmp_path: Path, role: EncodingRole, modality: str) -> None:
    provider = CUDAEncoderProvider(extended_config(tmp_path))
    with pytest.raises(ValueError, match="unsupported_embedding_route"):
        provider.capabilities.require(role, modality)
    with pytest.raises(ValueError):
        provider._prepare(TextInput("body", "id"), "query")  # type: ignore[arg-type]
    assert provider.calls == 0


def test_v1_identity_roles_and_wire_are_not_rewritten(tmp_path: Path) -> None:
    olddir, newdir = tmp_path / "old", tmp_path / "new"
    olddir.mkdir()
    newdir.mkdir()
    path = config(olddir)
    old = CUDAEncoderProvider(path)
    again = CUDAEncoderProvider(path)
    new = CUDAEncoderProvider(extended_config(newdir))
    assert old.space == again.space and old.space != new.space
    assert old.space.metadata["producer"] == "mdrack-cuda-abi12-v1"
    assert new.space.metadata["producer"] == "mdrack-cuda-retrieval-v2"
    assert old._prepare(TextInput("body", "id"), EncodingRole.QUERY) == (
        b"task: search result | query: body", "query",
    )
    for role, candidates in [(EncodingRole.DOCUMENT, ("text",)), (EncodingRole.QUERY, ("text",)),
                             (EncodingRole.QUERY, ("image", "text"))]:
        with pytest.raises(ValueError):
            old.capabilities.require(role, "text", candidates)
    assert new.capabilities.require(EncodingRole.DOCUMENT, "text", ("text",))
    assert new.capabilities.require(EncodingRole.DOCUMENT, "image", ("image",))


@pytest.mark.parametrize("key,value", [("role", "query"), ("kind", "query"), ("pid", 99),
                                      ("operation", "0" * 32), ("formatted_input_sha256", "0" * 64),
                                      ("profile_fingerprint", "0" * 64), ("protocol", "old")])
def test_mode_binding_refuses_stale_partial_or_foreign_receipts(tmp_path: Path, key: str, value: object) -> None:
    binding = {"role": "document", "operation": "1" * 32, "formatted_input_sha256": "2" * 64,
               "profile_fingerprint": "3" * 64, "protocol": "mdrack-cuda-retrieval-v2"}
    result = {**binding, "kind": "document", "pid": 7, "dimensions": 768, "cuda_device": "CUDA0"}
    result[key] = value
    (tmp_path / "stdout").write_text(json.dumps(result) + '\n{"owned_cleanup_completed":true}\n')
    (tmp_path / "stderr").write_bytes(b"")
    with pytest.raises(ValueError):
        CUDAEncoderProvider._result(tmp_path, tmp_path / "wire", "document", 7, binding)


@pytest.mark.asyncio
async def test_v2_closed_drift_and_image_query_are_pre_dispatch(tmp_path: Path) -> None:
    path = extended_config(tmp_path)
    provider = CUDAEncoderProvider(path)
    with pytest.raises(ValueError):
        await provider.encode(ImageInput(b"fake", "image/png", "id"), EncodingRole.QUERY)
    await provider.aclose()
    with pytest.raises(EmbeddingError, match="closed"):
        await provider.encode(TextInput("body", "id"), EncodingRole.DOCUMENT)
    other = CUDAEncoderProvider(path)
    (tmp_path / "encoder.cpp").write_bytes(b"changed")
    with pytest.raises(EmbeddingError, match="identity_changed"):
        await other.encode(TextInput("body", "id"), EncodingRole.DOCUMENT)
    assert provider.calls == other.calls == 0


@pytest.mark.asyncio
async def test_real_adapter_formatting_normal_B_import_reopen_and_D_union(tmp_path: Path) -> None:
    from mdrack.application.media_search import MediaSearchRequest
    from mdrack.config.models import MDRackConfig
    from mdrack.domain.embedding_inputs import EmbeddingResult
    from mdrack.eval.native_corpus import validate_native_bundle
    from mdrack.eval.native_runner import _projection
    from mdrack.public_api import MDRackEngine
    from mdrack_core import SearchScope
    from mdrack_sqlite import SQLiteCatalog
    from tests.unit.test_eval_native_runner import fixture

    class SoftwareEncoder(CUDAEncoderProvider):
        async def _execute(self, data: bytes, kind: str) -> EmbeddingResult:
            self.calls += 1
            self.wire.append((data, kind))
            return EmbeddingResult((1.0,) + (0.0,) * 767, self.space)

    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    provider = SoftwareEncoder(extended_config(cfgdir))
    provider.wire = []
    data = tmp_path / "media"
    resource = validate_native_bundle(fixture(data), media_root=data).resources[0]
    root = tmp_path / "B"
    (root / ".mdrack").mkdir(parents=True)
    with SQLiteCatalog.create_v2(root / ".mdrack/catalog.sqlite3"):
        pass
    engine = MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider)
    try:
        await _projection(engine, resource, provider)
        assert provider.wire[0] == (("title: none | text: " + resource.projection.content).encode(), "document")
        before = engine.storage.resource_store.read_snapshot(resource.resource_id).batch
        query = MediaSearchRequest(TextInput("query", "id"), SearchScope(modalities=("text",)), mode="semantic")
        found = await engine.search_media(query)
        assert found.results and found.space_id == provider.space.space_id
        unit_id = found.results[0]["evidence"][0]["unit_id"]
        assert engine.read_unit(unit_id)["text"] == resource.projection.content
        engine.close()
        engine = MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider)
        assert engine.storage.resource_store.read_snapshot(resource.resource_id).batch == before
        assert (await engine.search_media(query)).results
        # Actual existing enrichment contract and hybrid union, not another text resource.
        text = "manualunionmarker"
        payload = {"contract": "mdrack.prepared-enrichment", "version": 1,
                   "binding": engine.prepared_enrichment_binding(resource.resource_id),
                   "artifact_revision": "manual-1", "producer": "caller-manual", "recipe": "manual-text-v1",
                   "manual": True, "artifact": text, "artifact_sha256": hashlib.sha256(
                       json.dumps(text, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                   "dependencies": [], "dense": None}
        artifact = tmp_path / "manual.json"
        artifact.write_text(json.dumps(payload))
        calls = provider.calls
        engine.enrich_prepared(resource.resource_id, artifact, kind="text", purpose="manual:whole",
                               lexical=True, dense=False)
        assert provider.calls == calls
        after = engine.storage.resource_store.read_snapshot(resource.resource_id).batch
        assert after.resource == before.resource and after.vectors == before.vectors and after.spaces == before.spaces
        union = await engine.search_media(MediaSearchRequest(TextInput(text, "union"),
                          SearchScope(modalities=("image", "text")), mode="hybrid"))
        assert union.effective_mode == "hybrid" and union.results and provider.calls == calls + 1
        assert provider.wire[-1] == (b"task: search result | query: manualunionmarker", "query")
    finally:
        engine.close()
        await provider.aclose()
