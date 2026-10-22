"""Common late enrichment: synthetic prepared data, real file SQLite, no inference."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.ingestion.transcripts import read_transcript
from mdrack.public_api import MDRackEngine
from mdrack_core import (
    EmbeddingSpaceRecord,
    Facet,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_media import ProducerFingerprint, resource_id


def seed(root, *, only_native=False, kind="audio"):
    root.mkdir(exist_ok=True)
    assert CliRunner().invoke(main, ["--root", str(root), "init"]).exit_code == 0
    rid = resource_id("fixture", "prepared")
    loc = Locator("external_record", {"ref": {"audio": "clip.wav", "video": "clip.mp4", "image": "image.png"}[kind]})
    resource = ResourceRecord(
        rid,
        kind,
        {"audio": "audio/wav", "video": "video/mp4", "image": "image/png"}[kind],
        "fixture",
        loc,
        "prepared-not-source",
        metadata={"source_revision": "generation-1", "keep": {"manual": "metadata"}},
    )
    modality = "image" if kind == "image" else "audio"
    native = RepresentationRecord(
        "native", rid, "visual" if kind == "image" else "native_audio", modality, metadata={"producer_owner": "source"}
    )
    unit = SearchUnitRecord("native-u", rid, "native", "whole_resource", modality, None, loc, 0)
    independent = RepresentationRecord(
        "independent", rid, "caption_text", "text", "independentnote", metadata={"source_independent": True}
    )
    note = SearchUnitRecord("note-u", rid, "independent", "whole_resource", "text", "independentnote", loc, 0)
    batch = PreparedResourceBatch(
        resource,
        (native,) if only_native else (native, independent),
        (unit,) if only_native else (unit, note),
        (EmbeddingSpaceRecord("native-space", 2, "dot", "native-v1"),),
        (VectorRecord("native-u", "native-space", (-0.0, 0.75)),),
        (ResourceFacet(rid, Facet("topic", "synthetic"), "user"),),
    )
    engine = MDRackEngine(root=root, config=MDRackConfig())
    engine.storage.resource_store.replace_resource(batch)
    return engine, rid, engine.storage.resource_store.read_snapshot(rid).batch


def envelope(engine, rid, *, kind="transcript", revision="manual-1", text="cobalttranscript"):
    snapshot = engine.storage.resource_store.read_snapshot(rid)
    if kind in {"transcript", "video"}:
        artifact = read_transcript(
            json.dumps({"segments": [{"start": 0, "end": 1, "text": text}]}).encode(),
            resource_id=rid,
            producer_fingerprint=ProducerFingerprint.from_payload({"fixture": "manual"}),
        ).artifact.to_dict()
    else:
        artifact = text
    if kind in {"frames", "video"}:
        frames = {
            "schema": "mdrack.frame-captions.v1",
            "resource_id": rid,
            "producer_fingerprint": ProducerFingerprint.from_payload({"fixture": "caption"}).value,
            "normalization_fingerprint": None,
            "metadata": {},
            "frames": [{"frame_id": "frame-a", "timestamp_ms": 500, "caption": "cobaltframepoint", "metadata": {}}],
        }
        artifact = {"transcript": artifact, "frames": frames} if kind == "video" else frames
    digest = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return {
        "contract": "mdrack.prepared-enrichment",
        "version": 1,
        "binding": {
            "resource_id": rid,
            "lifecycle_id": snapshot.state.lifecycle_id,
            "source_namespace": "fixture",
            "source_locator": {
                "kind": snapshot.state.source_locator.kind,
                "payload": dict(snapshot.state.source_locator.payload),
            },
            "source_revision": snapshot.batch.resource.metadata["source_revision"],
        },
        "artifact_revision": revision,
        "producer": "manual-editor",
        "recipe": "prepared-v1",
        "manual": True,
        "artifact": artifact,
        "artifact_sha256": digest,
        "dependencies": [],
        "dense": {
            "space": {
                "space_id": "derived-space",
                "dimensions": 2,
                "metric": "dot",
                "fingerprint": "derived-v1",
                "metadata": {"profile": "prepared-text"},
            },
            "recipe": "text-embed-v1",
            "artifact_sha256": digest,
            "vectors": [[0.25, 0.5]] * (2 if kind == "video" else 1),
        },
    }


def write(root, value, name="ready.json"):
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", ["ocr", "text", "transcript", "frames", "video"])
@pytest.mark.parametrize(("lexical", "dense"), [(False, False), (True, False), (False, True), (True, True)])
def test_all_ready_projectors_and_four_intents_use_real_fts(tmp_path, kind, lexical, dense):
    from mdrack_core import LexicalBranch, SearchScope

    engine, rid, before = seed(
        tmp_path, kind="video" if kind in {"frames", "video"} else "image" if kind == "ocr" else "audio"
    )
    try:
        previous = engine.storage.resource_store.read_snapshot(rid)
        payload = envelope(engine, rid, kind=kind)
        path = write(tmp_path, payload)
        result = engine.enrich_prepared(
            rid,
            path,
            kind=kind,
            purpose="ready:whole",
            lexical=lexical,
            dense=dense,
            artifact_store=tmp_path / "archive",
            require_durable=True,
        )
        assert result["publication_status"] == ("published" if lexical or dense else "not_requested")
        after = engine.storage.resource_store.read_snapshot(rid)
        if not lexical and not dense:
            assert after == previous
        assert (
            engine.read_enrichment_artifact(result["artifact_reference"], artifact_store=tmp_path / "archive")
            == payload
        )
        assert next(v for v in after.batch.vectors if v.space_id == "native-space") == before.vectors[0]
        assert after.batch.facets == before.facets
        units = [u for u in after.batch.units if u.unit_id not in {"note-u", "native-u"}]
        assert len(units) == ((2 if kind == "video" else 1) if lexical or dense else 0)
        assert all((u.text is not None) == lexical and u.modality == "text" for u in units)
        assert len([v for v in after.batch.vectors if v.space_id == "derived-space"]) == (len(units) if dense else 0)
        if dense:
            from mdrack_core import VectorBranch

            hits = engine.storage.resource_store.search_vector(
                VectorBranch("prepared-query", "derived-space", (0.25, 0.5), expected_fingerprint="derived-v1"),
                scope=SearchScope(),
            )
            assert {hit.unit_id for hit in hits} == {u.unit_id for u in units}
        query = "cobaltframepoint" if kind == "frames" else "cobalttranscript"
        assert (
            bool(engine.storage.resource_store.search_lexical(LexicalBranch("q", query), scope=SearchScope()))
            == lexical
        )
        import asyncio

        public = asyncio.run(engine.search_unified(query, mode="text"))
        assert bool([item for item in public.results if item.resource_id == rid]) == lexical
        if kind in {"transcript", "video"} and units:
            timed = next(u for u in units if u.unit_kind == "time_segment")
            assert timed.evidence_locator.payload["start_ms"] == 0 and timed.evidence_locator.payload["end_ms"] == 1000
        if kind in {"frames", "video"} and units:
            frame = next(u for u in units if u.unit_kind == "frame")
            assert frame.evidence_locator.payload["timestamp_ms"] == 500
    finally:
        engine.close()


def test_normal_transcript_producer_preserves_native_and_independent_graph(tmp_path):
    import asyncio

    from mdrack_media import TranscriptArtifact

    engine, rid, before = seed(tmp_path)
    try:
        value = envelope(engine, rid)
        artifact = TranscriptArtifact.from_dict(value["artifact"])
        artifact = replace(artifact, metadata={**artifact.metadata, "enrichment_binding": value["binding"]})
        asyncio.run(
            engine.ingest_transcript(
                artifact,
                resource_kind="audio",
                media_type="audio/wav",
                source_namespace="fixture",
                source_locator=before.resource.locator,
                embeddings=False,
            )
        )
        current = engine.storage.resource_store.read_snapshot(rid).batch
        assert before.vectors[0] in current.vectors
        assert (
            next(r for r in current.representations if r.representation_id == "independent")
            == before.representations[0]
        )
        assert current.facets == before.facets
        assert current.resource.metadata["keep"] == before.resource.metadata["keep"]
    finally:
        engine.close()


@pytest.mark.parametrize("kind", ["ocr", "text", "transcript", "frames", "video"])
def test_no_text_removes_own_old_units_without_empty_core_batches(tmp_path, kind):
    from mdrack_core import LexicalBranch, SearchScope

    engine, rid, before = seed(
        tmp_path, kind="video" if kind in {"frames", "video"} else "image" if kind == "ocr" else "audio"
    )
    try:
        value = envelope(engine, rid, kind=kind)
        path = write(tmp_path, value)
        engine.enrich_prepared(rid, path, kind=kind, purpose="late:whole", dense=True)
        if kind in {"ocr", "text"}:
            value["artifact"] = ""
        elif kind == "transcript":
            value["artifact"]["atoms"] = []
        elif kind == "frames":
            value["artifact"]["frames"] = []
        else:
            value["artifact"]["transcript"]["atoms"] = []
            value["artifact"]["frames"]["frames"] = []
        value["artifact_revision"] = "no-text-2"
        value["artifact_sha256"] = hashlib.sha256(
            json.dumps(value["artifact"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        value["dense"] = None
        result = engine.enrich_prepared(
            rid, write(tmp_path, value, "empty.json"), kind=kind, purpose="late:whole", dense=True
        )
        assert result["artifact_status"] == "no_text"
        after = engine.storage.resource_store.read_snapshot(rid).batch
        assert after == before
        assert not engine.storage.resource_store.search_lexical(
            LexicalBranch("q", "cobalttranscript"), scope=SearchScope()
        )
        assert all(u.text is None or u.text.strip() for u in after.units)
    finally:
        engine.close()


def test_immediate_native_and_transcript_dependencies_and_manual_authority(tmp_path):
    from mdrack_core import LexicalBranch, SearchScope

    engine, rid, before = seed(tmp_path)
    try:
        engine.reindex_resource_file(
            rid, write(tmp_path, reindex_payload(engine, rid, native_base(before)), "native.json")
        )
        value = envelope(engine, rid)
        path = write(tmp_path, value)
        engine.enrich_prepared(rid, path, kind="transcript", purpose="transcript:whole")
        current = engine.storage.resource_store.read_snapshot(rid).batch
        native_vectors = tuple(v for v in current.vectors if v.space_id == "native-space")
        keys = (("transcript:whole", "default"), ("source/native_audio", "default"))
        active = {
            (r.metadata["enrichment_v1"]["purpose"], r.metadata["enrichment_v1"]["variant"]): r.metadata[
                "enrichment_v1"
            ]
            for r in current.representations
            if "enrichment_v1" in r.metadata
        }
        joint = envelope(engine, rid, kind="text", text="jointmaterial")
        joint["dependencies"] = [
            {"purpose": key[0], "variant": key[1], "revision": active[key]["artifact_revision"]} for key in keys
        ]
        joint["dense"]["space"]["space_id"] = "joint-space"
        joint["dense"]["space"]["fingerprint"] = "joint-v1"
        joint_path = write(tmp_path, joint, "joint.json")
        engine.enrich_prepared(
            rid, joint_path, kind="text", purpose="joint:whole", lexical=False, dense=True, inputs=keys
        )
        delayed = engine.prepare_enrichment(
            rid, joint_path, kind="text", purpose="joint:whole", lexical=False, dense=True, inputs=keys
        )
        correction = envelope(engine, rid, text="azurehuman", revision="manual-2")
        engine.enrich_prepared(
            rid, write(tmp_path, correction, "correction.json"), kind="transcript", purpose="transcript:whole"
        )
        after = engine.storage.resource_store.read_snapshot(rid)
        assert tuple(v for v in after.batch.vectors if v.space_id == "native-space") == native_vectors
        assert not any(v.space_id == "joint-space" for v in after.batch.vectors)
        assert not any(
            r.metadata.get("enrichment_v1", {}).get("purpose") == "joint:whole" for r in after.batch.representations
        )
        assert engine.storage.resource_store.search_lexical(LexicalBranch("q", "azurehuman"), scope=SearchScope())
        assert not engine.storage.resource_store.search_lexical(
            LexicalBranch("q", "cobalttranscript"), scope=SearchScope()
        )
        with pytest.raises(ValueError):
            engine.publish_enrichment(delayed)
        automatic = envelope(engine, rid, revision="auto-3", text="automaticmustnotwin")
        automatic["manual"] = False
        with pytest.raises(ValueError):
            engine.enrich_prepared(
                rid, write(tmp_path, automatic, "auto.json"), kind="transcript", purpose="transcript:whole"
            )
        assert engine.storage.resource_store.read_snapshot(rid) == after
    finally:
        engine.close()


def test_late_normal_transcript_cannot_overwrite_manual_and_whole_dependent_is_pruned(tmp_path):
    import asyncio

    from mdrack.application.transcript_ingestion import TranscriptIngestionService
    from mdrack_core import LexicalBranch, SearchScope
    from mdrack_media import TranscriptArtifact

    engine, rid, before = seed(tmp_path)
    try:
        value = envelope(engine, rid)
        artifact = TranscriptArtifact.from_dict(value["artifact"])
        artifact = replace(artifact, metadata={**artifact.metadata, "enrichment_binding": value["binding"]})
        asyncio.run(
            engine.ingest_transcript(
                artifact,
                resource_kind="audio",
                media_type="audio/wav",
                source_namespace="fixture",
                source_locator=before.resource.locator,
                embeddings=False,
            )
        )
        late_artifact = TranscriptArtifact.from_dict(envelope(engine, rid, text="lateautomatic")["artifact"])
        late_batch = TranscriptIngestionService(engine.storage.resource_store).prepare(
            late_artifact,
            resource_kind="audio",
            media_type="audio/wav",
            source_namespace="fixture",
            source_locator=before.resource.locator,
        )
        late_batch = replace(
            late_batch,
            resource=replace(
                late_batch.resource, metadata={**late_batch.resource.metadata, "source_revision": "generation-1"}
            ),
        )
        delayed = engine.prepare_reindex_file(
            rid, write(tmp_path, reindex_payload(engine, rid, late_batch), "late.json")
        )
        correction = envelope(engine, rid, text="humanwins", revision="manual-2")
        engine.enrich_prepared(
            rid, write(tmp_path, correction, "manual.json"), kind="transcript", purpose="source/transcript"
        )
        after = engine.storage.resource_store.read_snapshot(rid)
        assert not any(r.representation_kind == "whole_resource_text" for r in after.batch.representations)
        with pytest.raises(ValueError):
            engine.publish_reindex(delayed)
        assert engine.storage.resource_store.read_snapshot(rid) == after
        assert engine.storage.resource_store.search_lexical(LexicalBranch("q", "humanwins"), scope=SearchScope())
        assert not engine.storage.resource_store.search_lexical(
            LexicalBranch("q", "lateautomatic"), scope=SearchScope()
        )
        assert not engine.storage.resource_store.search_lexical(
            LexicalBranch("q", "cobalttranscript"), scope=SearchScope()
        )
        assert before.vectors[0] in after.batch.vectors
    finally:
        engine.close()


def test_video_frame_correction_preserves_transcript_only_dependent(tmp_path):
    engine, rid, _ = seed(tmp_path, kind="video")
    try:
        video = envelope(engine, rid, kind="video")
        engine.enrich_prepared(rid, write(tmp_path, video), kind="video", purpose="video:whole", dense=True)
        current = engine.storage.resource_store.read_snapshot(rid).batch
        transcript_info = next(
            r.metadata["enrichment_v1"]
            for r in current.representations
            if r.metadata.get("enrichment_v1", {}).get("purpose") == "video:whole/transcript"
        )
        joint = envelope(engine, rid, kind="text", text="transcriptderived")
        joint["dependencies"] = [
            {
                "purpose": "video:whole/transcript",
                "variant": "default",
                "revision": transcript_info["artifact_revision"],
            }
        ]
        joint["dense"]["space"]["space_id"] = "joint-space"
        joint["dense"]["space"]["fingerprint"] = "joint-v1"
        engine.enrich_prepared(
            rid,
            write(tmp_path, joint, "joint.json"),
            kind="text",
            purpose="joint:transcript",
            lexical=False,
            dense=True,
            inputs=(("video:whole/transcript", "default"),),
        )
        before = engine.storage.resource_store.read_snapshot(rid).batch
        vector = next(v for v in before.vectors if v.space_id == "joint-space")
        video["artifact"]["frames"]["frames"][0]["caption"] = "azureframecorrection"
        video["artifact_revision"] = "manual-2"
        video["artifact_sha256"] = hashlib.sha256(
            json.dumps(video["artifact"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        video["dense"]["artifact_sha256"] = video["artifact_sha256"]
        engine.enrich_prepared(
            rid, write(tmp_path, video, "video-correction.json"), kind="video", purpose="video:whole", dense=True
        )
        after = engine.storage.resource_store.read_snapshot(rid).batch
        assert next(v for v in after.vectors if v.space_id == "joint-space") == vector
        assert (
            next(
                r.metadata["enrichment_v1"]["artifact_revision"]
                for r in after.representations
                if r.metadata.get("enrichment_v1", {}).get("purpose") == "video:whole/transcript"
            )
            == transcript_info["artifact_revision"]
        )
    finally:
        engine.close()


def test_same_revision_cannot_hide_changed_dense_profile(tmp_path):
    engine, rid, _ = seed(tmp_path)
    try:
        value = envelope(engine, rid, kind="text")
        engine.enrich_prepared(rid, write(tmp_path, value), kind="text", purpose="caption:whole", dense=True)
        before = engine.storage.resource_store.read_snapshot(rid)
        value["dense"]["space"]["space_id"] = "different-profile"
        value["dense"]["space"]["fingerprint"] = "different-v1"
        with pytest.raises(ValueError):
            engine.enrich_prepared(
                rid, write(tmp_path, value, "profile.json"), kind="text", purpose="caption:whole", dense=True
            )
        assert engine.storage.resource_store.read_snapshot(rid) == before
        value["artifact_revision"] = "manual-profile-2"
        engine.enrich_prepared(
            rid, write(tmp_path, value, "profile-revision.json"), kind="text", purpose="caption:whole", dense=True
        )
        assert {v.space_id for v in engine.storage.resource_store.read_snapshot(rid).batch.vectors} == {
            "native-space",
            "different-profile",
        }
    finally:
        engine.close()


def test_opaque_operations_do_not_transfer_writer_authority_on_reopen(tmp_path):
    engine, rid, before = seed(tmp_path)
    path = write(tmp_path, envelope(engine, rid, kind="text"))
    prepared = engine.prepare_enrichment(rid, path, kind="text", purpose="caption:whole")
    reindex = engine.prepare_reindex_file(
        rid, write(tmp_path, reindex_payload(engine, rid, native_base(before)), "reindex.json")
    )
    old = engine.storage.resource_store.read_snapshot(rid)
    engine.close()
    reopened = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        assert reopened.storage.resource_store.read_snapshot(rid) == old
        for action in (lambda: reopened.publish_enrichment(prepared), lambda: reopened.publish_reindex(reindex)):
            with pytest.raises(ValueError):
                action()
        assert reopened.storage.resource_store.read_snapshot(rid) == old
        assert (
            reopened.enrich_prepared(rid, path, kind="text", purpose="caption:whole")["publication_status"]
            == "published"
        )
    finally:
        reopened.close()


def test_archive_concurrent_immutable_retain_and_unknown_owner_refusal(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from mdrack.application import _artifact_cache_fs as fs
    from mdrack.public_api import DurableArtifactStore

    archive = DurableArtifactStore(tmp_path / "archive")
    archive.initialize()
    data = b'{"fixture":"retained-material"}'
    with ThreadPoolExecutor(max_workers=2) as pool:
        references = list(pool.map(archive.retain, (data, data)))
    assert references[0] == references[1]
    assert archive.read_bytes(references[0]) == data
    assert not any(p.name.startswith(".stage-") for p in archive.root.iterdir())
    unknown = tmp_path / "unknown-owner"
    directory = fs.open_root(unknown, create=True)
    fs.close(directory)
    original = unknown / "caller.keep"
    original.write_bytes(b"caller-owned-original")
    with pytest.raises(ValueError):
        DurableArtifactStore(unknown).retain(data)
    assert original.read_bytes() == b"caller-owned-original"
    assert not (unknown / ".mdrack-durable-artifacts-v1").exists()


def test_normal_ready_artifact_does_not_borrow_current_source_binding(tmp_path):
    import asyncio

    from mdrack_media import TranscriptArtifact

    engine, rid, before = seed(tmp_path)
    try:
        value = envelope(engine, rid)
        unbound = TranscriptArtifact.from_dict(value["artifact"])
        snapshot = engine.storage.resource_store.read_snapshot(rid)
        with pytest.raises(ValueError):
            asyncio.run(
                engine.ingest_transcript(
                    unbound,
                    resource_kind="audio",
                    media_type="audio/wav",
                    source_namespace="fixture",
                    source_locator=before.resource.locator,
                    embeddings=False,
                )
            )
        stale = replace(unbound, metadata={"enrichment_binding": {**value["binding"], "lifecycle_id": "foreign-life"}})
        with pytest.raises(ValueError):
            asyncio.run(
                engine.ingest_transcript(
                    stale,
                    resource_kind="audio",
                    media_type="audio/wav",
                    source_namespace="fixture",
                    source_locator=before.resource.locator,
                    embeddings=False,
                )
            )
        assert engine.storage.resource_store.read_snapshot(rid) == snapshot
    finally:
        engine.close()


def test_source_observations_share_one_purpose_not_producer_variants(tmp_path):
    engine, rid, before = seed(tmp_path, kind="image")
    try:
        base = native_base(before)
        reps = tuple(
            RepresentationRecord("ocr-" + name, rid, "ocr_text", "text", text)
            for name, text in (("a", "firstobservation"), ("b", "secondobservation"))
        )
        units = tuple(
            SearchUnitRecord(
                rep.representation_id + "-u",
                rid,
                rep.representation_id,
                "whole_resource",
                "text",
                rep.text,
                before.resource.locator,
                0,
            )
            for rep in reps
        )
        batch = replace(base, representations=base.representations + reps, units=base.units + units)
        job = engine.prepare_reindex_file(rid, write(tmp_path, reindex_payload(engine, rid, batch), "source.json"))
        assert engine.publish_reindex(job)["publication_status"] == "published"
        current = engine.storage.resource_store.read_snapshot(rid).batch
        outputs = [
            r for r in current.representations if r.metadata.get("enrichment_v1", {}).get("purpose") == "ocr:whole"
        ]
        assert len(outputs) == 1
        observed = [u for u in current.units if u.representation_id == outputs[0].representation_id]
        assert [u.ordinal for u in observed] == [0, 1]
        assert {u.text for u in observed} == {"firstobservation", "secondobservation"}
        assert current.facets == before.facets
        assert engine.publish_reindex(job)["publication_status"] == "already_published"
    finally:
        engine.close()


def test_ready_material_revision_survives_projection_policy_changes(tmp_path):
    from mdrack_core import LexicalBranch, SearchScope

    engine, rid, _ = seed(tmp_path)
    try:
        path = write(tmp_path, envelope(engine, rid))
        fingerprints = []
        for lexical, dense in ((True, False), (True, True), (False, True), (True, True), (True, False)):
            result = engine.enrich_prepared(
                rid, path, kind="transcript", purpose="transcript:whole", lexical=lexical, dense=dense
            )
            assert result["publication_status"] in {"published", "already_published"}
            current = engine.storage.resource_store.read_snapshot(rid).batch
            info = next(
                r.metadata["enrichment_v1"]
                for r in current.representations
                if r.metadata.get("enrichment_v1", {}).get("purpose") == "transcript:whole"
            )
            fingerprints.append(info["material_fingerprint"])
            assert info["artifact_revision"] == "manual-1"
            assert (
                bool(
                    engine.storage.resource_store.search_lexical(
                        LexicalBranch("q", "cobalttranscript"), scope=SearchScope()
                    )
                )
                == lexical
            )
            assert bool([v for v in current.vectors if v.space_id == "derived-space"]) == dense
        assert len(set(fingerprints)) == 1
    finally:
        engine.close()


def test_rebase_preserves_concurrent_independent_resource_metadata(tmp_path):
    engine, rid, before = seed(tmp_path)
    try:
        path = write(tmp_path, envelope(engine, rid, kind="text"))
        enrichment = engine.prepare_enrichment(rid, path, kind="text", purpose="caption:whole")
        reindex = engine.prepare_reindex_file(
            rid, write(tmp_path, reindex_payload(engine, rid, native_base(before, new_profile=True)), "native.json")
        )
        changed = replace(
            before,
            resource=replace(
                before.resource, metadata={**before.resource.metadata, "keep": {"manual": "new independent metadata"}}
            ),
        )
        engine.storage.resource_store.replace_resource(changed)
        assert engine.publish_enrichment(enrichment)["publication_status"] == "published"
        assert engine.publish_reindex(reindex)["publication_status"] == "published"
        after = engine.storage.resource_store.read_snapshot(rid).batch
        assert after.resource.metadata["keep"] == changed.resource.metadata["keep"]
        assert after.facets == before.facets
        assert any(r.metadata.get("enrichment_v1", {}).get("purpose") == "caption:whole" for r in after.representations)
        assert {v.space_id for v in after.vectors} == {"native-new"}
    finally:
        engine.close()


def test_registered_prepared_cli_reindex_withdraw_and_content_free_failure(tmp_path, caplog):
    engine, rid, before = seed(tmp_path)
    value = envelope(engine, rid)
    path = write(tmp_path, value)
    engine.close()
    runner = CliRunner()

    def invoke(*args):
        result = runner.invoke(main, ["--root", str(tmp_path), "resource", *args])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)["data"]

    assert invoke("enrichment-binding", rid) == value["binding"]
    enriched = invoke(
        "enrich-prepared",
        rid,
        str(path),
        "--kind",
        "transcript",
        "--purpose",
        "transcript:whole",
        "--dense",
        "--artifact-store",
        str(tmp_path / "archive"),
        "--require-durable",
    )
    assert enriched["publication_status"] == "published"
    assert (
        invoke("read-artifact", enriched["artifact_reference"], "--artifact-store", str(tmp_path / "archive")) == value
    )
    broken = tmp_path / "PRIVATE_PATH_SENTINEL.json"
    broken.write_text('{"PRIVATE_BODY_SENTINEL": broken-json', encoding="utf-8")
    failed = runner.invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "resource",
            "enrich-prepared",
            rid,
            str(broken),
            "--kind",
            "transcript",
            "--purpose",
            "transcript:whole",
        ],
    )
    assert failed.exit_code == 1
    assert "PRIVATE_PATH_SENTINEL" not in failed.output + caplog.text
    assert "PRIVATE_BODY_SENTINEL" not in failed.output + caplog.text
    local = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        payload = reindex_payload(local, rid, native_base(before, new_profile=True))
    finally:
        local.close()
    assert invoke("publication-binding", rid) == payload["expected"]
    native = write(tmp_path, payload, "native.json")
    assert invoke("reindex", rid, str(native))["publication_status"] == "published"
    assert invoke("withdraw", rid)["publication_status"] == "withdrawn"
    state = invoke("publication-binding", rid)
    assert state["status"] == "live_without_projections"
    assert state["lifecycle_id"] == value["binding"]["lifecycle_id"]


def test_normal_video_and_common_video_share_component_purposes(tmp_path):
    import asyncio

    from mdrack.ingestion.frame_captions import read_frame_captions
    from mdrack_media import TranscriptArtifact

    engine, rid, before = seed(tmp_path, kind="video")
    try:
        value = envelope(engine, rid, kind="video")
        transcript = TranscriptArtifact.from_dict(value["artifact"]["transcript"])
        frames = read_frame_captions(json.dumps(value["artifact"]["frames"]).encode()).artifact
        transcript = replace(transcript, metadata={**transcript.metadata, "enrichment_binding": value["binding"]})
        frames = replace(frames, metadata={**frames.metadata, "enrichment_binding": value["binding"]})
        asyncio.run(
            engine.ingest_video(
                transcript,
                frames,
                media_type="video/mp4",
                source_namespace="fixture",
                source_locator=before.resource.locator,
                embeddings=False,
            )
        )
        current = engine.storage.resource_store.read_snapshot(rid).batch
        assert before.vectors[0] in current.vectors
        assert current.facets == before.facets
        value = envelope(engine, rid, kind="video", revision="manual-2")
        engine.enrich_prepared(rid, write(tmp_path, value), kind="video", purpose="source", dense=True)
        after = engine.storage.resource_store.read_snapshot(rid).batch
        purposes = [
            r.metadata["enrichment_v1"]["purpose"] for r in after.representations if "enrichment_v1" in r.metadata
        ]
        assert purposes.count("source/transcript") == purposes.count("source/frames") == 1
        assert "source/whole_resource_text" not in purposes
        assert before.vectors[0] in after.vectors
        assert after.facets == before.facets
    finally:
        engine.close()


def test_capture_precedes_real_artifact_read_and_late_auto_cannot_win(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from mdrack.application import prepared_enrichment as adapter

    engine, rid, _ = seed(tmp_path)
    before = engine.storage.resource_store.read_snapshot(rid)
    value = envelope(engine, rid, revision="automatic-1", text="automaticmustnotwin")
    value["manual"] = False
    path = write(tmp_path, value, "automatic.json")
    reached, release = Event(), Event()
    actual_read = adapter._read
    reads = []

    def gated_read(selected):
        if selected == path:
            reads.append(selected)
            if not reached.is_set():
                reached.set()
                assert release.wait(timeout=10)
        return actual_read(selected)

    monkeypatch.setattr(adapter, "_read", gated_read)

    def worker():
        local = MDRackEngine(root=tmp_path, config=MDRackConfig())
        try:
            operation = local.prepare_enrichment(rid, path, kind="transcript", purpose="transcript:whole")
            token = operation.captured.snapshot.state.state_token
            with pytest.raises(ValueError):
                local.publish_enrichment(operation)
            return token
        finally:
            local.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                assert reached.wait(timeout=10)
                manual = envelope(engine, rid, revision="manual-2", text="humanwins")
                engine.enrich_prepared(
                    rid, write(tmp_path, manual, "manual.json"), kind="transcript", purpose="transcript:whole"
                )
                corrected = engine.storage.resource_store.read_snapshot(rid)
            finally:
                release.set()
            assert future.result(timeout=10) == before.state.state_token
        assert len(reads) == 2
        assert engine.storage.resource_store.read_snapshot(rid) == corrected
    finally:
        release.set()
        engine.close()


def test_reindex_metadata_is_not_new_host_custody_evidence(tmp_path):
    engine, rid, before = seed(tmp_path)
    try:
        info = {
            "purpose": "forged:whole",
            "variant": "default",
            "source_revision": "generation-1",
            "artifact_revision": "forged-1",
            "dependencies": [],
            "producer": "controlled-fixture",
            "recipe": "ready-v1",
            "retention": "host_durable",
            "artifact_reference": "artifact:sha256:" + "1" * 64,
        }
        rep = RepresentationRecord(
            "forged", rid, "caption_text", "text", "real graph text", metadata={"enrichment_v1": info}
        )
        unit = SearchUnitRecord(
            "forged-u", rid, "forged", "whole_resource", "text", "real graph text", before.resource.locator, 0
        )
        ready = PreparedResourceBatch(before.resource, (rep,), (unit,))
        snapshot = engine.storage.resource_store.read_snapshot(rid)
        path = write(tmp_path, reindex_payload(engine, rid, ready), "forged-custody.json")
        with pytest.raises(ValueError):
            engine.reindex_resource_file(rid, path)
        assert engine.storage.resource_store.read_snapshot(rid) == snapshot
    finally:
        engine.close()


def test_explicit_variants_share_one_bounded_search_family(tmp_path):
    import asyncio

    from mdrack_core import LexicalBranch, SearchScope, VectorBranch

    engine, rid, _ = seed(tmp_path)
    try:
        for variant, text in (("default", "visiblevariant firstchoice"), ("archival", "visiblevariant otherchoice")):
            path = write(tmp_path, envelope(engine, rid, kind="text", text=text), variant + ".json")
            engine.enrich_prepared(rid, path, kind="text", purpose="caption:whole", variant=variant, dense=True)
        current = engine.storage.resource_store.read_snapshot(rid).batch
        outputs = [
            r for r in current.representations if r.metadata.get("enrichment_v1", {}).get("purpose") == "caption:whole"
        ]
        assert {r.metadata["enrichment_v1"]["variant"] for r in outputs} == {"default", "archival"}
        assert len([v for v in current.vectors if v.space_id == "derived-space"]) == 2
        # Policy: all explicitly published compatible variants share the branch limit,
        # not one multiplied candidate allocation per stored representation.
        lexical = engine.storage.resource_store.search_lexical(
            LexicalBranch("q", "visiblevariant", candidate_limit=1), scope=SearchScope()
        )
        dense = engine.storage.resource_store.search_vector(
            VectorBranch("q", "derived-space", (0.25, 0.5), candidate_limit=1), scope=SearchScope()
        )
        assert len(lexical) == len(dense) == 1
        public = asyncio.run(engine.search_unified("visiblevariant", mode="text", limit=1))
        assert [item.resource_id for item in public.results] == [rid]
    finally:
        engine.close()


def reindex_payload(engine, rid, batch):
    from mdrack.application.manifest import encode_prepared_resource_manifest

    return {
        "contract": "mdrack.reindex-resource",
        "version": 1,
        "expected": engine.publication_binding(rid),
        "owner": "source",
        "batch": json.loads(encode_prepared_resource_manifest(batch)),
    }


def native_base(before, *, generation=None, new_profile=False):
    resource = (
        before.resource
        if generation is None
        else replace(before.resource, metadata={**before.resource.metadata, "source_revision": generation})
    )
    rep = next(r for r in before.representations if r.representation_id == "native")
    unit = next(u for u in before.units if u.unit_id == "native-u")
    space = EmbeddingSpaceRecord(
        "native-new" if new_profile else "native-space", 2, "dot", "native-v2" if new_profile else "native-v1"
    )
    return PreparedResourceBatch(
        resource, (rep,), (unit,), (space,), (VectorRecord("native-u", space.space_id, (0.625, -0.125)),)
    )


def test_changed_source_reindex_prunes_dependents_but_preserves_independent_graph(tmp_path):
    from mdrack_core import LexicalBranch, SearchScope

    engine, rid, before = seed(tmp_path)
    try:
        store = engine.storage.resource_store
        path = write(tmp_path, envelope(engine, rid, kind="text"))
        engine.enrich_prepared(rid, path, kind="text", purpose="caption:whole", dense=True)
        delayed = engine.prepare_enrichment(rid, path, kind="text", purpose="caption:whole", dense=True)
        fresh = native_base(before, generation="generation-2", new_profile=True)
        artifact = write(tmp_path, reindex_payload(engine, rid, fresh), "reindex.json")
        operation = engine.prepare_reindex_file(rid, artifact)
        result = engine.publish_reindex(operation)
        assert result["publication_status"] == "published"
        after = store.read_snapshot(rid)
        assert engine.publish_reindex(operation)["publication_status"] == "already_published"
        assert store.read_snapshot(rid) == after
        assert after.batch.resource.metadata["source_revision"] == "generation-2"
        assert after.batch.resource.metadata["keep"] == before.resource.metadata["keep"]
        assert after.batch.facets == before.facets
        assert {r.representation_id for r in after.batch.representations} == {"native", "independent"}
        assert after.batch.vectors == fresh.vectors
        assert not store.search_lexical(LexicalBranch("q", "cobalttranscript"), scope=SearchScope())
        assert store.search_lexical(LexicalBranch("q", "independentnote"), scope=SearchScope())
        with pytest.raises(ValueError):
            engine.publish_enrichment(delayed)
        with pytest.raises(ValueError):
            engine.reindex_resource_file(rid, artifact)  # old local token is not a new preparation grant
        assert store.read_snapshot(rid) == after
    finally:
        engine.close()


def test_withdraw_reenter_and_delete_recreate_never_accept_old_jobs(tmp_path):
    engine, rid, before = seed(tmp_path)
    try:
        path = write(tmp_path, envelope(engine, rid, kind="text"))
        old = engine.prepare_enrichment(rid, path, kind="text", purpose="caption:whole")
        reindex_path = write(tmp_path, reindex_payload(engine, rid, native_base(before)), "reindex.json")
        old_reindex = engine.prepare_reindex_file(rid, reindex_path)
        original = engine.storage.resource_store.read_snapshot(rid)
        assert engine.withdraw_resource(rid)["publication_status"] == "withdrawn"
        withdrawn = engine.storage.resource_store.read_snapshot(rid)
        assert withdrawn.batch is None and withdrawn.state.live
        assert withdrawn.state.lifecycle_id == original.state.lifecycle_id
        with pytest.raises(ValueError):
            engine.publish_enrichment(old)
        with pytest.raises(ValueError):
            engine.publish_reindex(old_reindex)
        resumed = write(
            tmp_path, reindex_payload(engine, rid, native_base(before, generation="generation-2")), "resume.json"
        )
        assert engine.reindex_resource_file(rid, resumed)["publication_status"] == "published"
        assert engine.storage.resource_store.read_snapshot(rid).state.lifecycle_id == original.state.lifecycle_id
        deleted_job = engine.prepare_reindex_file(
            rid,
            write(
                tmp_path,
                reindex_payload(engine, rid, native_base(before, generation="generation-2")),
                "before-delete.json",
            ),
        )
        assert engine.delete_resource(rid).deleted
        from mdrack.application.manifest import encode_prepared_resource_manifest

        engine.import_resource_manifest(encode_prepared_resource_manifest(before))
        recreated = engine.storage.resource_store.read_snapshot(rid)
        assert recreated.state.lifecycle_id != original.state.lifecycle_id
        for action in (lambda: engine.publish_reindex(deleted_job), lambda: engine.publish_enrichment(old)):
            with pytest.raises(ValueError):
                action()
        assert engine.storage.resource_store.read_snapshot(rid) == recreated
    finally:
        engine.close()


@pytest.mark.parametrize(("lexical", "dense"), [(False, False), (True, False), (False, True), (True, True)])
def test_host_archive_survives_caller_and_cache_cleanup_then_reopen(tmp_path, lexical, dense):
    from mdrack.public_api import DurableArtifactStore

    engine, rid, before = seed(tmp_path)
    payload = envelope(engine, rid, kind="text")
    path = write(tmp_path, payload)
    archive = tmp_path / "archive"
    try:
        operation = engine.prepare_enrichment(
            rid,
            path,
            kind="text",
            purpose="caption:whole",
            lexical=lexical,
            dense=dense,
            artifact_store=archive,
            require_durable=True,
        )
        path.unlink()
        assert engine.publish_enrichment(operation)["publication_status"] == (
            "published" if lexical or dense else "not_requested"
        )
        published = engine.storage.resource_store.read_snapshot(rid)
        if not lexical and not dense:
            assert published.batch == before
        from mdrack.application.artifact_cache import ArtifactCache, ArtifactCacheKey

        cache = ArtifactCache(tmp_path / "cache")
        # A real clearable cache is provisioned independently; archive is not one.
        identities = (
            payload["binding"],
            payload["producer"],
            payload["dense"]["space"],
            {"prompt": "not_requested"},
            {"lexical": lexical, "dense": dense},
            payload["recipe"],
        )
        key = ArtifactCacheKey(
            "prepared_text",
            *(
                "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                for value in identities
            ),
        )
        cached_bytes = json.dumps(payload).encode("utf-8")
        assert cache.store(key, cached_bytes).state == "stored"
        assert json.loads(cache.lookup(key).payload) == payload
        assert cache.purge(confirm=True) == 1
        assert cache.lookup(key).state == "miss"
        with pytest.raises(ValueError):
            DurableArtifactStore(tmp_path / "cache").retain(b"{}")
    finally:
        engine.close()
    reopened = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        assert reopened.read_enrichment_artifact(operation.reference, artifact_store=archive) == payload
        assert reopened.storage.resource_store.read_snapshot(rid) == published
        from mdrack_core import LexicalBranch, SearchScope

        assert (
            bool(
                reopened.storage.resource_store.search_lexical(
                    LexicalBranch("q", "cobalttranscript"), scope=SearchScope()
                )
            )
            == lexical
        )
        assert bool([v for v in published.batch.vectors if v.space_id == "derived-space"]) == dense
        standalone = MDRackEngine(root=tmp_path / "reader-without-catalog", config=MDRackConfig())
        try:
            assert standalone.read_enrichment_artifact(operation.reference, artifact_store=archive) == payload
            assert not (tmp_path / "reader-without-catalog" / ".mdrack").exists()
        finally:
            standalone.close()
        result = CliRunner().invoke(
            main,
            [
                "--root",
                str(tmp_path),
                "resource",
                "read-artifact",
                operation.reference,
                "--artifact-store",
                str(archive),
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"] == payload
        archived_file = archive / operation.reference.rsplit(":", 1)[1] / "payload.json"
        archived_file.unlink()
        with pytest.raises(ValueError):
            reopened.read_enrichment_artifact(operation.reference, artifact_store=archive)
        assert reopened.storage.resource_store.read_snapshot(rid) == published
    finally:
        reopened.close()


def test_real_file_connections_rebase_native_producer_with_enrichment(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    engine, rid, before = seed(tmp_path)
    path = write(tmp_path, envelope(engine, rid, kind="text"))
    reindex_path = write(tmp_path, reindex_payload(engine, rid, native_base(before, new_profile=True)), "native.json")
    engine.close()
    barrier = Barrier(2)

    def worker(native):
        local = MDRackEngine(root=tmp_path, config=MDRackConfig())
        try:
            job = (
                local.prepare_reindex_file(rid, reindex_path)
                if native
                else local.prepare_enrichment(rid, path, kind="text", purpose="caption:whole", dense=True)
            )
            barrier.wait(timeout=10)
            return local.publish_reindex(job) if native else local.publish_enrichment(job)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (True, False)))
    assert [result["publication_status"] for result in results] == ["published", "published"]
    assert sorted(result["attempt_count"] for result in results) == [1, 2]
    final = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        after = final.storage.resource_store.read_snapshot(rid).batch
        assert {v.space_id for v in after.vectors} == {"native-new", "derived-space"}
        assert {r.representation_id for r in after.representations} >= {"native", "independent"}
        assert after.facets == before.facets and after.resource.metadata["keep"] == before.resource.metadata["keep"]
    finally:
        final.close()


def test_dense_only_transcript_then_fts_reuses_same_artifact_and_vector(tmp_path):
    engine, rid, before = seed(tmp_path)
    try:
        path = write(tmp_path, envelope(engine, rid))
        result = engine.enrich_prepared(
            rid, path, kind="transcript", purpose="transcript:audio:0-1000", lexical=False, dense=True
        )
        assert result["publication_status"] == "published"
        current = engine.storage.resource_store.read_snapshot(rid)
        derived = [u for u in current.batch.units if u.modality == "text" and u.unit_id != "note-u"]
        assert derived and all(u.text is None for u in derived)
        assert all(r.text is None for r in current.batch.representations if "enrichment_v1" in r.metadata)
        from mdrack_core import LexicalBranch, SearchScope

        assert not engine.storage.resource_store.search_lexical(
            LexicalBranch("q", "cobalttranscript"), scope=SearchScope()
        )
        vector = next(v for v in current.batch.vectors if v.space_id == "derived-space")
        assert vector.vector == (0.25, 0.5)
        assert [v for v in current.batch.vectors if v.space_id == "native-space"] == list(before.vectors)
        result = engine.enrich_prepared(
            rid, path, kind="transcript", purpose="transcript:audio:0-1000", lexical=True, dense=True
        )
        assert result["publication_status"] == "published"
        after = engine.storage.resource_store.read_snapshot(rid)
        assert next(v for v in after.batch.vectors if v.space_id == "derived-space") == vector
        assert engine.storage.resource_store.search_lexical(LexicalBranch("q", "cobalttranscript"), scope=SearchScope())
        assert after.batch.resource == before.resource and after.batch.facets == before.facets
    finally:
        engine.close()
