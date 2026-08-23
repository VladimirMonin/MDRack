from __future__ import annotations

from pathlib import Path

import pytest

from mdrack.ingestion.raw_source_provenance import (
    RAW_SOURCE_LOCATOR_KIND,
    RAW_SOURCE_PROVENANCE_SCHEMA,
    RawInputBudget,
    RawMediaKind,
    RawSignatureFact,
    RawSignatureKind,
    RawSourceError,
    RawSourceErrorCode,
    RawSourceProvenance,
    RawSourceSnapshot,
    canonical_json,
    capture_source,
    check_source_after,
    resource_metadata,
    sha256_digest,
    validate_budget,
    validate_signature_for_media,
    validate_source_ref,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "raw_source_provenance"


def png_fact() -> RawSignatureFact:
    return RawSignatureFact(RawSignatureKind.PNG, "image/png", "magic-v1")


def make_provenance(**changes: object) -> RawSourceProvenance:
    values: dict[str, object] = {
        "source_ref": "fixtures/tiny.png",
        "media_kind": RawMediaKind.IMAGE,
        "raw_source_sha256": sha256_digest(b"raw"),
        "byte_size": 3,
        "signature": png_fact(),
        "duration_ms": None,
        "selected_frame_count": 0,
        "prepared_evidence_sha256": sha256_digest(b"prepared"),
        "budget_fingerprint": RawInputBudget().fingerprint,
    }
    values.update(changes)
    return RawSourceProvenance(**values)  # type: ignore[arg-type]


def test_defaults_and_canonical_round_trip_keep_namespaces_separate() -> None:
    budget = RawInputBudget()
    assert budget.to_dict() == {
        "max_duration_ms": 3_600_000,
        "max_prepared_text_bytes": 8_388_608,
        "max_selected_frames": 600,
        "max_source_bytes": 33_554_432,
        "max_whole_resource_tokens": 8000,
    }
    provenance = make_provenance()
    encoded = canonical_json(provenance.to_dict())
    assert encoded == canonical_json(provenance.to_dict())
    restored = RawSourceProvenance.from_dict(provenance.to_dict())
    assert restored == provenance
    assert restored.content_hash == restored.raw_source_sha256
    assert restored.prepared_evidence_sha256 != restored.content_hash
    assert restored.locator.kind == RAW_SOURCE_LOCATOR_KIND
    assert restored.locator.payload == {"source_ref": "fixtures/tiny.png"}
    assert restored.to_dict()["schema"] == RAW_SOURCE_PROVENANCE_SCHEMA


@pytest.mark.parametrize("value", ["", "/tmp/a", "../a", "a/../b", "a\\b", "C:a", "a//b", "./a"])
def test_source_ref_is_relative_normalized_posix(value: str) -> None:
    with pytest.raises(RawSourceError) as caught:
        validate_source_ref(value)
    assert caught.value.code is RawSourceErrorCode.SOURCE_REF_INVALID
    assert str(caught.value) == "source_ref_invalid"


@pytest.mark.parametrize(
    ("kind", "mime"),
    [
        (RawSignatureKind.PNG, "image/png"),
        (RawSignatureKind.JPEG, "image/jpeg"),
        (RawSignatureKind.GIF, "image/gif"),
        (RawSignatureKind.WEBP, "image/webp"),
        (RawSignatureKind.RIFF_WAVE, "audio/wav"),
        (RawSignatureKind.ISO_BMFF, "video/mp4"),
        (RawSignatureKind.MATROSKA_EBML, "video/x-matroska"),
        (RawSignatureKind.UTF8_TEXT, "text/plain"),
        (RawSignatureKind.UTF8_TEXT, "application/json"),
    ],
)
def test_signature_facts_require_verified_mime(kind: RawSignatureKind, mime: str) -> None:
    fact = RawSignatureFact(kind, mime, "probe-v1")
    assert fact.to_dict()["kind"] == kind.value


def test_signature_and_media_kind_mismatch_is_fixed_error() -> None:
    with pytest.raises(RawSourceError) as caught:
        RawSignatureFact(RawSignatureKind.PNG, "audio/wav", "probe-v1")
    assert caught.value.code is RawSourceErrorCode.MIME_SIGNATURE_MISMATCH
    with pytest.raises(RawSourceError) as caught:
        validate_signature_for_media(RawMediaKind.VIDEO, png_fact())
    assert caught.value.code is RawSourceErrorCode.MIME_SIGNATURE_MISMATCH


def test_provenance_construction_and_decode_enforce_media_signature_consistency() -> None:
    with pytest.raises(RawSourceError) as caught:
        make_provenance(media_kind=RawMediaKind.VIDEO)
    assert caught.value.code is RawSourceErrorCode.MIME_SIGNATURE_MISMATCH

    payload = make_provenance().to_dict()
    payload["media_kind"] = RawMediaKind.VIDEO.value
    with pytest.raises(RawSourceError) as caught:
        RawSourceProvenance.from_dict(payload)
    assert caught.value.code is RawSourceErrorCode.MIME_SIGNATURE_MISMATCH
    assert "image/png" not in str(caught.value)


def test_json_utf8_text_mime_is_text_on_construction_and_decode() -> None:
    signature = RawSignatureFact(RawSignatureKind.UTF8_TEXT, "application/json", "probe-v1")
    provenance = make_provenance(
        source_ref="fixtures/data.json",
        media_kind=RawMediaKind.TEXT,
        signature=signature,
    )
    assert RawSourceProvenance.from_dict(provenance.to_dict()) == provenance


@pytest.mark.parametrize("field", ["source_ref", "budget_fingerprint", "raw_source_sha256"])
def test_from_dict_rejects_non_string_fields_without_coercion(field: str) -> None:
    payload = make_provenance().to_dict()
    payload[field] = 7
    with pytest.raises(RawSourceError) as caught:
        RawSourceProvenance.from_dict(payload)
    assert caught.value.code is RawSourceErrorCode.PREPARED_EVIDENCE_INVALID
    assert str(caught.value) == "prepared_evidence_invalid"


def test_invalid_direct_media_kind_is_payload_free() -> None:
    with pytest.raises(RawSourceError) as caught:
        make_provenance(media_kind="not-a-media-kind")
    assert caught.value.code is RawSourceErrorCode.PREPARED_EVIDENCE_INVALID
    assert str(caught.value) == "prepared_evidence_invalid"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("max_source_bytes", RawSourceErrorCode.SOURCE_TOO_LARGE),
        ("max_duration_ms", RawSourceErrorCode.DURATION_LIMIT_EXCEEDED),
        ("max_selected_frames", RawSourceErrorCode.SELECTED_FRAME_LIMIT_EXCEEDED),
    ],
)
def test_source_duration_and_frame_limits_are_inclusive(field: str, code: RawSourceErrorCode) -> None:
    budget = RawInputBudget(**{field: 1})
    kwargs: dict[str, object] = {}
    if field == "max_source_bytes":
        kwargs["byte_size"] = 2
    elif field == "max_duration_ms":
        kwargs.update(
            media_kind=RawMediaKind.AUDIO,
            signature=RawSignatureFact(RawSignatureKind.RIFF_WAVE, "audio/wav", "p"),
            duration_ms=2,
        )
    else:
        kwargs.update(
            media_kind=RawMediaKind.VIDEO,
            signature=RawSignatureFact(RawSignatureKind.ISO_BMFF, "video/mp4", "p"),
            duration_ms=1,
            selected_frame_count=2,
        )
    with pytest.raises(RawSourceError) as caught:
        validate_budget(make_provenance(**kwargs), budget)
    assert caught.value.code is code
    boundary = dict(kwargs)
    if field == "max_source_bytes":
        boundary["byte_size"] = 1
    elif field == "max_duration_ms":
        boundary["duration_ms"] = 1
    else:
        boundary["selected_frame_count"] = 1
    validate_budget(make_provenance(**boundary), budget)


