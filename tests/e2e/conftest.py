"""Marker for the offline end-to-end test lane."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    marker = pytest.mark.e2e
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if nodeid.startswith("tests/e2e/"):
            item.add_marker(marker)
