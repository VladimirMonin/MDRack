"""Actual message-only sinks, not LogRecord.extra, are the evidence surface."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
from mdrack.cli import main
from mdrack.config.models import MDRackConfig


def test_fixed_command_error_fields_reach_flushed_file(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("mdrack.cli")
    logger.addHandler(handler)
    try:
        result = CliRunner().invoke(main, ["--root", str(tmp_path), "--config-file",
                                         str(tmp_path / "PRIVATE_PATH_SENTINEL"), "status"])
        handler.flush()
        output = log_path.read_text(encoding="utf-8")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "CONFIG_ERROR"
    assert output
    assert '"reason":"config_invalid"' in output
    assert '"status":"failed"' in output
    assert "PRIVATE_PATH_SENTINEL" not in output + result.stdout + result.stderr


def test_actual_image_failure_and_empty_search_fields_reach_same_file(tmp_path: Path) -> None:
    assert CliRunner().invoke(main, ["--root", str(tmp_path), "init"]).exit_code == 0
    path = tmp_path / "image-events.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("mdrack.cli.commands.images")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    class Broken(logging.Handler):
        calls = 0

        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage().startswith("cli.image.search.completed "):
                self.calls += 1
                raise OSError("PRIVATE_EXCEPTION_SENTINEL")

    broken = Broken()
    logger.addHandler(broken)
    try:
        failure = CliRunner().invoke(main, [
            "--root", str(tmp_path), "image", "ingest", str(tmp_path / "PRIVATE_PATH_SENTINEL.png"),
            "--resource-id", "fixture", "--source-namespace", "fixture", "--source-ref", "images/fixture.png",
            "--caption", "PRIVATE_CONTENT_SENTINEL", "--provider", "fake",
        ])
        assert failure.exit_code == 1
        assert json.loads(failure.stdout)["error"]["code"] == "IMAGE_INGEST_ERROR"
        handlers_before = tuple(logger.handlers)
        for _ in range(3):
            result = CliRunner().invoke(main, [
                "--root", str(tmp_path), "image", "search", "PRIVATE_QUERY_SENTINEL", "--mode", "text",
            ])
            assert result.exit_code == 0
            assert json.loads(result.stdout)["data"]["results"] == []
            assert result.stdout.count("\n") == 1
            assert tuple(logger.handlers) == handlers_before
        handler.flush()
        output = path.read_text(encoding="utf-8")
        assert broken.calls == 3
        assert output
        assert '"reason":"image_ingest_error"' in output
        assert '"representation_count":1' in output
        assert '"result_count":0' in output
        assert '"mode":"text"' in output
        assert '"limit":20' in output
        for private in ("PRIVATE_CONTENT_SENTINEL", "PRIVATE_PATH_SENTINEL", "PRIVATE_QUERY_SENTINEL",
                        "PRIVATE_EXCEPTION_SENTINEL"):
            assert private not in output + failure.stdout + failure.stderr + result.stdout + result.stderr
    finally:
        logger.removeHandler(handler)
        logger.removeHandler(broken)
        logger.setLevel(previous_level)
        handler.close()


@pytest.mark.parametrize("failed_event", [
    "core.index.completed", "image.ingest.completed", "cli.image.ingest.completed",
])
def test_committed_graph_survives_sink_failure_and_cli_process_returns_one_json(
    tmp_path: Path, failed_event: str,
) -> None:
    source = tmp_path / "PRIVATE_PATH_SENTINEL.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    initialized = CliRunner().invoke(main, ["--root", str(tmp_path), "init"])
    assert initialized.exit_code == 0
    log_path = tmp_path / "flushed.log"
    witness = tmp_path / "commit-witness.json"
    script = '''
import json, logging, sys
from pathlib import Path
from mdrack.cli import main
from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
from mdrack.config.models import MDRackConfig
root, source, log_path, witness, failed_event = sys.argv[1:]
class Broken(logging.Handler):
    def emit(self, record):
        if record.getMessage().split(" ", 1)[0] == failed_event:
            catalog = open_application_catalog_readonly(Path(root), MDRackConfig())
            try:
                counts = catalog.verify()
                data = {"resources": counts.resources, "units": counts.units}
                Path(witness).write_text(json.dumps(data), encoding="utf-8")
            finally:
                catalog.close()
            raise OSError("PRIVATE_EXCEPTION_SENTINEL https://PRIVATE_HOST.invalid/private-api")
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(Broken())
handler = logging.FileHandler(log_path, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(handler)
try:
    main(["--root", root, "image", "ingest", source, "--resource-id", "fixture-image",
          "--source-namespace", "fixture", "--source-ref", "images/fixture.png",
          "--caption", "PRIVATE_CONTENT_SENTINEL", "--provider", "fake"])
finally:
    handler.flush()
    handler.close()
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path), str(source), str(log_path), str(witness), failed_event],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    payload, end = json.JSONDecoder().raw_decode(result.stdout)
    assert not result.stdout[end:].strip()
    assert payload["ok"] is True
    assert json.loads(witness.read_text(encoding="utf-8")) == {"resources": 1, "units": 2}
    catalog = open_application_catalog_readonly(tmp_path, MDRackConfig())
    try:
        counts = catalog.verify()
        assert (counts.resources, counts.representations, counts.units, counts.vectors) == (1, 2, 2, 2)
        assert catalog.read_resource("fixture-image") is not None
    finally:
        catalog.close()
    output = log_path.read_text(encoding="utf-8")
    assert output
    assert '"representation_count":2' in output
    assert '"unit_count":2' in output
    assert '"vector_count":2' in output
    assert '"elapsed_ms":' in output
    for private in ("PRIVATE_CONTENT_SENTINEL", "PRIVATE_PATH_SENTINEL", "PRIVATE_EXCEPTION_SENTINEL",
                    "PRIVATE_HOST", "private-api"):
        assert private not in output + result.stdout + result.stderr
