"""Tests for retrieval eval query loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdrack.eval.queries import EvalQuery, EvalQueryError, EvalQuerySet, load_queries

QUERIES_YAML = Path(__file__).resolve().parent.parent / "retrieval_eval" / "queries.yaml"


class TestLoadQueries:
    """Verify loading eval queries from YAML file."""

    def test_loads_valid_yaml_file(self) -> None:
        qset = load_queries(QUERIES_YAML)
        assert isinstance(qset, EvalQuerySet)
        assert len(qset.queries) == 2

    def test_first_query_fields(self) -> None:
        qset = load_queries(QUERIES_YAML)
        q = qset.queries[0]
        assert q.id == "Q001"
        assert q.query == "how can an agent read neighboring chunks"
        assert q.mode == "hybrid"
        assert q.expected == {"file_path_contains": "retrieval", "heading_contains": "Neighbor"}
        assert q.metrics == {"recall_at": 5}

    def test_second_query_fields(self) -> None:
        qset = load_queries(QUERIES_YAML)
        q = qset.queries[1]
        assert q.id == "Q002"
        assert q.query == "OperationalError no such table"
        assert q.mode == "text"
        assert q.expected == {"content_contains": "OperationalError"}
        assert q.metrics == {"recall_at": 5}

    def test_all_queries_are_eval_query_instances(self) -> None:
        qset = load_queries(QUERIES_YAML)
        for query in qset.queries:
            assert isinstance(query, EvalQuery)


class TestValidation:
    """Verify validation of eval query fields."""

    def test_missing_required_field_raises_error(self) -> None:
        raw = """
queries:
  - id: QX
    query: "test query"
    mode: "text"
    metrics:
      recall_at: 3
"""
        with pytest.raises(EvalQueryError, match="Missing required fields"):
            load_queries(_write_temp_yaml(raw))

    def test_invalid_mode_raises_error(self) -> None:
        raw = """
queries:
  - id: QX
    query: "test query"
    mode: "bad_mode"
    expected:
      content_contains: "foo"
    metrics:
      recall_at: 3
"""
        with pytest.raises(EvalQueryError, match="Invalid mode"):
            load_queries(_write_temp_yaml(raw))

    def test_missing_queries_key_raises_error(self) -> None:
        raw = """
not_queries: []
"""
        with pytest.raises(EvalQueryError, match="'queries' key"):
            load_queries(_write_temp_yaml(raw))

    def test_queries_not_a_list_raises_error(self) -> None:
        raw = """
queries: "not a list"
"""
        with pytest.raises(EvalQueryError, match="must be a list"):
            load_queries(_write_temp_yaml(raw))

    def test_query_item_not_a_mapping_raises_error(self) -> None:
        raw = """
queries:
  - "not a mapping"
"""
        with pytest.raises(EvalQueryError, match="must be a mapping"):
            load_queries(_write_temp_yaml(raw))

    def test_empty_expected_mapping_raises_error(self) -> None:
        raw = """
queries:
  - id: QX
    query: "test query"
    mode: "text"
    expected: {}
    metrics:
      recall_at: 3
"""
        with pytest.raises(EvalQueryError, match="must not be empty"):
            load_queries(_write_temp_yaml(raw))

    def test_unsupported_expected_clause_raises_error(self) -> None:
        raw = """
queries:
  - id: QX
    query: "test query"
    mode: "text"
    expected:
      unsupported_clause: "foo"
    metrics:
      recall_at: 3
"""
        with pytest.raises(EvalQueryError, match="Unsupported expected clauses"):
            load_queries(_write_temp_yaml(raw))

    def test_blank_expected_clause_value_raises_error(self) -> None:
        raw = """
queries:
  - id: QX
    query: "test query"
    mode: "text"
    expected:
      content_contains: "   "
    metrics:
      recall_at: 3
"""
        with pytest.raises(EvalQueryError, match="must be a non-empty string"):
            load_queries(_write_temp_yaml(raw))

    def test_unsupported_metric_raises_error(self) -> None:
        raw = """
queries:
  - id: QX
    query: "test query"
    mode: "text"
    expected:
      content_contains: "foo"
    metrics:
      mrr: true
"""
        with pytest.raises(EvalQueryError, match="Unsupported metrics"):
            load_queries(_write_temp_yaml(raw))


class TestMissingFile:
    """Verify error when YAML file does not exist."""

    def test_missing_file_raises_filenotfound(self) -> None:
        nonexistent = Path("nonexistent_queries.yaml")
        with pytest.raises(FileNotFoundError, match="not found"):
            load_queries(nonexistent)


def _write_temp_yaml(content: str) -> Path:
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        encoding="utf-8",
        delete=False,
    ) as f:
        f.write(content)
        return Path(f.name)
