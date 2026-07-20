from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdrack.ingestion.transcripts import (
    TranscriptReadError,
    read_srt,
    read_timed_json,
    read_transcript,
    read_vtt,
    read_whisper_json,
)
from mdrack_media import ProducerFingerprint, resource_id

FIXTURES = Path(__file__).parents[2] / "assets" / "v1_1" / "transcripts"
RESOURCE_ID = resource_id("fixture", "media/example.wav")
PRODUCER = ProducerFingerprint.from_payload({"engine": "fixture", "version": 1})


def _semantic_atoms(result: object) -> tuple[tuple[object, ...], ...]:
    artifact = result.artifact  # type: ignore[attr-defined]
    return tuple(
        (
            atom.atom_id,
            atom.start_ms,
            atom.end_ms,
            atom.text,
            atom.ordinal,
        )
        for atom in artifact.atoms
    )


def test_four_readers_emit_equivalent_existing_artifact() -> None:
    whisper = read_whisper_json(
        (FIXTURES / "whisper.json").read_bytes(),
        resource_id=RESOURCE_ID,
        producer_fingerprint=PRODUCER,
    )
    vtt = read_vtt(
        (FIXTURES / "sample.vtt").read_text(encoding="utf-8"),
        resource_id=RESOURCE_ID,
        producer_fingerprint=PRODUCER,
        language="ru",
    )
    srt = read_srt(
        (FIXTURES / "sample.srt").read_text(encoding="utf-8"),
        resource_id=RESOURCE_ID,
        producer_fingerprint=PRODUCER,
        language="ru",
    )
    timed = read_timed_json(
        (FIXTURES / "timed.json").read_text(encoding="utf-8"),
        resource_id=RESOURCE_ID,
        producer_fingerprint=PRODUCER,
    )

    outputs = (whisper, vtt, srt, timed)
    assert all(item.artifact.language == "ru" for item in outputs)
    assert all(item.artifact.duration_ms == 4250 for item in outputs)
    assert all(item.artifact.representation_id == whisper.artifact.representation_id for item in outputs)
    assert all(_semantic_atoms(item) == _semantic_atoms(whisper) for item in outputs)
    assert [atom.text for atom in whisper.artifact.atoms] == ["Первая фраза.", "Вторая фраза."]
    assert whisper.artifact.atoms[0].start_ms == 1000
    assert whisper.artifact.atoms[0].end_ms == 2501
    assert len(whisper.artifact.atoms) == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("whisper.json", "whisper_json"),
        ("sample.vtt", "webvtt"),
        ("sample.srt", "srt"),
        ("timed.json", "timed_json_v1"),
    ],
)
def test_autodetection_is_deterministic(name: str, expected: str) -> None:
    source = (FIXTURES / name).read_bytes()
    first = read_transcript(source, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER, language="ru")
    second = read_transcript(source, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER, language="ru")
    assert first.detected_format == expected
    assert first.artifact == second.artifact
    assert first.source_fingerprint == second.source_fingerprint


def test_source_digest_tracks_exact_input_not_only_parsed_atoms() -> None:
    compact = json.dumps(json.loads((FIXTURES / "whisper.json").read_text(encoding="utf-8")))
    pretty = (FIXTURES / "whisper.json").read_text(encoding="utf-8")
    compact_result = read_whisper_json(
        compact, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER
    )
    pretty_result = read_whisper_json(
        pretty, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER
    )
    assert _semantic_atoms(compact_result) == _semantic_atoms(pretty_result)
    assert compact_result.source_fingerprint != pretty_result.source_fingerprint


def test_strict_failure_and_lenient_skip_use_safe_diagnostics() -> None:
    sentinel = "PRIVATE transcript payload must not leak"
    source = json.dumps(
        {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "kept"},
                {"start": 0.5, "end": 2.0, "text": sentinel},
                {"start": 2.0, "end": 3.0, "text": "also kept"},
            ]
        }
    )
    with pytest.raises(TranscriptReadError) as captured:
        read_whisper_json(
            source,
            resource_id=RESOURCE_ID,
            producer_fingerprint=PRODUCER,
        )
    assert captured.value.code == "overlapping_interval"
    assert sentinel not in str(captured.value)

    result = read_whisper_json(
        source,
        resource_id=RESOURCE_ID,
        producer_fingerprint=PRODUCER,
        strict=False,
    )
    assert [atom.text for atom in result.artifact.atoms] == ["kept", "also kept"]
    assert [item.code for item in result.diagnostics] == ["overlapping_interval"]
    assert sentinel not in repr(result.diagnostics)


