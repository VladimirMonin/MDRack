"""Role/input route isolation over real CPU preparation, publication and SQLite.

Scripted vectors prove routing, not model relevance. No replacement search executor.
"""
from dataclasses import replace

import pytest

from mdrack.application.manifest import encode_prepared_resource_manifest
from mdrack.application.media_search import MediaSearchRequest, StoredUnitInput
from mdrack.application.prepared_enrichment import artifact_digest
from mdrack.domain.embedding_inputs import (
    AudioInput,
    EmbeddingResult,
    EncodingRole,
    InputCapability,
    RouteCapabilities,
    TextInput,
)
from mdrack.domain.native_media import NativeMediaPolicy
from mdrack_core import Facet, ResourceFacet, SearchScope
from mdrack_media import resource_id
from tests.integration.test_native_temporal import ScriptedProvider, blobs, engine
from tests.integration.test_native_temporal import media_files as media_files

POLICY = NativeMediaPolicy(end_ms=500, window_ms=500, hop_ms=500, frame_step_ms=500, max_width=32, max_height=24)


class RouteProvider(ScriptedProvider):
    async def encode(self, value, role):
        result = await super().encode(value, role)
        # Undeclared image/video candidates outrank admitted audio without a scope.
        if isinstance(value, AudioInput) and role == EncodingRole.DOCUMENT:
            return EmbeddingResult((0.6, 0.8), self.space)
        return result


async def seed(e, provider, media_files):
    found, values = {}, {}
    for kind in ('frames', 'audio', 'video'):
        found[kind] = await e.ingest_native_media(
            media_files[0 if kind == 'audio' else 1], kind=kind,
            resource_id=resource_id('routes', kind), source_namespace='routes', source_ref=kind, policy=POLICY,
        )
        assert found[kind].publication_status == 'published'
        values[kind] = provider.calls[-1][0]
    return found, values


def query_for(kind, found, values):
    if kind == 'text':
        return TextInput('controlled route query', 'route-query')
    if kind.startswith('stored-'):
        return StoredUnitInput(found[kind.removeprefix('stored-')].unit_ids[0])
    return values[kind]


def modalities(result):
    return {e['modality'] for item in result.results for e in item['evidence']}


@pytest.mark.parametrize('kind', ['text', 'frames', 'audio', 'video', 'stored-frames', 'stored-audio', 'stored-video'])
@pytest.mark.parametrize('mode', ['semantic', 'hybrid'])
async def test_current_route_default_explicit_and_cross_modal_independent_rows(tmp_path, media_files, kind, mode):
    provider = RouteProvider()
    with engine(tmp_path, provider) as e:
        found, values = await seed(e, provider, media_files)
        query = query_for(kind, found, values)
        modality = ('image' if kind == 'stored-frames' else kind.removeprefix('stored-')) if isinstance(
            query, StoredUnitInput
        ) else query.modality
        role = EncodingRole.SIMILARITY if isinstance(query, StoredUnitInput) else EncodingRole.QUERY
        other = 'video' if modality == 'text' else 'text'
        other_role = EncodingRole.QUERY if role == EncodingRole.SIMILARITY else EncodingRole.SIMILARITY
        route = InputCapability(role, modality, ('audio',), 2 * 1024 * 1024, 5000)
        unrelated = (
            InputCapability(EncodingRole.DOCUMENT, modality, ('text', 'image', 'audio', 'video'), 2 * 1024 * 1024),
            InputCapability(role, other, ('video',), 2 * 1024 * 1024),
            InputCapability(other_role, modality, ('image',), 2 * 1024 * 1024),
        )
        provider.capabilities = RouteCapabilities((route,), True)
        request = MediaSearchRequest(query, mode=mode, target='unit', limit=1, candidate_budget=1)
        before = blobs(e)
        baseline = await e.search_media(request)
        assert modalities(baseline) == {'audio'}
        assert baseline.results[0]['resource_id'] == found['audio'].resource_id
        for extra in unrelated:
            provider.capabilities = RouteCapabilities((route, extra), True)
            assert (await e.search_media(request)).to_dict() == baseline.to_dict()
        provider.capabilities = RouteCapabilities((route, *unrelated), True)
        calls = len(provider.calls)
        explicit = await e.search_media(replace(request, scope=SearchScope(modalities=('audio',))))
        assert explicit.to_dict() == baseline.to_dict()
        assert len(provider.calls) == calls + (0 if isinstance(query, StoredUnitInput) else 1)
        calls = len(provider.calls)
        with pytest.raises(ValueError, match='unsupported_embedding_route'):
            await e.search_media(replace(request, scope=SearchScope(modalities=('audio', 'video')), allow_partial=True))
        assert len(provider.calls) == calls
        # An unrelated role/input, even with a common complete space, is not a route.
        provider.capabilities = RouteCapabilities(unrelated, True)
        with pytest.raises(ValueError, match='unsupported_embedding_route'):
            await e.search_media(request)
        assert len(provider.calls) == calls
        assert blobs(e) == before and not e.storage.resource_store.connection.in_transaction


