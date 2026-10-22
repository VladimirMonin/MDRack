"""W2 real fresh app-v2/files/child CLI; synthetic encoder is not a model."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import struct
import subprocess
import sys
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack.application.media_search import MediaSearchRequest, StoredUnitInput
from mdrack.application.source_resolution import SourceResolver
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import EmbeddingResult, EncodingRole, ImageInput, TextInput
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.prepared import LegacyTextEmbeddingAdapter
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.ingestion.raw_source_provenance import RawInputBudget, RawSourceError, sha256_digest
from mdrack.public_api import MDRackEngine
from mdrack_core import SearchScope
from mdrack_sqlite import SQLiteCatalog


# Decodable 1x1 PNG, no third-party decoder needed by this route.
def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


PNG = (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
       + _chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + _chunk(b'IEND', b''))


class ControlledProvider(SyntheticPreparedProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = []
        self.failure = None
        self.hook = None

    async def encode(self, value, role):
        self.calls.append((value.modality, role))
        if self.hook:
            self.hook()
        if self.failure == 'raise':
            raise RuntimeError('PRIVATE_PROVIDER_SENTINEL')
        if self.failure == 'cancel':
            raise asyncio.CancelledError()
        if self.failure == 'space':
            return EmbeddingResult((1.0,) * 8, replace(self.space, fingerprint='other'))
        if self.failure == 'nan':
            return EmbeddingResult((float('nan'),) * 8, self.space)
        return await super().encode(value, role)


def make_engine(root: Path, provider=None):
    return MDRackEngine(root=root, config=MDRackConfig(),
                        prepared_embedding_provider=provider or SyntheticPreparedProvider())


async def ingest(engine, path):
    return await engine.ingest_native_image(path, resource_id='image', source_namespace='host', source_ref='a.png')


@pytest.mark.asyncio
async def test_native_ingest_search_read_reopen_and_child(tmp_path: Path) -> None:
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    before = hashlib.sha256(PNG).hexdigest()
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await ingest(engine, source)
        read = engine.read_unit(result.unit_ids[0])
        assert read['text'] is None and read['modality'] == 'image'
        request = MediaSearchRequest(ImageInput(PNG, 'image/png', 'query'), SearchScope(modalities=('image',)))
        found = await engine.search_media(request)
        assert found.requested_mode == 'hybrid' and found.effective_mode == 'semantic'
        assert not found.degradations and found.results[0]['resource_id'] == 'image'
        assert found.space_id == provider.space.space_id and found.profile_fingerprint == provider.space.fingerprint
        assert set(found.to_dict()) == {
            'schema', 'target', 'requested_mode', 'effective_mode', 'space_id', 'profile_fingerprint',
            'basis_unit_id', 'score_kind', 'results', 'total_count', 'degraded', 'degradations',
        }
        with pytest.raises(TypeError):
            found.results[0]['resource_id'] = 'mutated'
        evidence = found.results[0]['evidence'][0]
        assert evidence['text'] is None and evidence['source_revision'] == sha256_digest(PNG)
        assert evidence['locator']['payload']['source_ref'] == 'a.png'
        text_result = await engine.search_media(replace(request, query=TextInput('scene', 'query')))
        assert text_result.results[0]['resource_id'] == 'image'
        count = len(provider.calls)
        similar = await engine.search_media(replace(request, query=StoredUnitInput(result.unit_ids[0])))
        assert similar.results and len(provider.calls) == count
        assert similar.basis_unit_id == result.unit_ids[0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    path = tmp_path / '.mdrack' / 'catalog.sqlite3'
    with SQLiteCatalog.open_readonly(path) as catalog:
        assert catalog.connection.execute('SELECT COUNT(*) FROM core_search_units_fts').fetchone()[0] == 0
        assert catalog.read_unit(result.unit_ids[0]).text is None
    with make_engine(tmp_path) as reopened:
        assert reopened.read_unit(result.unit_ids[0])['text'] is None
        assert reopened._storage is None  # read owns a reader, not cached writer
        assert (await reopened.search_media(request)).results
    cmd = [sys.executable, '-B', '-m', 'mdrack', '--root', str(tmp_path), 'image', 'native-search',
           '--image-file', str(source), '--provider', 'synthetic']
    child = subprocess.run(cmd, text=True, capture_output=True, env=os.environ.copy(), timeout=30)
    assert child.returncode == 0, child.stderr + child.stdout
    payload = json.loads(child.stdout)
    assert payload['data'] == found.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['raise', 'nan', 'space', 'cancel', 'changed'])
async def test_failure_preserves_old_graph_and_next_query(tmp_path: Path, failure: str) -> None:
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        original = await ingest(engine, source)
        old_graph = engine.export_resource_manifest('image')
        provider.failure = failure
        if failure == 'changed':
            provider.hook = lambda: source.write_bytes(PNG + b'changed')
        error = asyncio.CancelledError if failure == 'cancel' else Exception
        with pytest.raises(error):
            await ingest(engine, source)
        provider.failure = None
        provider.hook = None
        assert engine.read_unit(original.unit_ids[0])['text'] is None
        assert engine.export_resource_manifest('image') == old_graph
        found = await engine.search_media(MediaSearchRequest(ImageInput(PNG, 'image/png', 'query')))
        assert found.results[0]['resource_id'] == 'image'


@pytest.mark.asyncio
async def test_changed_source_is_not_logged_as_a_completed_commit(tmp_path: Path, caplog) -> None:
    import logging
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    caplog.set_level(logging.INFO)
    with make_engine(tmp_path, provider) as engine:
        original = await ingest(engine, source)
        caplog.clear()
        provider.hook = lambda: source.write_bytes(PNG + b'changed')
        with pytest.raises(RawSourceError):
            await ingest(engine, source)
        messages = [record.getMessage() for record in caplog.records]
        assert not any(message.startswith('core.index.completed') for message in messages)
        assert any(message.startswith('core.index.failed') for message in messages)
        assert engine.read_unit(original.unit_ids[0])['text'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('lexical,dense', [(False, False), (True, False), (False, True), (True, True)])
async def test_initial_prepared_derived_text_policy_without_extraction(
    tmp_path: Path, lexical: bool, dense: bool,
) -> None:
    from mdrack.ingestion.images import ExtractedImageText
    provider = ControlledProvider()
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    artifact = ExtractedImageText('ocr_text', 'needle', 'caller-prepared-v1')
    with make_engine(tmp_path, provider) as engine:
        class ForbiddenExtractor:
            async def extract(self, *args, **kwargs):
                pytest.fail('new OCR is forbidden in native ingestion')
        engine.image_extractor = ForbiddenExtractor()
        result = await engine.ingest_native_image(source, resource_id='image', source_namespace='host',
                                                  source_ref='a.png', derived_text=(artifact,),
                                                  derived_lexical=lexical, derived_dense=dense)
        units = [engine.read_unit(unit_id) for unit_id in result.unit_ids]
        text_units = [unit for unit in units if unit['modality'] == 'text']
        assert bool(text_units) == (lexical or dense)
        assert all(unit['text'] == ('needle' if lexical else None) for unit in text_units)
        assert provider.calls.count(('image', EncodingRole.DOCUMENT)) == 1
        assert provider.calls.count(('text', EncodingRole.DOCUMENT)) == int(dense)
        request = MediaSearchRequest(TextInput('needle', 'q'), mode='text')
        assert bool((await engine.search_media(request)).results) == lexical
        assert artifact.text == 'needle'  # 00 leaves the read-only artifact with caller; no durable-host claim
    assert source.read_bytes() == PNG


@pytest.mark.asyncio
async def test_lmstudio_text_transport_refuses_images_before_any_side_effect(tmp_path: Path, monkeypatch) -> None:
    provider = FakeEmbeddingProvider(dimensions=8)
    profile = embedding_profile_from_config(MDRackConfig(), provider)
    adapter = LegacyTextEmbeddingAdapter(provider, profile)
    def forbidden(*args, **kwargs):
        pytest.fail('filesystem or DB touched for unsupported route')
    monkeypatch.setattr(SourceResolver, 'image', forbidden)
    with make_engine(tmp_path, adapter) as engine:
        monkeypatch.setattr(engine, '_application_storage', forbidden)
        with pytest.raises(ValueError, match='unsupported_embedding_route'):
            await ingest(engine, tmp_path / 'does-not-exist')
        with pytest.raises(ValueError, match='unsupported_embedding_route'):
            await engine.search_media(MediaSearchRequest(ImageInput(PNG, 'image/png', 'q')))
    assert not (tmp_path / '.mdrack').exists()


def test_resolver_authority_revision_budget_range_and_cleanup(tmp_path: Path) -> None:
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    resolver = SourceResolver({('host', 'a.png'): source})
    with resolver.image(namespace='host', source_ref='a.png', expected_revision=sha256_digest(PNG)) as resolved:
        assert resolved[2].content == PNG
        transient = resolved[1].temporary_path
        assert transient.exists()
    assert not transient.exists()
    with pytest.raises(ValueError, match='source_authority_missing'):
        with resolver.image(namespace='other', source_ref='a.png'):
            pass
    for kwargs in ({'expected_revision': 'wrong'}, {'budget': RawInputBudget(max_source_bytes=1)},
                   {'byte_range': (0, 1)}, {'source_ref': 'https://private.invalid/a.png'}):
        args = {'namespace': 'host', 'source_ref': 'a.png', **kwargs}
        with pytest.raises((ValueError, RawSourceError)):
            with resolver.image(**args):
                pass
    source.unlink()
    with pytest.raises(RawSourceError):
        with resolver.image(namespace='host', source_ref='a.png'):
            pass


@pytest.mark.asyncio
async def test_legacy_adapter_uses_query_method_and_preserves_identity() -> None:
    class Provider(FakeEmbeddingProvider):
        def __init__(self):
            super().__init__(dimensions=8)
            self.queries = 0
            self.documents = 0
        async def embed_query(self, text, profile='default'):
            self.queries += 1
            return [1.0] * 8
        async def embed(self, texts, profile='default'):
            self.documents += 1
            return [[1.0] * 8 for _ in texts]
    provider = Provider()
    profile = embedding_profile_from_config(MDRackConfig(), provider)
    adapter = LegacyTextEmbeddingAdapter(provider, profile)
    await adapter.encode(TextInput('q', 'q'), EncodingRole.QUERY)
    assert provider.queries == 1 and provider.documents == 0
    await adapter.encode(TextInput('d', 'd'), EncodingRole.DOCUMENT)
    assert provider.documents == 1
    from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
    assert adapter.space == CanonicalTextEmbeddingSpace(profile).record
