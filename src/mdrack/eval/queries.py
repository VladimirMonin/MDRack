"""Retrieval eval query format — YAML-based eval query definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_MODES = frozenset({"text", "semantic", "hybrid"})
VALID_EXPECTED_KEYS = frozenset({
    "content_contains",
    "file_path_contains",
    "heading_contains",
})
VALID_METRIC_KEYS = frozenset({"recall_at"})


class EvalQueryError(Exception):
    """Base exception for eval query loading/validation errors."""


@dataclass
class EvalQuery:
    """A single retrieval eval query with expected results and metrics."""

    id: str
    query: str
    mode: str
    expected: dict[str, str]
    metrics: dict[str, Any]


@dataclass
class EvalQuerySet:
    """A collection of eval queries loaded from a YAML file."""

    queries: list[EvalQuery] = field(default_factory=list)


def _validate_expected(expected: Any, query_id: str) -> dict[str, str]:
    if not isinstance(expected, dict):
        raise EvalQueryError(f"Expected clauses for query '{query_id}' must be a mapping")

    if not expected:
        raise EvalQueryError(f"Expected clauses for query '{query_id}' must not be empty")

    unsupported = sorted(set(expected) - VALID_EXPECTED_KEYS)
    if unsupported:
        raise EvalQueryError(
            f"Unsupported expected clauses for query '{query_id}': {unsupported}. "
            f"Supported clauses: {sorted(VALID_EXPECTED_KEYS)}"
        )

    normalized: dict[str, str] = {}
    for key, value in expected.items():
        if not isinstance(value, str) or not value.strip():
            raise EvalQueryError(
                f"Expected clause '{key}' for query '{query_id}' must be a non-empty string"
            )
        normalized[key] = value.strip()

    return normalized


def _validate_metrics(metrics: Any, query_id: str) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        raise EvalQueryError(f"Metrics for query '{query_id}' must be a mapping")

    if not metrics:
        raise EvalQueryError(f"Metrics for query '{query_id}' must not be empty")

    unsupported = sorted(set(metrics) - VALID_METRIC_KEYS)
    if unsupported:
        raise EvalQueryError(
            f"Unsupported metrics for query '{query_id}': {unsupported}. "
            f"Supported metrics: {sorted(VALID_METRIC_KEYS)}"
        )

    recall_at = metrics.get("recall_at")
    if recall_at is not None and (not isinstance(recall_at, int) or recall_at <= 0):
        raise EvalQueryError(
            f"Metric 'recall_at' for query '{query_id}' must be a positive integer"
        )

    return dict(metrics)


def _validate_query(q: dict[str, Any]) -> EvalQuery:
    required = ["id", "query", "mode", "expected", "metrics"]
    missing = [k for k in required if k not in q]
    if missing:
        raise EvalQueryError(f"Missing required fields: {missing}")

    mode = q["mode"]
    if mode not in VALID_MODES:
        raise EvalQueryError(
            f"Invalid mode '{mode}' for query '{q['id']}'. "
            f"Valid modes: {sorted(VALID_MODES)}"
        )

    expected = _validate_expected(q["expected"], str(q["id"]))
    metrics = _validate_metrics(q["metrics"], str(q["id"]))

    return EvalQuery(
        id=str(q["id"]),
        query=str(q["query"]),
        mode=mode,
        expected=expected,
        metrics=metrics,
    )


def load_queries(path: Path) -> EvalQuerySet:
    """Load and validate eval queries from a YAML file.

    Args:
        path: Path to the YAML file containing eval queries.

    Returns:
        An EvalQuerySet with validated queries.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        EvalQueryError: If the YAML structure or query fields are invalid.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Eval queries file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict) or "queries" not in raw:
        raise EvalQueryError("YAML must contain a top-level 'queries' key")

    query_list = raw["queries"]
    if not isinstance(query_list, list):
        raise EvalQueryError("'queries' must be a list")

    queries: list[EvalQuery] = []
    for i, item in enumerate(query_list):
        if not isinstance(item, dict):
            raise EvalQueryError(f"Query at index {i} must be a mapping, got {type(item).__name__}")
        queries.append(_validate_query(item))

    return EvalQuerySet(queries=queries)