@pytest.mark.parametrize('target', ['resource', 'unit'])
async def test_route_filter_composes_with_all_scope_fields_before_limits_and_reopen(tmp_path, media_files, target):
    provider = RouteProvider()
    with engine(tmp_path, provider) as e:
        found, _ = await seed(e, provider, media_files)
        # A numerically closer, but namespace-excluded, audio resource.
        excluded = await e.ingest_native_media(
            media_files[0], kind='audio', resource_id=resource_id('excluded', 'audio'),
            source_namespace='excluded', source_ref='audio', policy=POLICY,
        )
        wanted = found['audio'].resource_id
        store = e.storage.resource_store
        batch = store.read_snapshot(wanted).batch
        selected = Facet('route-test', 'selected')
        other = Facet('route-test', 'other')
        batch = replace(batch, facets=(ResourceFacet(wanted, selected, 'manual'),))
        e.import_resource_manifest(encode_prepared_resource_manifest(batch))
        excluded_batch = store.read_snapshot(excluded.resource_id).batch
        e.import_resource_manifest(encode_prepared_resource_manifest(replace(
            excluded_batch, vectors=tuple(replace(v, vector=(1.0, 0.0)) for v in excluded_batch.vectors),
        )))
        # Real prepared PNG from the public CPU frame producer. Without narrowing,
        # a closer out-of-route/out-of-scope candidate consumes the single slot.
        image = next(v for v, role in provider.calls if v.modality == 'image')
        unrestricted = await e.search_media(MediaSearchRequest(
            image, target=target, mode='semantic', limit=1, candidate_budget=1,
        ))
        assert unrestricted.results and unrestricted.results[0]['resource_id'] != wanted
        provider.capabilities = RouteCapabilities((InputCapability(EncodingRole.QUERY, 'image', ('audio',), 2000000),))
        scope = SearchScope(
            source_namespaces=('routes',), resource_kinds=(batch.resource.resource_kind,),
            media_types=(batch.resource.media_type,),
            representation_kinds=(batch.representations[0].representation_kind,),
            unit_kinds=(batch.units[0].unit_kind,),
            facets_any=(selected,), facets_all=(selected,), facets_none=(other,),
        )
        request = MediaSearchRequest(image, scope, target=target, mode='semantic', limit=1, candidate_budget=1)
        before = blobs(e)
        result = await e.search_media(request)
        assert len(result.results) == 1 and result.results[0]['resource_id'] == wanted
        assert modalities(result) == {'audio'}
        explicit = await e.search_media(replace(request, scope=replace(scope, modalities=('audio',))))
        assert explicit.to_dict() == result.to_dict()
        for field, value in [
            ('source_namespaces', ('absent',)), ('representation_kinds', ('absent',)),
            ('facets_all', (other,)), ('facets_none', (selected,)),
        ]:
            assert not (await e.search_media(replace(request, scope=replace(scope, **{field: value})))).results
        root = e.root
        assert blobs(e) == before
    from mdrack.config.models import MDRackConfig
    from mdrack.public_api import MDRackEngine
    with MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider) as reopened:
        assert (await reopened.search_media(request)).to_dict() == result.to_dict()
        assert blobs(reopened) == before


async def test_lexical_only_does_not_require_or_union_embedding_routes(tmp_path, media_files):
    import json

    provider = RouteProvider()
    with engine(tmp_path, provider) as e:
        found, _ = await seed(e, provider, media_files)
        rid = found['frames'].resource_id
        payload = {
            'contract': 'mdrack.prepared-enrichment', 'version': 1,
            'binding': e.prepared_enrichment_binding(rid), 'artifact_revision': 'manual-r1',
            'producer': 'fixture-editor', 'recipe': 'fixture-text', 'manual': True,
            'artifact': 'routelexicalword', 'artifact_sha256': artifact_digest('routelexicalword'),
            'dependencies': [], 'dense': None,
        }
        ready = tmp_path / 'prepared-text.json'
        ready.write_text(json.dumps(payload))
        e.enrich_prepared(rid, ready, kind='text', purpose='fixture/manual')
        provider.capabilities = RouteCapabilities(())
        before = blobs(e)
        calls = len(provider.calls)
        request = MediaSearchRequest(TextInput('routelexicalword', 'lexical-query'), mode='text')
        assert modalities(await e.search_media(request)) == {'text'}
        assert not (await e.search_media(replace(request, scope=SearchScope(modalities=('audio',))))).results
        assert len(provider.calls) == calls and blobs(e) == before
