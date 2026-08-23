import json
import wave
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_ingest_audio_emits_safe_success(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "private-audio.wav"
    command = tmp_path / "fake-stt.py"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000)
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "print('{\"schema\":\"mdrack.timed-transcript.v1\",\"atoms\":["
        "{\"start_ms\":0,\"end_ms\":100,\"text\":\"cli audio\"}]}')\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    result = CliRunner().invoke(
        main,
        [
            "--root", str(root), "ingest", "audio", str(source),
            "--source-ref", "recordings/private.wav",
            "--allow-external-stt", "--stt-command", str(command),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["resource_kind"] == "audio"
    assert str(source) not in result.stdout
    assert "cli audio" not in result.stdout


def test_ingest_audio_requires_authorization(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "audio.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000)
    result = CliRunner().invoke(
        main,
        [
            "--root", str(root), "ingest", "audio", str(source),
            "--source-ref", "audio.wav", "--stt-command", "missing-command",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "RAW_AUDIO_INGEST_ERROR"
    assert "missing-command" not in result.stdout


def test_ingest_audio_missing_command_emits_safe_json(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "audio.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000)

    result = CliRunner().invoke(
        main,
        [
            "--root", str(root), "ingest", "audio", str(source),
            "--source-ref", "audio.wav", "--allow-external-stt",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "RAW_AUDIO_INGEST_ERROR"
    assert str(source) not in result.stdout
