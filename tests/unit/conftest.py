"""Marker for the deterministic unit-test lane."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    marker = pytest.mark.unit
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if nodeid.startswith("tests/unit/"):
            item.add_marker(marker)
