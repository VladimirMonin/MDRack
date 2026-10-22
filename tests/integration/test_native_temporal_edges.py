"""Cross-track/publication/budget/cancellation local-component witnesses."""

import asyncio
import json
from dataclasses import replace

import pytest

from mdrack.application.media_search import MediaSearchRequest
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import TextInput
from mdrack.domain.native_media import NativeMediaPolicy
from mdrack.public_api import MDRackEngine
from mdrack_core import LexicalBranch, SearchScope
from mdrack_media import resource_id
from tests.integration.test_native_temporal import ScriptedProvider, blobs, engine
from tests.integration.test_native_temporal import media_files as media_files


async def test_native_tracks_shared_resource_enrichment_real_cas_and_lifecycle(tmp_path, media_files):
    from mdrack.application.manifest import encode_prepared_resource_manifest
    from mdrack.application.prepared_enrichment import artifact_digest

    _, source = media_files
    provider = ScriptedProvider()
    e = engine(tmp_path, provider)
    rid = resource_id("shared", "video")
    other = MDRackEngine(root=e.root, config=MDRackConfig(), prepared_embedding_provider=provider)
    try:
        base = NativeMediaPolicy(end_ms=2000, max_width=32, max_height=24)
        await e.ingest_native_media(
            source, kind="frames", resource_id=rid, source_namespace="shared", source_ref="clip", policy=base
        )
        native_blobs = blobs(e)
        payload = {
            "contract": "mdrack.prepared-enrichment",
            "version": 1,
            "binding": e.prepared_enrichment_binding(rid),
            "artifact_revision": "manual-r1",
            "producer": "fixture-editor",
            "recipe": "fixture-text",
            "manual": True,
            "artifact": "retainednativeindigoword",
            "artifact_sha256": artifact_digest("retainednativeindigoword"),
            "dependencies": [],
            "dense": None,
        }
        ready = tmp_path / "ready.json"
        ready.write_text(json.dumps(payload))
        calls = len(provider.calls)
        e.enrich_prepared(rid, ready, kind="text", purpose="fixture/manual")
        assert len(provider.calls) == calls and all(blobs(e)[key] == data for key, data in native_blobs.items())
        assert e.storage.resource_store.search_lexical(
            LexicalBranch("q", "retainednativeindigoword"), scope=SearchScope()
        )
        fired = []

        async def concurrent_metadata():
            assert not e.storage.resource_store.connection.in_transaction
            if not fired:
                current = other.storage.resource_store.read_snapshot(rid).batch
                other.storage.resource_store.replace_resource(
                    replace(
                        current,
                        resource=replace(
                            current.resource, metadata={**current.resource.metadata, "independent_user_value": "keep"}
                        ),
                    )
                )
                fired.append(True)

        provider.hook = concurrent_metadata
        added = await e.ingest_native_media(
            source, kind="audio", resource_id=rid, source_namespace="shared", source_ref="clip", policy=base
        )
        assert added.attempt_count == 2 and added.provider_call_count == 2 and fired
        provider.hook = None
        assert all(blobs(e)[key] == data for key, data in native_blobs.items())
        after = e.storage.resource_store.read_snapshot(rid)
        assert after.batch.resource.metadata["independent_user_value"] == "keep"
        assert {"image", "audio", "text"} == {u.modality for u in after.batch.units}
        await e.ingest_native_media(
            source, kind="video", resource_id=rid, source_namespace="shared", source_ref="clip", policy=base
        )
        latest = e.storage.resource_store.read_snapshot(rid)
        assert {"image", "audio", "video", "text"} == {u.modality for u in latest.batch.units}
        grouped = await e.search_media(
            MediaSearchRequest(TextInput("scripted", "q"), target="resource", mode="semantic", evidence_limit=2)
        )
        units = await e.search_media(MediaSearchRequest(TextInput("scripted", "q"), target="unit", mode="semantic"))
        assert len(grouped.results) == 1 and len(grouped.results[0]["evidence"]) == 2 and len(units.results) > 1
        old_manifest = encode_prepared_resource_manifest(latest.batch)
        recreated = []

        async def delete_recreate():
            if not recreated:
                other.delete_resource(rid)
                other.import_resource_manifest(old_manifest)
                recreated.append(other.storage.resource_store.read_snapshot(rid))

        provider.hook = delete_recreate
        with pytest.raises(ValueError, match="producer_lifecycle_changed"):
            await e.ingest_native_media(
                source,
                kind="video",
                resource_id=rid,
                source_namespace="shared",
                source_ref="clip",
                policy=replace(base, start_ms=500, end_ms=1500),
            )
        assert e.storage.resource_store.read_snapshot(rid) == recreated[0]
        assert recreated[0].state.lifecycle_id != latest.state.lifecycle_id
    finally:
        other.close()
        e.close()