def test_prepared_text_and_token_limits() -> None:
    provenance = make_provenance()
    with pytest.raises(RawSourceError) as caught:
        validate_budget(provenance, RawInputBudget(max_prepared_text_bytes=2), prepared_text="три")
    assert caught.value.code is RawSourceErrorCode.PREPARED_TEXT_LIMIT_EXCEEDED
    with pytest.raises(RawSourceError) as caught:
        validate_budget(provenance, RawInputBudget(max_whole_resource_tokens=2), whole_resource_tokens=3)
    assert caught.value.code is RawSourceErrorCode.PREPARED_EVIDENCE_INVALID


def test_capture_snapshot_and_source_changed_are_private_and_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "private-sentinel.png"
    source.write_bytes((FIXTURES / "tiny.png").read_bytes())
    snapshot = capture_source(source, "fixtures/tiny.png", RawMediaKind.IMAGE, png_fact())
    try:
        assert isinstance(snapshot, RawSourceSnapshot)
        assert snapshot.source_bytes == source.read_bytes()
        assert snapshot.temporary_path.read_bytes() == source.read_bytes()
        assert "private-sentinel" not in repr(snapshot)
        assert not hasattr(snapshot, "to_dict")
        check_source_after(snapshot, source)
        source.write_bytes(b"changed")
        with pytest.raises(RawSourceError) as caught:
            check_source_after(snapshot, source)
        assert caught.value.code is RawSourceErrorCode.SOURCE_CHANGED
        assert str(caught.value) == "source_changed"
    finally:
        snapshot.cleanup()
    assert not snapshot.temporary_path.exists()


