"""Guarded entrypoint reserved for the dedicated LIVE LM Studio stage."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Set

from mdrack.ports.model_catalog import EmbeddingCapabilityEvidence

_TARGET_MODEL_IDS = (
    "qwen3-embedding-0.6b",
    "qwen3-embedding-4b",
    "qwen3-embedding-8b",
)


def _normalize_model_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.casefold())


def _resolve_target_model_id(key: str) -> str | None:
    normalized = _normalize_model_key(key)
    matches = [
        model_id
        for model_id in _TARGET_MODEL_IDS
        if _normalize_model_key(model_id) in normalized
    ]
    if len(matches) > 1:
        raise ValueError("ambiguous LM Studio catalog key")
    return matches[0] if matches else None


def _resolved_key_set(
    keys: Set[str],
    *,
    reject_unrecognized: bool = False,
) -> set[str]:
    resolved: set[str] = set()
    for key in keys:
        model_id = _resolve_target_model_id(key)
        if model_id is None and reject_unrecognized:
            raise ValueError("runtime evidence requires a recognized target model")
        if model_id is not None:
            resolved.add(model_id)
    return resolved


def _resolved_tested_dimensions(
    evidence: Mapping[str, tuple[int, int, int]],
) -> dict[str, tuple[int, int, int]]:
    resolved: dict[str, tuple[int, int, int]] = {}
    for key, dimensions in evidence.items():
        model_id = _resolve_target_model_id(key)
        if model_id is None:
            raise ValueError("runtime evidence requires a recognized target model")
        previous = resolved.get(model_id)
        if previous is not None and previous != dimensions:
            raise ValueError("ambiguous runtime evidence for target model")
        resolved[model_id] = dimensions
    return resolved


def build_capability_report(
    *,
    discovered_model_keys: Set[str],
    tested_dimensions: Mapping[str, tuple[int, int, int]] | None = None,
    unsupported_model_keys: Set[str] | None = None,
) -> dict[str, object]:
    """Build a report from supplied evidence without performing runtime calls."""
    tested_dimensions = tested_dimensions or {}
    unsupported_model_keys = unsupported_model_keys or set()
    discovered = _resolved_key_set(discovered_model_keys)
    tested = _resolved_tested_dimensions(tested_dimensions)
    unsupported = _resolved_key_set(unsupported_model_keys, reject_unrecognized=True)

    unexpected_evidence = (set(tested) | unsupported) - discovered
    if unexpected_evidence:
        raise ValueError("runtime evidence requires a discovered model")

    models: list[dict[str, object]] = []
    for model_id in _TARGET_MODEL_IDS:
        if model_id in tested:
            native, requested, returned = tested[model_id]
            evidence = EmbeddingCapabilityEvidence(
                model_id=model_id,
                status="tested",
                native_dimensions=native,
                requested_dimensions=requested,
                returned_dimensions=returned,
                vector_length_valid=requested == returned,
            )
        elif model_id in unsupported:
            evidence = EmbeddingCapabilityEvidence(model_id=model_id, status="unsupported")
        elif model_id in discovered:
            evidence = EmbeddingCapabilityEvidence(model_id=model_id, status="not_tested")
        else:
            evidence = EmbeddingCapabilityEvidence(model_id=model_id, status="not_installed")
        models.append(evidence.as_dict())

    return {
        "status": "evidence_report",
        "calls_attempted": 0,
        "models": models,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Explicitly acknowledge that this is the later LIVE stage.",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        print(json.dumps({"status": "live_confirmation_required", "calls_attempted": 0}, sort_keys=True))
        return 2
    print(json.dumps({"status": "live_stage_not_implemented", "calls_attempted": 0}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
