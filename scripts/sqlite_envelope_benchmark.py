"""Reproducible, provider-free SQLite operating-envelope benchmark.

The harness creates only disposable synthetic catalogs. It reports privacy-safe
aggregates and deliberately does not choose or probe an alternative backend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_candidate_migrations, get_migrations_dir

DEFAULT_CELLS = ((1_000, 384), (10_000, 384), (50_000, 384), (100_000, 384),
                 (1_000, 768), (10_000, 768), (50_000, 768), (100_000, 768),
                 (1_000, 1024), (10_000, 1024), (50_000, 1024), (100_000, 1024))

_CHILD = r'''
import json, resource, sys, time
from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.storage.sqlite.connection import get_connection
from mdrack_core.domain import SearchScope, VectorBranch

db, dim, limit = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
conn = get_connection(__import__("pathlib").Path(db))
store = SQLiteResourceStore(conn)
branch = VectorBranch("envelope", "space", (1.0,) + (0.0,) * (dim - 1), 1.0, limit)
started_cpu = time.process_time()
started = time.perf_counter()
result = store.search_vector(branch, scope=SearchScope())
elapsed = (time.perf_counter() - started) * 1000
cpu = (time.process_time() - started_cpu) * 1000
fusion_started = time.perf_counter()
# The adapter returns candidates; this models the provider-neutral deterministic
# top-k fusion step without introducing a backend-specific implementation.
merged = sorted(((item.unit_id, item.raw_score) for item in result), key=lambda item: (-item[1], item[0]))
fusion = (time.perf_counter() - fusion_started) * 1000
print(json.dumps({"wall_ms": elapsed, "cpu_ms": cpu, "rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  "candidates": len(result), "fusion_ms": fusion, "merged": len(merged)}))
store.connection.close()
'''


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def make_catalog(path: Path, count: int, dimensions: int) -> None:
    conn = get_connection(path)
    apply_candidate_migrations(conn, get_migrations_dir())
    now = "2026-07-20T00:00:00+00:00"
    conn.execute(
        "INSERT INTO core_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("resource", "synthetic", "application/x.synthetic", "offline-envelope",
         "synthetic", "{}", "sha256:" + "0" * 64, None, None, "{}", now),
    )
    conn.execute(
        "INSERT INTO core_representations VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("representation", "resource", "whole_resource", "text", None, None, None, None, None, "{}"),
    )
    conn.execute(
        "INSERT INTO core_embedding_spaces VALUES(?,?,?,?,?)",
        ("space", dimensions, "cosine", "offline-envelope-v1", "{}"),
    )
    blob = json.dumps((1.0,) + (0.0,) * (dimensions - 1), separators=(",", ":")).encode()
    for start in range(0, count, 1_000):
        stop = min(count, start + 1_000)
        conn.executemany(
            "INSERT INTO core_search_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ((f"unit-{i:08d}", "resource", "representation", "whole_resource", "text",
              None, "synthetic", "{}", i, None, None, "{}") for i in range(start, stop)),
        )
        conn.executemany(
            "INSERT INTO core_unit_embeddings VALUES(?,?,?,?)",
            ((f"unit-{i:08d}", "space", blob, now) for i in range(start, stop)),
        )
    conn.commit()
    conn.close()


def run_cell(count: int, dimensions: int, warmups: int, repetitions: int, candidate_limit: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="mdrack-envelope-") as temp:
        database = Path(temp) / "catalog.db"
        make_catalog(database, count, dimensions)
        command = [sys.executable, "-c", _CHILD, str(database), str(dimensions), str(candidate_limit)]
        for _ in range(warmups):
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode:
                raise RuntimeError(completed.stderr)
        samples: list[dict[str, float]] = []
        for _ in range(repetitions):
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode:
                raise RuntimeError(completed.stderr)
            samples.append(json.loads(completed.stdout))
        db_bytes = sum(path.stat().st_size for path in database.parent.glob("catalog.db*"))
        return {
            "units": count,
            "dimensions": dimensions,
            "warmups": warmups,
            "repetitions": repetitions,
            "candidate_limit": candidate_limit,
            "db_bytes": db_bytes,
            "metrics": {
                metric: {"p50": percentile([sample[metric] for sample in samples], 0.50),
                         "p95": percentile([sample[metric] for sample in samples], 0.95),
                         "p99": percentile([sample[metric] for sample in samples], 0.99)}
                for metric in ("wall_ms", "cpu_ms", "rss_kib", "candidates", "fusion_ms")
            },
        }


def parse_cells(raw: str) -> tuple[tuple[int, int], ...]:
    cells = []
    for item in raw.split(","):
        units, dimensions = item.split("x", 1)
        cells.append((int(units), int(dimensions)))
    return tuple(cells)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", default=",".join(f"{n}x{d}" for n, d in DEFAULT_CELLS))
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.warmups < 0 or args.repetitions < 1 or args.candidate_limit < 1:
        parser.error("warmups >= 0, repetitions >= 1 and candidate-limit >= 1 are required")
    started = time.perf_counter()
    results = [run_cell(n, d, args.warmups, args.repetitions, args.candidate_limit)
               for n, d in parse_cells(args.cells)]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {
        "contract": "mdrack.sqlite-envelope-v1",
        "evidence_boundary": "local components",
        "privacy": {"source": "synthetic", "network_attempts": 0, "temporary_catalogs_removed": True},
        "host": {"python": sys.version.split()[0], "platform": platform.platform(),
                 "kernel": os.uname().release, "machine": platform.machine(),
                 "cpu": platform.processor()},
        "revision": revision,
        "harness_sha256": script_digest,
        "packages": {"mdrack": "workspace", "mdrack-core": "workspace", "mdrack-sqlite": "workspace"},
        "config": {"cells": args.cells, "warmups": args.warmups, "repetitions": args.repetitions,
                   "candidate_limit": args.candidate_limit},
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cells": results,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
