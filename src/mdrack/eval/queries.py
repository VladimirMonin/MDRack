"""Retrieval eval query format — YAML-based eval query definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_MODES = frozenset({"text", "semantic", "hybrid"})


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

    return EvalQuery(
        id=str(q["id"]),
        query=str(q["query"]),
        mode=mode,
        expected=dict(q["expected"]),
        metrics=dict(q["metrics"]),
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
