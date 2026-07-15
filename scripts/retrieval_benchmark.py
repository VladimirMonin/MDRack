"""Run a retrieval baseline and write a privacy-safe local JSON report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mdrack.config.models import MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.eval.privacy import scan_privacy
from mdrack.eval.queries import load_queries
from mdrack.eval.reporting import build_retrieval_report
from mdrack.eval.retrieval import run_retrieval_eval
from mdrack.markdown import chunk_builder, section_builder
from mdrack.markdown import parser as markdown_parser
from mdrack.storage.sqlite.connection import get_connection


def _digest_ref(parts: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return f"sha256:{digest.hexdigest()}"


def _value_bytes(value: Any) -> bytes:
    if value is None:
        return b"null"
    if isinstance(value, bytes):
        return b"bytes:" + value
    return f"{type(value).__name__}:{value}".encode("utf-8")


def _rows_ref(conn: sqlite3.Connection, queries: Iterable[str]) -> str:
    parts: list[bytes] = []
    for query in queries:
        parts.append(query.encode("utf-8"))
        for row in conn.execute(query):
            parts.extend(_value_bytes(value) for value in row)
    return _digest_ref(parts)


def _module_ref(*modules: Any, config: dict[str, Any] | None = None) -> str:
    parts = [Path(module.__file__).read_bytes() for module in modules]
    if config is not None:
        parts.append(json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return _digest_ref(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local-reports/retrieval-baseline.json"),
    )
    args = parser.parse_args()

    query_set = load_queries(args.queries)
    benchmark_digest = hashlib.sha256(args.queries.read_bytes()).hexdigest()[:16]
    config = MDRackConfig()
    provider = FakeEmbeddingProvider()
    conn = get_connection(args.db)
    try:
        evaluation = run_retrieval_eval(
            conn,
            query_set,
            provider,
            config=config,
            k=args.k,
        )
        corpus_ref = _rows_ref(
            conn,
            ["SELECT id, source_hash, status FROM files ORDER BY id"],
        )
        index_ref = _rows_ref(
            conn,
            [
                "SELECT version FROM schema_migrations ORDER BY version",
                "SELECT id, file_id, section_id, content, content_type, chunk_index, "
                "heading_path, previous_chunk_id, next_chunk_id, embedding_text, "
                "embedding_text_hash FROM chunks ORDER BY id",
                "SELECT chunk_id, profile_name, embedding FROM chunk_embeddings "
                "ORDER BY chunk_id, profile_name",
            ],
        )
        profile_ref = _digest_ref(
            [
                _rows_ref(
                    conn,
                    [
                        "SELECT name, model, dimensions, endpoint FROM embedding_profiles "
                        "ORDER BY name"
                    ],
                ).encode("utf-8"),
                provider._model_name.encode("utf-8"),
                str(provider.dimensions).encode("ascii"),
                b"default",
            ]
        )
    finally:
        conn.close()

    report = build_retrieval_report(
        evaluation,
        benchmark_ref=f"sha256:{benchmark_digest}",
        corpus_ref=corpus_ref,
        index_ref=index_ref,
        profile_ref=profile_ref,
        parser_ref=_module_ref(markdown_parser),
        chunker_ref=_module_ref(
            chunk_builder,
            section_builder,
            config=config.chunking.model_dump(),
        ),
    ).to_dict()
    privacy = scan_privacy(
        report,
        forbidden_values=[
            query.query for query in query_set.queries
        ] + [args.db.name, args.queries.name],
    )
    if not privacy.safe:
        print(json.dumps({"ok": False, "privacy": privacy.to_dict()}, ensure_ascii=True))
        return 2

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report": report}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
