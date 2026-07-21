from __future__ import annotations

from typing import Any

import pytest

from tests.evaluation.v1_1.offline_runner import execute_twice

pytestmark = [pytest.mark.e2e, pytest.mark.offline]


def test_scenarios_a_to_d_use_production_facades_and_disposable_catalogs() -> None:
    report: dict[str, Any] = execute_twice()

    assert report["execution"]["repeats"] == 2
    assert report["execution"]["fresh_disposable_catalogs"] == 2
    assert report["execution"]["disposable_catalogs_removed"] is True
    assert all(
        set(run["scenarios"]) == {
            "A_metadata",
            "B_audio",
            "C_video",
            "D_degradation",
        }
        for run in report["runs"]
    )
    assert "universal_semantic_quality" in report["non_claims"]
