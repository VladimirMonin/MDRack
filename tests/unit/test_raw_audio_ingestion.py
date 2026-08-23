import wave
from pathlib import Path

import pytest

import mdrack.ingestion.raw_audio as raw_audio_module
from mdrack.ingestion.raw_audio import RawAudioIngestionService
from mdrack.ingestion.raw_source_provenance import RawSourceError, RawSourceErrorCode


class Catalog:
    def __init__(self) -> None:
        self.batches = []

    def replace_resource(self, batch: object) -> None:
        self.batches.append(batch)

    def delete_resource(self, resource_id: str) -> None:
        del resource_id


def make_wave(path: Path) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000)


def make_stt(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "assert sys.stdin.buffer.read()\n"
        "print(json.dumps({'schema': 'mdrack.timed-transcript.v1', 'atoms': "
        "[{'start_ms': 0, 'end_ms': 500, 'text': 'audio needle'}]}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_wave_ingestion_uses_snapshot_and_raw_provenance(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source.wav"
    command = tmp_path / "stt.py"
    make_wave(source)
    make_stt(command)
    catalog = Catalog()

    result = RawAudioIngestionService(catalog).ingest(
        source,
        source_ref="recordings/source.wav",
        root=root,
        stt_command=str(command),
        allow_external_stt=True,
    )

    assert result.resource_kind == "audio"
    assert result.media_type == "audio/wav"
    assert len(catalog.batches) == 1
    batch = catalog.batches[0]
    assert batch.resource.content_hash.startswith("sha256:")
    assert batch.resource.locator.payload == {"source_ref": "recordings/source.wav"}
    assert batch.resource.metadata["provenance"]["duration_ms"] == 1000


def test_non_wave_is_rejected_without_catalog_write(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source.bin"
    source.write_bytes(b"not wave")
    catalog = Catalog()

    with pytest.raises(RawSourceError):
        RawAudioIngestionService(catalog).ingest(
            source,
            source_ref="source.bin",
            root=root,
            stt_command="missing-command",
            allow_external_stt=True,
        )
    assert catalog.batches == []


def test_stt_stdout_is_bounded_during_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source.wav"
    command = tmp_path / "overflow.py"
    make_wave(source)
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(b'x' * 4096)\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    monkeypatch.setattr(raw_audio_module, "MAX_STT_STDOUT_BYTES", 64)
    catalog = Catalog()

    with pytest.raises(RawSourceError) as caught:
        RawAudioIngestionService(catalog).ingest(
            source,
            source_ref="recordings/source.wav",
            root=root,
            stt_command=str(command),
            allow_external_stt=True,
        )

    assert caught.value.code is RawSourceErrorCode.PREPARED_TEXT_LIMIT_EXCEEDED
    assert catalog.batches == []
