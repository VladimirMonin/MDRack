#!/usr/bin/env python3
"""Run a deterministic, provider-free quality experiment on the public fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from mdrack.eval.quality import (
    QualityCase,
    QualityJudgment,
    QualityUnit,
    evaluate_quality,
    fingerprint,
)

_TOKEN = re.compile(r"\w+", re.UNICODE)


def _load_units(root: Path) -> tuple[list[QualityUnit], dict[str, str]]:
    manifest = json.loads((root / "corpus-v1" / "manifest.json").read_text(encoding="utf-8"))
    units: list[QualityUnit] = []
    texts: dict[str, str] = {}
    for resource in manifest["resources"]:
        artifact = root / "corpus-v1" / resource["artifact_ref"]
        if resource["resource_kind"] == "document":
            raw = artifact.read_text(encoding="utf-8")
            parts = [
                part.strip()
                for part in re.split(r"\n\s*\n", raw)
                if part.strip() and not part.lstrip().startswith("#")
            ]
            for item, text in zip(resource["units"], parts):
                unit = QualityUnit(item["unit_id"], resource["resource_id"])
                units.append(unit)
                texts[unit.unit_id] = text
        else:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            if resource["resource_kind"] == "image":
                item = resource["units"][0]
                unit = QualityUnit(item["unit_id"], resource["resource_id"])
                units.append(unit)
                texts[unit.unit_id] = payload["ocr_text"] + " " + payload["caption_text"]
            else:
                for item, passage in zip(
                    (unit for unit in resource["units"] if unit["unit_kind"] == "time_segment"),
                    payload["passages"],
                ):
                    unit = QualityUnit(item["unit_id"], resource["resource_id"], item["start_ms"], item["end_ms"])
                    units.append(unit)
                    texts[unit.unit_id] = passage["text"]
                for item, frame in zip(
                    (unit for unit in resource["units"] if unit["unit_kind"] == "frame"),
                    payload.get("frames", []),
                ):
                    unit = QualityUnit(item["unit_id"], resource["resource_id"], timestamp_ms=item["timestamp_ms"])
                    units.append(unit)
                    texts[unit.unit_id] = frame["caption"]
    return units, texts


def _cases(root: Path) -> list[QualityCase]:
    payload = json.loads((root / "queries-v1" / "queries.json").read_text(encoding="utf-8"))
    cases = []
    for item in payload["cases"]:
        judgments = tuple(
            QualityJudgment(
                resource_id=value["resource_id"],
                grade=value["grade"],
                unit_id=value.get("unit_id"),
                start_ms=value.get("evidence", {}).get("start_ms"),
                end_ms=value.get("evidence", {}).get("end_ms"),
                timestamp_ms=value.get("evidence", {}).get("timestamp_ms"),
            )
            for value in item["judgments"]
        )
        cases.append(
            QualityCase(
                case_kind=item["case_kind"],
                cutoffs=tuple(item["cutoffs"]["recall"]),
                mrr_cutoff=item["cutoffs"]["mrr"],
                ndcg_cutoff=item["cutoffs"]["ndcg"],
                judgments=judgments,
                slice_tags=tuple(item["slice_tags"]),
                case_id=item["query_id"],
                query_text=item["query_text"],
            )
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("tests/evaluation"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    units, texts = _load_units(root)
    cases = _cases(root)

    def rank(case: QualityCase) -> list[QualityUnit]:
        terms = set(_TOKEN.findall(case.query_text.casefold()))
        scored = [(sum(texts[unit.unit_id].casefold().count(term) for term in terms), unit) for unit in units]
        return [unit for score, unit in sorted(scored, key=lambda value: (-value[0], value[1].unit_id)) if score > 0]

    corpus_manifest = json.loads((root / "corpus-v1" / "manifest.json").read_text(encoding="utf-8"))
    query_bytes = (root / "queries-v1" / "queries.json").read_bytes()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip() or "unavailable"
    runner_source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = evaluate_quality(
        cases,
        rank,
        corpus_ref=corpus_manifest["contract_digest"],
        implementation_ref=fingerprint(
            {
                "runner_source_sha256": runner_source_sha256,
                "executable": sys.executable,
                "revision": revision,
            }
        ),
    )
    report["experiment"] = {
        "evidence_boundary": "offline/provider-free",
        "ranker": "lexical-token-overlap-v1",
        "corpus_contract": corpus_manifest["contract_digest"],
        "query_set_ref": fingerprint(query_bytes.decode("utf-8")),
        "unit_count": len(units),
        "non_claims": [
            "does not measure LM Studio/OpenRouter/provider relevance",
            "does not select production defaults",
            "does not claim native visual or acoustic similarity",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output), "cases": len(cases), "units": len(units)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
