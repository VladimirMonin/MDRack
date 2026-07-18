"""Executable v0.2 baselines and the mandatory mdrack_core boundary guard."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from mdrack.application.retrieval import HybridRetrievalService
from mdrack.config.models import MDRackConfig
from mdrack.domain.indexing import SourceLocator
from mdrack.domain.retrieval import RetrievalCandidate
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.eval.privacy import scan_privacy
from mdrack.indexing.indexer import run_indexer
from mdrack.markdown.parser import parse_markdown
from mdrack.output.envelope import error, success
from mdrack.search.text import text_search
from mdrack.storage.sqlite.connection import get_connection
from scripts import check_no_forbidden_deps
from scripts.check_core_boundaries import Violation, check_python_file, check_repository

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "core_multimodal"
EXPECTED = json.loads((FIXTURE_ROOT / "expected.json").read_text(encoding="utf-8"))


def _write_core(root: Path, relative_path: str, source: str) -> Path:
    path = root / "src" / "mdrack_core" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _candidate(logical_id: str, score: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        logical_id=logical_id,
        score=score,
        content_preview=f"public preview {logical_id}",
        source_locator=SourceLocator(
            root_id="baseline",
            relative_path=f"markdown/{logical_id}.md",
            start_line=2,
            end_line=4,
            heading_path=("Baseline", logical_id),
            block_id=f"block_{logical_id}",
            chunk_id=logical_id,
            start_offset=10,
            end_offset=30,
            block_kind="paragraph",
            chunk_kind="structural",
        ),
        metadata={"section_title": logical_id.title()},
    )


def test_repository_guard_passes_before_core_exists() -> None:
    assert check_repository(Path(__file__).resolve().parents[2]) == []


def test_guard_accepts_stdlib_only_skeleton_and_ignores_explanatory_text(tmp_path: Path) -> None:
    path = _write_core(
        tmp_path,
        "domain/value.py",
        '"""The core has no provider, model, endpoint, storage, or Markdown behavior."""\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class Resource:\n"
        "    identifier: str\n"
        "    note: str = 'provider endpoint model storage markdown'\n",
    )

    assert check_python_file(path) == []
    assert check_repository(tmp_path) == []


def test_guard_allows_internal_relative_imports(tmp_path: Path) -> None:
    _write_core(tmp_path, "domain/value.py", "VALUE = 1\n")
    path = _write_core(tmp_path, "domain/__init__.py", "from .value import VALUE\n")

    assert check_python_file(path) == []
    assert check_repository(tmp_path) == []


@pytest.mark.parametrize(
    ("source", "category"),
    [
        ("import mdrack\n", "reverse-import"),
        ("import sqlite3\n", "infrastructure-import"),
        ("import pydantic\n", "third-party-import"),
        ("def call_provider():\n    return None\n", "forbidden-identifier"),
        ("def load(model_name: str):\n    return model_name\n", "forbidden-identifier"),
        ("def read():\n    return open('value')\n", "infrastructure-call"),
        ("import importlib\nimportlib.import_module('mdrack')\n", "reverse-import"),
        ("from .records import provider\n", "forbidden-identifier"),
        ("fn(endpoint='x')\n", "forbidden-identifier"),
        ("import importlib\nimportlib.import_module(name='mdrack')\n", "reverse-import"),
        (
            "from importlib import import_module as load\nload('mdrack')\n",
            "reverse-import",
        ),
    ],
)
def test_guard_rejects_reverse_third_party_and_infrastructure_leaks(
    tmp_path: Path,
    source: str,
    category: str,
) -> None:
    path = _write_core(tmp_path, "leak.py", source)

    violations = check_python_file(path, display_path=Path("src/mdrack_core/leak.py"))

    assert category in {violation.category for violation in violations}
    assert all(str(tmp_path) not in violation.render() for violation in violations)


@pytest.mark.parametrize(
    "source",
    [
        "from .records import resource\n",
        "fn(label='provider endpoint storage')\n",
        "import importlib\nimportlib.import_module(name='json')\n",
        (
            "from importlib import import_module as load\n"
            "load(name='.records', package='mdrack_core')\n"
        ),
        "from importlib import import_module as load\nclient.load('mdrack')\n",
    ],
)
def test_guard_preserves_identifier_and_dynamic_import_false_positive_controls(
    tmp_path: Path,
    source: str,
) -> None:
    path = _write_core(tmp_path, "allowed.py", source)

    assert check_python_file(path) == []


def test_application_code_may_import_core_because_only_core_is_scanned(tmp_path: Path) -> None:
    _write_core(tmp_path, "__init__.py", "VALUE = 1\n")
    app = tmp_path / "src" / "mdrack" / "application" / "example.py"
    app.parent.mkdir(parents=True)
    app.write_text("from mdrack_core import VALUE\n", encoding="utf-8")

    assert check_repository(tmp_path) == []


def test_dependency_and_platform_verify_gates_include_core_boundary_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = Violation("src/mdrack_core/leak.py", 1, 1, "reverse-import", "test finding")
    monkeypatch.setattr(check_no_forbidden_deps, "check_core_repository", lambda: [finding])

    assert check_no_forbidden_deps.check_core_boundaries() == [
        f"core boundary: {finding.render()}"
    ]
    repository = Path(__file__).resolve().parents[2]
    for verify_script in ("verify.sh", "verify.ps1"):
        content = (repository / "scripts" / verify_script).read_text(encoding="utf-8")
        assert content.count("uv run python scripts/check_core_boundaries.py") == 1


def test_v03_markdown_projection_preserves_retrieval_metrics_and_source_bytes(
    tmp_path: Path,
) -> None:
    source = FIXTURE_ROOT / "markdown"
    assert EXPECTED["provenance"]["markdown_sha256"] == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source.glob("*.md"))
    }
    corpus = tmp_path / "corpus"
    shutil.copytree(source, corpus)
    source_hashes = {
        path.relative_to(corpus).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(corpus.rglob("*.md"))
    }

    block_count = sum(len(parse_markdown(path).blocks) for path in sorted(corpus.rglob("*.md")))
    result = run_indexer(
        root=corpus,
        config=MDRackConfig(),
        provider=FakeEmbeddingProvider(),
    )
    connection = get_connection(corpus / ".mdrack" / "knowledge.db")
    try:
        actual_counts = {
            "files": connection.execute(
                "SELECT COUNT(*) FROM files WHERE status = 'active'"
            ).fetchone()[0],
            "blocks": block_count,
            "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "errors": result.errors_count,
        }
        expected_counts = {**EXPECTED["retrieval_metrics"]["counts"], "chunks": 10}
        assert actual_counts == expected_counts

        for query in EXPECTED["retrieval_metrics"]["queries"]:
            retrieved = text_search(connection, query["query"], limit=query["k"]).results
            matching_ranks = [
                rank
                for rank, item in enumerate(retrieved, 1)
                if item.file_relative_path == query["expected_path"]
                and item.heading_path == query["expected_heading_path"]
            ]
            recall_at_k = 1.0 if matching_ranks else 0.0
            mrr = 1.0 / matching_ranks[0] if matching_ranks else 0.0
            precision_at_k = len(matching_ranks) / query["k"]
            assert len(retrieved) == query["retrieved_count"]
            assert recall_at_k == query["recall_at_k"]
            assert mrr == query["mrr"]
            assert precision_at_k == query["precision_at_k"]
    finally:
        connection.close()

    assert source_hashes == {
        path.relative_to(corpus).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(corpus.rglob("*.md"))
    }


@pytest.mark.asyncio
async def test_v02_compatibility_envelopes_and_hybrid_dto_are_frozen() -> None:
    assert success({"count": 1}, "scan") == EXPECTED["compatibility"]["success_envelope"]
    assert error("failed", "E_TEST", "scan", {"count": 1}) == EXPECTED["compatibility"]["error_envelope"]

    result = await HybridRetrievalService(rrf_k=60).retrieve(
        "public baseline query",
        [_candidate("shared", 0.9), _candidate("text", 0.8), _candidate("shared", 0.7)],
        [_candidate("semantic", 0.95), _candidate("shared", 0.85)],
        limit=10,
    )

    assert result.to_dict() == EXPECTED["compatibility"]["hybrid_result"]


def test_v02_privacy_baseline_accepts_aggregates_and_classifies_synthetic_leaks() -> None:
    privacy = EXPECTED["privacy"]
    assert scan_privacy(
        privacy["safe_report"],
        forbidden_values=privacy["forbidden_values"],
    ).safe

    leaked = {
        "query": privacy["forbidden_values"][0],
        "path": "/home/public-sentinel/private.md",
        "endpoint": "https://private-sentinel.invalid:1234/path",
        "vector": privacy["forbidden_values"][2],
    }
    result = scan_privacy(leaked, forbidden_values=privacy["forbidden_values"])

    assert sorted({finding.category for finding in result.findings}) == privacy["leak_categories"]
    assert all(
        sentinel not in finding.location
        for finding in result.findings
        for sentinel in privacy["forbidden_values"]
    )