def test_timed_json_requires_the_frozen_schema() -> None:
    source = {"schema": "other", "atoms": []}
    with pytest.raises(TranscriptReadError) as captured:
        read_timed_json(source, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER)
    assert captured.value.code == "unsupported_schema"


def test_vtt_accepts_header_metadata_crlf_bom_settings_and_cue_markup() -> None:
    source = (
        "\ufeffWEBVTT\r\nKind: captions\r\nLanguage: ru\r\n\r\n"
        "cue-a\r\n00:00:00.000 --> 00:00:01.000 align:start\r\n"
        "<v Speaker>Привет&nbsp;мир</v>\r\n"
    )
    result = read_vtt(
        source,
        resource_id=RESOURCE_ID,
        producer_fingerprint=PRODUCER,
        language="ru",
    )
    assert result.artifact.atoms[0].text == "Привет мир"
    assert result.artifact.atoms[0].metadata["source_cue_id"] == "cue-a"


def test_format_detection_failure_is_safe() -> None:
    sentinel = "PRIVATE unsupported transcript"
    with pytest.raises(TranscriptReadError) as captured:
        read_transcript(sentinel, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER)
    assert captured.value.code == "unknown_format"
    assert sentinel not in str(captured.value)


def test_direct_string_surrogates_use_stable_source_error_codes() -> None:
    sentinel = "PRIVATE transcript payload must not leak"
    json_source = (
        '{"segments": [{"start": 0, "end": 1, "text": "'
        + sentinel
        + "\ud800"
        + '"}]}'
    )
    for reader in (read_whisper_json, read_transcript):
        with pytest.raises(TranscriptReadError) as captured:
            reader(json_source, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER)
        assert captured.value.code == "invalid_json"
        assert captured.value.source_ordinal is None
        assert sentinel not in str(captured.value)

    subtitle_sources = (
        (
            read_vtt,
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n" + sentinel + "\ud800\n",
        ),
        (
            read_srt,
            "1\n00:00:00,000 --> 00:00:01,000\n" + sentinel + "\ud800\n",
        ),
    )
    for reader, source in subtitle_sources:
        with pytest.raises(TranscriptReadError) as captured:
            reader(source, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER)
        assert captured.value.code == "invalid_subtitle"
        assert captured.value.source_ordinal is None
        assert sentinel not in str(captured.value)


@pytest.mark.parametrize("end_seconds", ["1e999999999", 1e308])
def test_extreme_whisper_seconds_preserve_strict_and_lenient_diagnostics(
    end_seconds: object,
) -> None:
    sentinel = "PRIVATE extreme timing payload"
    source = {"segments": [{"start": 0, "end": end_seconds, "text": sentinel}]}

    with pytest.raises(TranscriptReadError) as captured:
        read_whisper_json(source, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER)
    assert captured.value.code == "invalid_timing"
    assert captured.value.source_ordinal == 0
    assert sentinel not in str(captured.value)

    result = read_whisper_json(
        source,
        resource_id=RESOURCE_ID,
        producer_fingerprint=PRODUCER,
        strict=False,
    )
    assert result.artifact.atoms == ()
    assert [(item.code, item.source_ordinal) for item in result.diagnostics] == [
        ("invalid_timing", 0)
    ]
    assert sentinel not in repr(result.diagnostics)


def test_oversized_subtitle_hours_preserve_strict_and_lenient_diagnostics() -> None:
    oversized_hours = "9" * 5000
    sentinel = "PRIVATE oversized subtitle payload"
    sources = (
        (read_vtt, f"WEBVTT\n\n{oversized_hours}:00:00.000 --> 00:00:01.000\n{sentinel}\n"),
        (read_srt, f"1\n{oversized_hours}:00:00,000 --> 00:00:01,000\n{sentinel}\n"),
    )

    for reader, source in sources:
        with pytest.raises(TranscriptReadError) as captured:
            reader(source, resource_id=RESOURCE_ID, producer_fingerprint=PRODUCER)
        assert captured.value.code == "invalid_timing"
        assert captured.value.source_ordinal == 0
        assert sentinel not in str(captured.value)

        result = reader(
            source,
            resource_id=RESOURCE_ID,
            producer_fingerprint=PRODUCER,
            strict=False,
        )
        assert result.artifact.atoms == ()
        assert [(item.code, item.source_ordinal) for item in result.diagnostics] == [
            ("invalid_timing", 0)
        ]
        assert sentinel not in repr(result.diagnostics)
