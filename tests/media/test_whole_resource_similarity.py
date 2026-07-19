from __future__ import annotations

import pytest

from mdrack_media import weighted_centroid


def test_weighted_centroid_is_order_independent_and_normalized() -> None:
    first = weighted_centroid({"b": (0.0, 2.0), "a": (2.0, 0.0)}, {"a": 1, "b": 3})
    second = weighted_centroid({"a": (2.0, 0.0), "b": (0.0, 2.0)}, {"b": 3, "a": 1})
    assert first == second
    assert first[0] == pytest.approx(0.316227766)
    assert first[1] == pytest.approx(0.948683298)


def test_weighted_centroid_rejects_zero_norm() -> None:
    with pytest.raises(ValueError, match="zero norm"):
        weighted_centroid({"a": (1.0, 0.0), "b": (-1.0, 0.0)}, {"a": 1, "b": 1})
