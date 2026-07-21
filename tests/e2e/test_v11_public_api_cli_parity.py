"""MDRack 1.1 manifest parity through the embedded engine and CLI."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.ports.storage import KnowledgeStorage
from mdrack.public_api import MDRackEngine
from mdrack_sqlite import SQLiteCatalog


class _EngineStorage:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.resource_store = catalog

    def close(self) -> None:
        self.resource_store.close()


def _manifest() -> bytes:
    value: dict[str, Any] = {
        "contract": "mdrack.prepared-resource",
        "version": 1,
        "resource": {
            "resource_id": "video-1",
            "resource_kind": "video",
            "media_type": "video/mp4",
            "source_namespace": "fixture",
            "locator": {
                "kind": "external_record",
                "payload": {"ref": "PRIVATE_LOCATOR_SENTINEL"},
            },
            "content_hash": "sha256:fixture",
            "title": "PRIVATE_TITLE_SENTINEL",
            "metadata": {
                "source": {"secret": "PRIVATE_METADATA_SENTINEL"},
                "ingestion": {"adapter": "fixture"},
            },
        },
        "representations": [
            {
                "representation_id": "representation-1",
                "resource_id": "video-1",
                "representation_kind": "frame_caption",
                "modality": "text",
                "text": "PRIVATE_FRAME_TEXT_SENTINEL",
                "metadata": {},
            }
        ],
        "units": [
            {
                "unit_id": "unit-1",
                "resource_id": "video-1",
                "representation_id": "representation-1",
                "unit_kind": "frame",
                "modality": "text",
                "text": "PRIVATE_FRAME_TEXT_SENTINEL",
                "evidence_locator": {
                    "kind": "time_range",
                    "payload": {"start_ms": 1000, "end_ms": 2000},
                },
                "ordinal": 0,
                "metadata": {},
            }
        ],
        "spaces": [
            {
                "space_id": "space-1",
                "dimensions": 2,
                "metric": "dot",
                "fingerprint": "fixture-space-v1",
                "metadata": {},
            }
        ],
        "vectors": [{"unit_id": "unit-1", "space_id": "space-1", "vector": [1.0, 0.0]}],
        "facets": [],
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def test_engine_and_cli_manifest_round_trip_are_byte_identical_and_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked_network)
    catalog_path = tmp_path / "catalog.sqlite3"
    restored_path = tmp_path / "restored.sqlite3"
    export_path = tmp_path / "export.json"
    redacted_path = tmp_path / "redacted.json"
    lexical_path = tmp_path / "lexical.json"
    SQLiteCatalog.create(catalog_path).close()
    SQLiteCatalog.create(restored_path).close()

    engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        storage=cast(KnowledgeStorage, _EngineStorage(SQLiteCatalog.open(catalog_path))),
    )
    try:
        imported = engine.import_resource_manifest(_manifest()).to_dict()
        api_payload = engine.export_resource_manifest("video-1")
    finally:
        engine.close()

    cli_export = CliRunner().invoke(
        main,
        [
            "resource",
            "export",
            "video-1",
            "--catalog",
            str(catalog_path),
            "--output",
            str(export_path),
        ],
    )
    cli_import = CliRunner().invoke(
        main,
        [
            "resource",
            "import",
            str(export_path),
            "--catalog",
            str(restored_path),
        ],
    )

    cli_redacted = CliRunner().invoke(
        main,
        [
            "resource",
            "export",
            "video-1",
            "--catalog",
            str(catalog_path),
            "--output",
            str(redacted_path),
            "--redact-source-metadata",
        ],
    )
    cli_lexical = CliRunner().invoke(
        main,
        [
            "resource",
            "export",
            "video-1",
            "--catalog",
            str(catalog_path),
            "--output",
            str(lexical_path),
            "--no-vectors",
        ],
    )
    redacted_bytes = redacted_path.read_bytes()
    cli_collision = CliRunner().invoke(
        main,
        [
            "resource",
            "export",
            "video-1",
            "--catalog",
            str(catalog_path),
            "--output",
            str(redacted_path),
        ],
    )

    assert cli_export.exit_code == cli_import.exit_code == 0
    assert imported == json.loads(cli_import.stdout)["data"]
    assert api_payload == export_path.read_bytes()
    assert json.loads(api_payload)["contract"] == "mdrack.prepared-resource"
    assert json.loads(api_payload)["version"] == 1
    assert cli_redacted.exit_code == 0
    assert cli_lexical.exit_code == 0
    assert json.loads(cli_lexical.stdout)["data"]["counts"]["vectors"] == 0
    assert json.loads(cli_lexical.stdout)["data"]["counts"]["spaces"] == 0
    assert json.loads(lexical_path.read_bytes())["vectors"] == []
    assert json.loads(lexical_path.read_bytes())["spaces"] == []
    assert json.loads(redacted_bytes)["resource"]["metadata"] == {
        "ingestion": {"adapter": "fixture"}
    }
    assert b"PRIVATE_METADATA_SENTINEL" not in redacted_bytes
    assert cli_collision.exit_code == 1
    assert json.loads(cli_collision.stdout)["error"] == {
        "message": "Prepared resource export failed",
        "code": "RESOURCE_MANIFEST_OUTPUT_UNAVAILABLE",
    }
    assert redacted_path.read_bytes() == redacted_bytes
    captured = "".join(
        (
            cli_export.stdout,
            cli_export.stderr,
            cli_import.stdout,
            cli_import.stderr,
            cli_redacted.stdout,
            cli_redacted.stderr,
            cli_lexical.stdout,
            cli_lexical.stderr,
            cli_collision.stdout,
            cli_collision.stderr,
        )
    )
    assert "PRIVATE_" not in captured
    assert str(tmp_path) not in captured

    restored_engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        storage=cast(KnowledgeStorage, _EngineStorage(SQLiteCatalog.open(restored_path))),
    )
    try:
        assert restored_engine.export_resource_manifest("video-1") == api_payload
    finally:
        restored_engine.close()
