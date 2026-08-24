"""Configured-catalog lifecycle parity for the singular resource CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.public_api import MDRackEngine


def _manifest() -> bytes:
    return json.dumps(
        {
            "contract": "mdrack.prepared-resource",
            "version": 1,
            "resource": {
                "resource_id": "resource-1",
                "resource_kind": "video",
                "media_type": "video/mp4",
                "source_namespace": "fixture",
                "locator": {"kind": "external_record", "payload": {"ref": "PRIVATE_LOCATOR"}},
                "metadata": {"source": {"secret": "PRIVATE_METADATA"}},
            },
            "representations": [
                {
                    "representation_id": "representation-1",
                    "resource_id": "resource-1",
                    "representation_kind": "frame_caption",
                    "modality": "text",
                    "text": "PRIVATE_TEXT",
                    "metadata": {},
                }
            ],
            "units": [
                {
                    "unit_id": "unit-1",
                    "resource_id": "resource-1",
                    "representation_id": "representation-1",
                    "unit_kind": "frame",
                    "modality": "text",
                    "text": "PRIVATE_TEXT",
                    "evidence_locator": {"kind": "video_frame", "payload": {"timestamp_ms": 1000}},
                    "ordinal": 0,
                    "metadata": {},
                }
            ],
            "spaces": [],
            "vectors": [],
            "facets": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def test_resource_lifecycle_uses_the_configured_catalog_and_matches_engine(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    runner = CliRunner()
    initialized = runner.invoke(main, ["--root", str(root), "init"])
    assert initialized.exit_code == 0, initialized.output

    engine = MDRackEngine(root=root, config=MDRackConfig())
    try:
        imported = engine.import_resource_manifest(_manifest()).to_dict()
        engine_unit = engine.read_unit("unit-1")
    finally:
        engine.close()

    assert engine_unit == {
        "unit_id": "unit-1",
        "resource_id": "resource-1",
        "representation_id": "representation-1",
        "unit_kind": "frame",
        "modality": "text",
        "text": "PRIVATE_TEXT",
        "evidence_locator": {"kind": "video_frame", "payload": {"timestamp_ms": 1000}},
        "ordinal": 0,
    }

    output_path = root / "resource.json"
    exported = runner.invoke(
        main,
        ["--root", str(root), "resource", "export", "resource-1", "--output", str(output_path)],
    )
    inspected = runner.invoke(main, ["--root", str(root), "resource", "inspect", "resource-1"])
    deleted = runner.invoke(main, ["--root", str(root), "resource", "delete", "resource-1"])
    restored = runner.invoke(main, ["--root", str(root), "resource", "import", str(output_path)])

    assert exported.exit_code == inspected.exit_code == deleted.exit_code == restored.exit_code == 0
    assert json.loads(restored.output)["data"] == imported
    assert json.loads(inspected.output)["data"]["counts"] == {
        "representations": 1,
        "units": 1,
        "spaces": 0,
        "vectors": 0,
        "facets": 0,
    }
    assert json.loads(deleted.output)["data"] == {"resource_id": "resource-1", "deleted": True}

    reopened = MDRackEngine(root=root, config=MDRackConfig())
    try:
        assert reopened.export_resource_manifest("resource-1") == output_path.read_bytes()
    finally:
        reopened.close()

    captured = "".join((exported.output, inspected.output, deleted.output, restored.output))
    assert "PRIVATE_" not in captured
    assert str(root) not in captured
    assert list((root / ".mdrack").glob("*.sqlite3")) == [root / ".mdrack" / "catalog.sqlite3"]
