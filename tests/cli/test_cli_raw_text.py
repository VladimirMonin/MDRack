import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_ingest_text_emits_safe_success(tmp_path: Path) -> None:
    source = tmp_path.parent / "raw-cli-source.txt"
    source.write_text("cli needle", encoding="utf-8")
    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "ingest", "text", str(source), "--source-ref", "docs/cli.txt", "--media-type", "text/plain"],  # noqa: E501
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["resource_kind"] == "raw_text"
    assert str(source) not in result.stdout
    assert "cli needle" not in result.stdout


def test_ingest_text_missing_absolute_source_emits_safe_json(tmp_path: Path) -> None:
    source = tmp_path.parent / "PRIVATE_MISSING_SOURCE_SENTINEL.txt"
    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "ingest", "text", str(source), "--source-ref", "docs/missing.txt", "--media-type", "text/plain"],  # noqa: E501
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "RAW_TEXT_INGEST_ERROR"
    assert str(source) not in result.stdout
    assert "PRIVATE_MISSING_SOURCE_SENTINEL" not in result.stdout
