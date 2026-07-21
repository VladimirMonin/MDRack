"""Build and verify the deterministic MDRack 1.1 pre-result fixture freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, Callable, cast

ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = ROOT / "tests/evaluation"
CORPUS = EVALUATION_ROOT / "corpus-v1/manifest.json"
QUERIES = EVALUATION_ROOT / "queries-v1/queries.json"
BENCHMARK = EVALUATION_ROOT / "benchmark-v1/manifest.json"
CONFIG = ROOT / "configs/eval-v11.toml"
SENTINELS = ROOT / "tests/privacy/v1_1/sentinels.json"
CAPABILITY_MAP = EVALUATION_ROOT / "v1_1/capability-dod-map.json"
OUTPUT = EVALUATION_ROOT / "v1_1/freeze-manifest.json"

CONTRACT = "mdrack.v11-evaluation-input-freeze"
SCHEMA_VERSION = 1
EXPECTED_CAPABILITIES = tuple(f"C{index:02d}" for index in range(1, 20))
EXPECTED_DOD = tuple(f"29.{index}" for index in range(1, 9))
EXPECTED_SURFACES = {
    "api",
    "cache",
    "cli_stderr",
    "cli_stdout",
    "eval",
    "log",
    "provider",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("fixture document root must be an object")
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _contract_digest(document: dict[str, Any]) -> str:
    payload = {key: value for key, value in document.items() if key != "contract_digest"}
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _published_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _iter_input_paths() -> list[Path]:
    paths: set[Path] = {
        CONFIG,
        SENTINELS,
        CAPABILITY_MAP,
        ROOT / "scripts/build_v11_test_assets.py",
        EVALUATION_ROOT / "generate_fixture.py",
        EVALUATION_ROOT / "contract_validator.py",
    }
    for directory in (
        EVALUATION_ROOT / "corpus-v1",
        EVALUATION_ROOT / "queries-v1",
        EVALUATION_ROOT / "benchmark-v1",
        EVALUATION_ROOT / "schemas",
        ROOT / "tests/assets/v1_1",
    ):
        paths.update(path for path in directory.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())


def _validate_public_contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    namespace = runpy.run_path(str(EVALUATION_ROOT / "contract_validator.py"))
    validate_contracts = cast(
        Callable[[Path, Path, Path], tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
        namespace["validate_contracts"],
    )
    return validate_contracts(CORPUS, QUERIES, BENCHMARK)


def _validate_config() -> dict[str, Any]:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("phase") != "input_freeze" or config.get("candidate_results_allowed") is not False:
        raise ValueError("evaluation config must remain a pre-result input freeze")
    if config.get("network_allowed") is not False or config.get("private_corpus_allowed") is not False:
        raise ValueError("evaluation config must remain public and offline")
    if config.get("seed") != 20260721 or config.get("provider") != "deterministic_static":
        raise ValueError("evaluation config identity drifted")
    if config.get("execution", {}).get("repeats") != 2:
        raise ValueError("evaluation config must require two deterministic runs")
    for key in ("corpus_manifest", "queries", "benchmark_manifest", "privacy_sentinels"):
        relative = config.get(key)
        if not isinstance(relative, str) or not (ROOT / relative).is_file():
            raise ValueError("evaluation config contains an invalid frozen input path")
    return config


def _validate_mapping() -> dict[str, Any]:
    mapping = _load_json(CAPABILITY_MAP)
    capabilities = mapping.get("capabilities")
    dod_sections = mapping.get("dod_sections")
    if not isinstance(capabilities, list) or tuple(item.get("id") for item in capabilities) != EXPECTED_CAPABILITIES:
        raise ValueError("capability map must contain the exact 19 reconciled capabilities")
    if not isinstance(dod_sections, list) or tuple(item.get("id") for item in dod_sections) != EXPECTED_DOD:
        raise ValueError("capability map must cover DoD sections 29.1 through 29.8")
    known = set(EXPECTED_CAPABILITIES)
    referenced: set[str] = set()
    output_relative = OUTPUT.relative_to(ROOT).as_posix()
    for capability in capabilities:
        fixtures = capability.get("fixtures")
        if not isinstance(fixtures, list) or not fixtures:
            raise ValueError("every capability must name frozen fixture inputs")
        for relative in fixtures:
            if not isinstance(relative, str):
                raise ValueError("capability fixture references must be strings")
            if relative != output_relative and not (ROOT / relative).exists():
                raise ValueError("capability map references a missing fixture input")
    for section in dod_sections:
        support = section.get("fixture_support")
        gates = section.get("downstream_gates")
        if not isinstance(support, list) or not support or not set(support) <= known:
            raise ValueError("DoD mapping contains invalid fixture support")
        if not isinstance(gates, list) or not gates:
            raise ValueError("DoD mapping must name downstream evidence gates")
        referenced.update(cast(list[str], support))
    if referenced != known:
        raise ValueError("every reconciled capability must participate in the DoD mapping")
    if mapping.get("evidence_boundary") != "input fixtures only; mapping is not completion evidence":
        raise ValueError("capability map must not claim completion evidence")
    return mapping


def _validate_sentinels() -> dict[str, Any]:
    sentinels = _load_json(SENTINELS)
    values = sentinels.get("forbidden_values")
    keys = sentinels.get("forbidden_keys")
    surfaces = sentinels.get("surfaces")
    if sentinels.get("classification") != "synthetic" or sentinels.get("license_spdx") != "CC0-1.0":
        raise ValueError("privacy sentinels must be synthetic CC0 inputs")
    if not isinstance(values, dict) or len(values) < 12 or len(set(values.values())) != len(values):
        raise ValueError("privacy sentinel value families are incomplete or duplicated")
    if not isinstance(keys, list) or len(keys) < 3 or len(set(keys)) != len(keys):
        raise ValueError("privacy sentinel key families are incomplete or duplicated")
    if not isinstance(surfaces, list) or set(surfaces) != EXPECTED_SURFACES:
        raise ValueError("privacy sentinel surfaces drifted")
    return sentinels


def build_manifest() -> dict[str, Any]:
    corpus, queries, benchmark = _validate_public_contracts()
    config = _validate_config()
    _validate_mapping()
    sentinels = _validate_sentinels()

    resources = cast(list[dict[str, Any]], corpus["resources"])
    cases = cast(list[dict[str, Any]], queries["cases"])
    if not all(isinstance(case.get("judgments"), list) and case["judgments"] for case in cases):
        raise ValueError("every frozen query must have judgments before candidate evaluation")

    licenses = Counter(str(resource["provenance"]["license_spdx"]) for resource in resources)
    classifications = Counter(str(resource["provenance"]["classification"]) for resource in resources)
    query_kinds = Counter(str(case["case_kind"]) for case in cases)
    input_sha256 = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in _iter_input_paths()
    }

    manifest: dict[str, Any] = {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "phase": "input_freeze",
        "candidate_output_policy": {
            "allowed": False,
            "required_consumer_check": "freeze contract and byte digests must match before evaluation",
        },
        "source_contracts": {
            "corpus": corpus["contract_digest"],
            "queries": queries["contract_digest"],
            "benchmark": benchmark["contract_digest"],
        },
        "input_sha256": input_sha256,
        "inventory": {
            "resources": len(resources),
            "queries": len(cases),
            "resource_kinds": corpus["roadmap_scale"]["actual"],
            "query_kinds": dict(sorted(query_kinds.items())),
            "licenses": dict(sorted(licenses.items())),
            "classifications": dict(sorted(classifications.items())),
            "capabilities": len(EXPECTED_CAPABILITIES),
            "dod_sections": len(EXPECTED_DOD),
            "privacy_sentinel_values": len(sentinels["forbidden_values"]),
            "privacy_sentinel_keys": len(sentinels["forbidden_keys"]),
        },
        "generator_contract": {
            "public_corpus": "tests/evaluation/generate_fixture.py",
            "aggregate_freeze": "scripts/build_v11_test_assets.py",
            "seed": config["seed"],
            "deterministic": True,
        },
        "evidence_boundary": {
            "proves": "public licensed provider-free input identity only",
            "non_claims": [
                "candidate relevance or thresholds",
                "application offline E2E",
                "latency or capacity",
                "installed packages",
                "live providers or private corpus",
                "Windows or Python 3.12",
                "visual or acoustic similarity",
                "universal quality",
            ],
        },
    }
    manifest["contract_digest"] = _contract_digest(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the checked-in manifest without writing")
    args = parser.parse_args()

    manifest = build_manifest()
    expected = _published_bytes(manifest)
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != expected:
            raise SystemExit("frozen evaluation manifest is missing or stale")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(expected)

    summary = {
        "status": "ok",
        "phase": manifest["phase"],
        "contract_digest": manifest["contract_digest"],
        "candidate_results_allowed": manifest["candidate_output_policy"]["allowed"],
        "resources": manifest["inventory"]["resources"],
        "queries": manifest["inventory"]["queries"],
        "input_files": len(manifest["input_sha256"]),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
