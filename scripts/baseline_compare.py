"""Compare a fixed MDRack corpus across historical and current checkouts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from mdrack.eval.privacy import scan_privacy
from mdrack.eval.reporting import build_baseline_comparison_report

HISTORICAL_REVISION = "cbd60b84bf19025eac0ae5c2616626db5351481f"
_WORKER_SOURCE = r'''
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path

from mdrack.config.models import MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.indexing.indexer import run_indexer
from mdrack.markdown.parser import parse_markdown
from mdrack.search.text import text_search
from mdrack.storage.sqlite.connection import get_connection


def source_ref(relative_path, heading_path):
    value = relative_path + "\0" + heading_path
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def fact_matches(item, fact):
    if item.file_relative_path != fact["relative_path"]:
        return False
    expected_heading = fact.get("heading_path")
    return expected_heading is None or (item.heading_path or "") == expected_heading


def metric_values(source_facts, retrieved, k):
    matched_facts = set()
    first = None
    for rank, item in enumerate(retrieved[:k], 1):
        for fact_index, fact in enumerate(source_facts):
            if fact_matches(item, fact):
                matched_facts.add(fact_index)
                if first is None:
                    first = rank
    return {
        "recall_at_k": len(matched_facts) / len(source_facts) if source_facts else 0.0,
        "mrr": 1.0 / first if first else 0.0,
        "precision_at_k": len(matched_facts) / k,
    }


request = json.loads(sys.stdin.read())
root = Path(request["root"])
queries = request["queries"]
config = MDRackConfig()
provider = FakeEmbeddingProvider()

block_count = sum(len(parse_markdown(path).blocks) for path in sorted(root.rglob("*.md")))
index_started = time.perf_counter()
index_result = run_indexer(root=root, config=config, provider=provider)
index_ms = (time.perf_counter() - index_started) * 1000

conn = get_connection(root / ".mdrack" / "knowledge.db")
try:
    counts = {
        "files": conn.execute("SELECT COUNT(*) FROM files WHERE status = 'active'").fetchone()[0],
        "blocks": block_count,
        "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "errors": index_result.errors_count,
    }
    results = []
    search_ms = 0.0
    for query in queries:
        k = int(query["metrics"]["recall_at"])
        started = time.perf_counter()
        search_result = text_search(conn, query["query"], limit=k)
        search_ms += (time.perf_counter() - started) * 1000
        source_facts = query["expected"]["source_facts"]
        normalized = [
            {
                "rank": rank,
                "source_ref": source_ref(item.file_relative_path, item.heading_path or ""),
            }
            for rank, item in enumerate(search_result.results, 1)
        ]
        metrics = metric_values(source_facts, search_result.results, k)
        conditions_met = bool(source_facts) and metrics["recall_at_k"] > 0.0
        results.append(
            {
                "query_ref": query["id"],
                "mode": "text",
                "k": k,
                "retrieved": normalized,
                "expected_fact_count": len(source_facts),
                "metrics": metrics,
                "conditions_met": conditions_met,
                "error_category": None if source_facts else "zero_gold",
            }
        )
finally:
    conn.close()

packages = ("click", "pydantic", "markdown-it-py", "PyYAML")
dependencies = {}
for package in packages:
    try:
        dependencies[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        dependencies[package] = "not_installed"

passed = sum(1 for result in results if result["conditions_met"])
summary = {
    "queries_total": len(results),
    "queries_successful": passed,
    "queries_failed": len(results) - passed,
    "avg_recall_at_k": sum(result["metrics"]["recall_at_k"] for result in results) / len(results),
    "avg_mrr": sum(result["metrics"]["mrr"] for result in results) / len(results),
    "avg_precision_at_k": sum(result["metrics"]["precision_at_k"] for result in results) / len(results),
}
print(json.dumps({
    "environment": {
        "python": platform.python_version(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "dependencies": dependencies,
    },
    "counts": counts,
    "test_counts": {"total": len(results), "passed": passed, "failed": len(results) - passed},
    "timings_ms": {"index": round(index_ms, 3), "search": round(search_ms, 3)},
    "results": results,
    "summary": summary,
}, sort_keys=True))
'''


def _framed_digest(entries: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, content in entries:
        name_bytes = name.encode("utf-8")
        digest.update(len(name_bytes).to_bytes(8, "big"))
        digest.update(name_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def corpus_fingerprint(corpus: Path) -> str:
    """Hash sorted root-relative Markdown paths and exact file bytes."""
    if not corpus.is_dir():
        raise ValueError("corpus must be a directory")
    entries = [(path.relative_to(corpus).as_posix(), path.read_bytes()) for path in sorted(corpus.rglob("*.md"))]
    if not entries:
        raise ValueError("corpus must contain Markdown files")
    return _framed_digest(entries)


def load_baseline_queries(path: Path) -> list[dict[str, Any]]:
    """Load the fixed text-only query contract used by both revisions."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    queries = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(queries, list) or not queries:
        raise ValueError("query set must contain a non-empty queries list")
    required = {"id", "query", "mode", "expected", "metrics"}
    for query in queries:
        if not isinstance(query, dict) or not required.issubset(query):
            raise ValueError("every query must contain the baseline query fields")
        if query["mode"] != "text":
            raise ValueError("cross-revision baseline queries must use text mode")
        if not isinstance(query["expected"], dict):
            raise ValueError("every query must define stable source expectations")
        source_facts = query["expected"].get("source_facts")
        if not isinstance(source_facts, list) or not source_facts:
            raise ValueError("every query must define stable source facts")
        for fact in source_facts:
            if not isinstance(fact, dict) or not isinstance(fact.get("relative_path"), str):
                raise ValueError("every source fact must define a relative_path")
            if set(fact) - {"relative_path", "heading_path"}:
                raise ValueError("source facts contain unsupported fields")
            if "heading_path" in fact and not isinstance(fact["heading_path"], str):
                raise ValueError("source fact heading_path must be a string")
        recall_at = query["metrics"].get("recall_at") if isinstance(query["metrics"], dict) else None
        if not isinstance(recall_at, int) or isinstance(recall_at, bool) or recall_at < 1:
            raise ValueError("every query must define a positive recall_at")
    return queries


