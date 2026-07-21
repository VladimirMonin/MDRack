"""Privacy checks for the frozen MDRack 1.1 evaluation inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from mdrack.eval.privacy import scan_privacy

ROOT = Path(__file__).resolve().parents[3]
SENTINELS = ROOT / "tests/privacy/v1_1/sentinels.json"


def _load() -> dict[str, Any]:
    value = json.loads(SENTINELS.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_every_frozen_sentinel_is_detected_without_echo() -> None:
    fixture = _load()
    values = list(fixture["forbidden_values"].values())
    keys = list(fixture["forbidden_keys"])
    payload: object = {"nested": [{key: values} for key in keys]}

    result = scan_privacy(payload, forbidden_values=values, forbidden_keys=keys)

    assert result.safe is False
    rendered = json.dumps(result.to_dict(), sort_keys=True)
    assert all(token not in rendered for token in (*values, *keys))


def test_aggregate_freeze_record_is_privacy_safe() -> None:
    fixture = _load()
    values = list(fixture["forbidden_values"].values())
    keys = list(fixture["forbidden_keys"])
    safe_record = {
        "schema_version": 1,
        "phase": "input_freeze",
        "resources": 50,
        "queries": 170,
        "bundle_digest": "sha256:" + "a" * 64,
    }

    assert scan_privacy(safe_record, forbidden_values=values, forbidden_keys=keys).safe