def test_allowlisted_metadata_excludes_path_and_content() -> None:
    metadata = resource_metadata(make_provenance())
    assert set(metadata) == {"provenance"}
    provenance_metadata = metadata["provenance"]
    assert isinstance(provenance_metadata, dict)
    assert set(provenance_metadata) == {
        "byte_size", "budget_fingerprint", "duration_ms", "media_kind",
        "prepared_evidence_sha256", "raw_source_sha256", "schema",
        "selected_frame_count", "signature", "source_ref",
    }
    assert "private-sentinel" not in repr(metadata)
    assert "MDRACK_PRIVATE_CONTENT_SENTINEL" not in repr(metadata)


def test_capture_and_after_check_read_only_one_byte_past_source_bound(tmp_path: Path) -> None:
    source = tmp_path / "bounded.png"
    source.write_bytes(b"1234")
    budget = RawInputBudget(max_source_bytes=3)
    with pytest.raises(RawSourceError) as caught:
        capture_source(source, "fixtures/bounded.png", RawMediaKind.IMAGE, png_fact(), budget)
    assert caught.value.code is RawSourceErrorCode.SOURCE_TOO_LARGE

    source.write_bytes(b"123")
    snapshot = capture_source(source, "fixtures/bounded.png", RawMediaKind.IMAGE, png_fact(), budget)
    try:
        source.write_bytes(b"1234")
        with pytest.raises(RawSourceError) as caught:
            check_source_after(snapshot, source, budget)
        assert caught.value.code is RawSourceErrorCode.SOURCE_CHANGED
    finally:
        snapshot.cleanup()


def test_after_check_uses_capture_bound_for_default_and_equal_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "same-bound.png"
    source.write_bytes(b"123")
    budget = RawInputBudget(max_source_bytes=3)
    snapshot = capture_source(source, "fixtures/same-bound.png", RawMediaKind.IMAGE, png_fact(), budget)
    reads: list[int] = []
    original_open = Path.open

    def spy_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        handle = original_open(self, *args, **kwargs)
        if self == source:
            original_read = handle.read

            def spy_read(size: int = -1):  # type: ignore[no-untyped-def]
                reads.append(size)
                return original_read(size)

            handle.read = spy_read  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(Path, "open", spy_open)
    try:
        check_source_after(snapshot, source)
        check_source_after(snapshot, source, budget)
        assert reads == [4, 4]
        reads.clear()
        with pytest.raises(RawSourceError) as caught:
            check_source_after(snapshot, source, RawInputBudget(max_source_bytes=4))
        assert caught.value.code is RawSourceErrorCode.SOURCE_CHANGED
        assert reads == []
    finally:
        snapshot.cleanup()