def _git_sha(checkout: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _implementation_identity(
    checkout: Path,
    head_sha: str,
    corpus: Path,
    queries: Path,
) -> dict[str, Any]:
    """Identify HEAD plus the exact public Phase 1 code/input bytes."""
    entries = [
        ("scripts/baseline_compare.py", (checkout / "scripts/baseline_compare.py").read_bytes()),
        ("src/mdrack/eval/reporting.py", (checkout / "src/mdrack/eval/reporting.py").read_bytes()),
        ("tests/retrieval_eval/baseline_queries.yaml", queries.read_bytes()),
    ]
    entries.extend(
        (
            f"tests/fixtures/baseline_corpus/{path.relative_to(corpus).as_posix()}",
            path.read_bytes(),
        )
        for path in sorted(corpus.rglob("*.md"))
    )
    entries.sort(key=lambda entry: entry[0])
    return {
        "head_sha": head_sha,
        "manifest": {
            "algorithm": "sha256-framed-v1",
            "recipe": (
                "sort canonical root-relative POSIX paths; for each entry hash "
                "uint64be(path_utf8_bytes_length) || path_utf8_bytes || "
                "uint64be(content_bytes_length) || exact_content_bytes"
            ),
            "paths": [
                "scripts/baseline_compare.py",
                "src/mdrack/eval/reporting.py",
                "tests/retrieval_eval/baseline_queries.yaml",
                "tests/fixtures/baseline_corpus/**/*.md",
            ],
            "entry_count": len(entries),
            "digest": _framed_digest(entries),
        },
    }


def _unavailable(commit_sha: str, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "commit_sha": commit_sha,
        "historical_baseline_unavailable": reason,
    }


def _run_checkout(
    checkout: Path,
    commit_sha: str,
    corpus: Path,
    queries: list[dict[str, Any]],
    *,
    historical: bool,
) -> dict[str, Any]:
    if not checkout.is_dir():
        return _unavailable(commit_sha, "checkout_missing")
    try:
        actual_sha = _git_sha(checkout)
    except (OSError, subprocess.SubprocessError):
        return _unavailable(commit_sha, "checkout_revision_unreadable")
    if historical and commit_sha != HISTORICAL_REVISION:
        return _unavailable(HISTORICAL_REVISION, "historical_revision_not_pinned")
    if actual_sha != commit_sha:
        reason = "historical_revision_mismatch" if historical else "checkout_revision_mismatch"
        return _unavailable(commit_sha, reason)

    with tempfile.TemporaryDirectory(prefix="mdrack-baseline-") as temp_dir:
        sandbox = Path(temp_dir) / "corpus"
        shutil.copytree(corpus, sandbox)
        request = json.dumps({"root": str(sandbox), "queries": queries}, ensure_ascii=False)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(checkout / "src")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = subprocess.run(
                [sys.executable, "-c", _WORKER_SOURCE],
                input=request,
                cwd=checkout,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return _unavailable(commit_sha, "checkout_runtime_timeout")
        except OSError:
            return _unavailable(commit_sha, "checkout_runtime_unavailable")

    if completed.returncode != 0:
        reason = "checkout_runtime_incompatible" if historical else "checkout_execution_failed"
        return _unavailable(commit_sha, reason)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _unavailable(commit_sha, "checkout_output_invalid")
    result.update(
        {
            "status": "available",
            "commit_sha": commit_sha,
            "commands": [
                {
                    "argv": ["python", "-c", "<embedded-baseline-worker>"],
                    "cwd": "<checkout>",
                    "environment": {
                        "PYTHONPATH": "<checkout>/src",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    "stdin_contract": "fixed-corpus-and-query-set",
                    "worker_sha256": hashlib.sha256(_WORKER_SOURCE.encode("utf-8")).hexdigest(),
                }
            ],
        }
    )
    return result


def _query_set_fingerprint(path: Path) -> str:
    return _framed_digest([(path.name, path.read_bytes())])


def _write_summary(path: Path, report: dict[str, Any], report_digest: str) -> None:
    historical = report["historical"]
    current = report["current"]
    identity = report["implementation_identity"]
    manifest = identity["manifest"]
    lines = [
        "# MDRack v0.2 historical/current baseline evidence",
        "",
        f"- Historical revision: `{report['revisions']['historical']}`",
        f"- Current revision: `{report['revisions']['current']}`",
        f"- Current implementation manifest: `{manifest['digest']}` ({manifest['entry_count']} entries)",
        f"- Corpus fingerprint: `{report['corpus_fingerprint']}`",
        f"- Query-set fingerprint: `{report['query_set_fingerprint']}`",
        f"- Local report digest: `{report_digest}`",
        f"- Historical status: `{historical['status']}`",
        f"- Current status: `{current['status']}`",
    ]
    if historical["status"] == "unavailable":
        lines.append(
            "- Historical baseline unavailable: "
            f"`{historical['historical_baseline_unavailable']}`"
        )
    if historical["status"] == "available":
        lines.extend(
            [
                f"- Historical files/blocks/chunks/errors: "
                f"`{historical['counts']['files']}/{historical['counts']['blocks']}/"
                f"{historical['counts']['chunks']}/{historical['counts']['errors']}`",
                f"- Historical retrieval tests passed: "
                f"`{historical['test_counts']['passed']}/{historical['test_counts']['total']}`",
            ]
        )
    if current["status"] == "available":
        lines.extend(
            [
                f"- Current files/blocks/chunks/errors: "
                f"`{current['counts']['files']}/{current['counts']['blocks']}/"
                f"{current['counts']['chunks']}/{current['counts']['errors']}`",
                f"- Current retrieval tests passed: "
                f"`{current['test_counts']['passed']}/{current['test_counts']['total']}`",
            ]
        )
    lines.extend(
        [
            f"- Comparable: `{str(report['comparison']['comparable']).lower()}`",
            "",
            "The local JSON report is intentionally excluded from Git. This summary contains only public revision IDs, "
            "hashes, aggregate counts, status categories, and metrics. It omits absolute paths, raw queries, "
            "corpus text, "
            "database identifiers, credentials, and provider bodies.",
            "",
            "The implementation manifest uses `sha256-framed-v1`: sort the canonical root-relative POSIX "
            "paths listed in the local report, then for each file hash `uint64be(path byte length)`, exact "
            "UTF-8 path bytes, `uint64be(content byte length)`, and exact content bytes in that order.",
            "",
            "Reproduce from the current checkout:",
            "",
            "```bash",
            "git worktree add --detach ../mdrack-cbd60b8 cbd60b8",
            "uv run python scripts/baseline_compare.py \\",
            "  --baseline-checkout ../mdrack-cbd60b8 \\",
            "  --current-checkout . \\",
            "  --corpus tests/fixtures/baseline_corpus \\",
            "  --queries tests/retrieval_eval/baseline_queries.yaml \\",
            "  --output .local-reports/v0.2-baseline.json \\",
            "  --summary-output docs/evidence/v0.2-baseline.md",
            "```",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-checkout", type=Path, required=True)
    parser.add_argument("--current-checkout", type=Path, default=Path("."))
    parser.add_argument("--corpus", type=Path, default=Path("tests/fixtures/baseline_corpus"))
    parser.add_argument("--queries", type=Path, default=Path("tests/retrieval_eval/baseline_queries.yaml"))
    parser.add_argument("--output", type=Path, default=Path(".local-reports/v0.2-baseline.json"))
    parser.add_argument("--summary-output", type=Path, default=None)
    args = parser.parse_args(argv)

    queries = load_baseline_queries(args.queries)
    baseline_sha = HISTORICAL_REVISION
    current_sha = _git_sha(args.current_checkout)
    implementation_identity = _implementation_identity(
        args.current_checkout,
        current_sha,
        args.corpus,
        args.queries,
    )
    historical = _run_checkout(
        args.baseline_checkout,
        baseline_sha,
        args.corpus,
        queries,
        historical=True,
    )
    current = _run_checkout(
        args.current_checkout,
        current_sha,
        args.corpus,
        queries,
        historical=False,
    )
    report = build_baseline_comparison_report(
        baseline_sha=baseline_sha,
        current_sha=current_sha,
        corpus_ref=corpus_fingerprint(args.corpus),
        query_set_ref=_query_set_fingerprint(args.queries),
        historical=historical,
        current=current,
        implementation_identity=implementation_identity,
    )
    forbidden = [query["query"] for query in queries]
    forbidden.extend(path.name for path in args.corpus.rglob("*.md"))
    privacy = scan_privacy(report, forbidden_values=forbidden)
    if not privacy.safe:
        print(json.dumps({"ok": False, "privacy": privacy.to_dict()}, sort_keys=True))
        return 2

    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    report_digest = f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
    if args.summary_output is not None:
        _write_summary(args.summary_output, report, report_digest)
    ok = current["status"] == "available" and report["comparison"]["comparable"]
    print(json.dumps({"ok": ok, "report_ref": report_digest}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