@pytest.mark.parametrize(
    "policy,reason",
    [
        (NativeMediaPolicy(end_ms=3000), "native_range_invalid"),
        (NativeMediaPolicy(end_ms=2000, max_duration_ms=1000), "native_duration_budget_exceeded"),
        (NativeMediaPolicy(end_ms=2000, max_units=1, frame_step_ms=250), "native_unit_budget_exceeded"),
        (NativeMediaPolicy(end_ms=2000, max_prepared_bytes=10), "native_prepared_budget_exceeded"),
    ],
)
async def test_native_budget_refusal_has_no_provider_or_graph(tmp_path, media_files, policy, reason):
    _, source = media_files
    provider = ScriptedProvider()
    e = engine(tmp_path, provider)
    rid = resource_id("budget", "clip")
    try:
        with pytest.raises(ValueError, match=reason):
            await e.ingest_native_media(
                source, kind="frames", resource_id=rid, source_namespace="budget", source_ref="clip", policy=policy
            )
        assert not provider.calls and e.storage.resource_store.read_snapshot(rid).state.status == "never_created"
    finally:
        e.close()


async def test_native_decoder_cancellation_and_timeout_reap_actual_child(tmp_path, monkeypatch):
    import sys

    from mdrack.ingestion.native_decoding import NativeMediaDecoder

    original = asyncio.create_subprocess_exec
    processes = []

    async def observe(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    decoder = NativeMediaDecoder()
    task = asyncio.create_task(
        decoder._run(sys.executable, ["-I", "-B", "-S", "-c", "import time;time.sleep(30)"], NativeMediaPolicy(), 100)
    )
    for _ in range(100):
        if processes:
            break
        await asyncio.sleep(0.01)
    assert processes
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
    with pytest.raises(TimeoutError):
        await decoder._run(
            sys.executable,
            ["-I", "-B", "-S", "-c", "import time;time.sleep(30)"],
            NativeMediaPolicy(timeout_seconds=1),
            100,
        )
    assert all(p.returncode is not None for p in processes)


async def test_native_provider_cancellation_cleans_snapshot_without_publication(tmp_path, media_files):
    import tempfile
    from pathlib import Path

    source, _ = media_files
    provider = ScriptedProvider()
    e = engine(tmp_path, provider)
    entered = asyncio.Event()

    async def wait_forever():
        entered.set()
        await asyncio.Event().wait()

    provider.hook = wait_forever
    snapshots = set(Path(tempfile.gettempdir()).glob("mdrack-raw-*.snapshot"))
    rid = resource_id("cancel", "audio")
    task = asyncio.create_task(
        e.ingest_native_media(
            source,
            kind="audio",
            resource_id=rid,
            source_namespace="cancel",
            source_ref="clip",
            policy=NativeMediaPolicy(end_ms=1000),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert e.storage.resource_store.read_snapshot(rid).state.status == "never_created"
        assert set(Path(tempfile.gettempdir()).glob("mdrack-raw-*.snapshot")) == snapshots
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        e.close()


async def test_native_after_begin_rollback_next_operation_and_source_transition(tmp_path, media_files):
    from mdrack_core import Locator, RepresentationRecord, SearchUnitRecord

    source, _ = media_files
    provider = ScriptedProvider()
    e = engine(tmp_path, provider)
    rid = resource_id("transition", "audio")
    try:
        policy = NativeMediaPolicy(end_ms=1000)
        await e.ingest_native_media(
            source, kind="audio", resource_id=rid, source_namespace="transition", source_ref="clip", policy=policy
        )
        store = e.storage.resource_store
        old = store.read_snapshot(rid)
        before = blobs(e)
        seen = []

        def fail(point):
            seen.append(point)
            if point == "after_units":
                raise OSError("PRIVATE_STORAGE_SENTINEL")

        store._failure_hook = fail
        try:
            with pytest.raises(Exception):
                await e.ingest_native_media(
                    source,
                    kind="audio",
                    resource_id=rid,
                    source_namespace="transition",
                    source_ref="clip",
                    policy=replace(policy, start_ms=1000, end_ms=2000),
                )
        finally:
            store._failure_hook = None
        assert "after_begin" in seen and "after_units" in seen
        assert store.read_snapshot(rid) == old and blobs(e) == before
        assert not store.connection.in_transaction
        useful = await e.ingest_native_media(
            source,
            kind="audio",
            resource_id=rid,
            source_namespace="transition",
            source_ref="clip",
            policy=replace(policy, start_ms=1000, end_ms=2000),
        )
        assert useful.publication_status == "published"
        current = store.read_snapshot(rid)
        note = RepresentationRecord(
            "independent-note",
            rid,
            "caption_text",
            "text",
            "independentnoteword",
            metadata={"source_independent": True},
        )
        unit = SearchUnitRecord(
            "independent-unit",
            rid,
            note.representation_id,
            "whole_resource",
            "text",
            note.text,
            Locator("external_record", {"ref": "note"}),
            0,
        )
        store.replace_resource(
            replace(
                current.batch,
                representations=(*current.batch.representations, note),
                units=(*current.batch.units, unit),
            )
        )
        old_generation = store.read_snapshot(rid).batch.resource.metadata["source_revision"]
        source.write_bytes(source.read_bytes() + b"new-raw-revision")
        changed = await e.ingest_native_media(
            source, kind="audio", resource_id=rid, source_namespace="transition", source_ref="clip", policy=policy
        )
        final = store.read_snapshot(rid)
        assert changed.publication_status == "published"
        assert final.batch.resource.metadata["source_revision"] != old_generation
        assert len([r for r in final.batch.representations if r.modality == "audio"]) == 1
        assert all(u.unit_id not in {x.unit_id for x in old.batch.units} for u in final.batch.units)
        assert note in final.batch.representations
        assert store.search_lexical(LexicalBranch("q", "independentnoteword"), scope=SearchScope())
    finally:
        e.close()


def test_partial_snapshot_write_failure_removes_owned_file(tmp_path, monkeypatch):
    from mdrack.ingestion import raw_source_provenance as source_io

    partial = tmp_path / "partial.snapshot"

    class BrokenSnapshot:
        name = str(partial)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, payload):
            partial.write_bytes(payload[:2])
            raise OSError("PRIVATE_PARTIAL_WRITE_SENTINEL")

    monkeypatch.setattr(source_io.tempfile, "NamedTemporaryFile", lambda **kwargs: BrokenSnapshot())
    with pytest.raises(source_io.RawSourceError, match="source_unavailable"):
        source_io._snapshot_file(b"owned synthetic bytes")
    assert not partial.exists()


async def test_audio_only_query_capability_limits_default_candidates_not_query_modality_assumptions(
    tmp_path, media_files
):
    from mdrack.domain.embedding_inputs import EncodingRole, InputCapability, RouteCapabilities

    _, source = media_files
    provider = ScriptedProvider()
    e = engine(tmp_path, provider)
    rid = resource_id("routes", "shared")
    try:
        policy = NativeMediaPolicy(end_ms=1000, max_width=32, max_height=24)
        for kind in ("frames", "audio"):
            await e.ingest_native_media(
                source, kind=kind, resource_id=rid, source_namespace="routes", source_ref="shared", policy=policy
            )
        provider.capabilities = RouteCapabilities((InputCapability(EncodingRole.QUERY, "audio", ("audio",), 1000000),))
        result = await e.search_native_media(source, kind="audio", policy=policy, target="unit")
        assert result.results
        assert all(evidence["modality"] == "audio" for item in result.results for evidence in item["evidence"])
    finally:
        e.close()
