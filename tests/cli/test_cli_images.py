from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main

PNG_BYTES = b"\x89PNG\r\n\x1a\ncli-image"


def test_image_ingest_and_text_search_use_portable_ref(tmp_path: Path) -> None:
    source = tmp_path.parent / "cli-image.png"
    source.write_bytes(PNG_BYTES)
    initialized = CliRunner().invoke(main, ["--root", str(tmp_path), "init"])
    assert initialized.exit_code == 0, initialized.output
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "image",
            "ingest",
            str(source),
            "--resource-id",
            "cli-image-1",
            "--source-namespace",
            "cli",
            "--source-ref",
            "images/cli-image.png",
            "--caption",
            "portable caption",
            "--provider",
            "fake",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["media_type"] == "image/png"
    assert str(source) not in result.stdout
    assert "portable caption" not in result.stdout

    search = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "image", "search", "portable", "--mode", "text"],
    )
    assert search.exit_code == 0, search.output
    search_payload = json.loads(search.stdout)
    assert search_payload["data"]["results"][0]["source_ref"] == "images/cli-image.png"


def test_image_ingest_rejects_nonportable_ref_without_payload(tmp_path: Path) -> None:
    source = tmp_path.parent / "cli-image-invalid-ref.png"
    source.write_bytes(PNG_BYTES)
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "image",
            "ingest",
            str(source),
            "--resource-id",
            "cli-image-invalid",
            "--source-namespace",
            "cli",
            "--source-ref",
            str(source),
            "--caption",
            "PRIVATE_IMAGE_CAPTION_SENTINEL",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "IMAGE_INGEST_ERROR"
    assert str(source) not in result.stdout
    assert "PRIVATE_IMAGE_CAPTION_SENTINEL" not in result.stdout
