"""Guarded entrypoint reserved for the dedicated LIVE LM Studio stage."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Set
from pathlib import Path

from mdrack.config.loader import load_config
from mdrack.integrations.lmstudio.client import LMStudioControlClient
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


def _parse_dimensions(raw: str) -> tuple[int, ...]:
    try:
        dimensions = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError as exc:
        raise ValueError("dimensions must be comma-separated positive integers") from exc
    if not dimensions or any(value < 1 for value in dimensions) or len(set(dimensions)) != len(dimensions):
        raise ValueError("dimensions must be unique positive integers")
    return dimensions


def _endpoint_ref(endpoint: str) -> str:
    return "sha256:" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]


def _safe_state(value: str | None) -> str:
    return value if value in {"loaded", "active", "running", "idle", "unloaded", "not-loaded"} else "unknown"


async def _run_live_matrix(
    *,
    endpoint: str,
    model: str,
    requested_dimensions: tuple[int, ...],
    allow_model_load: bool,
    config_source: str,
) -> dict[str, object]:
    """Run only explicitly authorized, privacy-safe capability probes."""
    client = LMStudioControlClient(endpoint)
    calls_attempted = 0
    cleanup: dict[str, object] = {"model_loaded_by_runner": False, "unload_status": "not_required"}
    report: dict[str, object] = {
        "contract": "mdrack.stage9-live-dimension-capability-v1",
        "status": "failed",
        "reason_code": "live_call_failed",
        "calls_attempted": 0,
        "config": {
            "config_source": config_source,
            "endpoint_configured": bool(endpoint),
            "endpoint_ref": _endpoint_ref(endpoint),
            "model": model,
            "requested_dimensions": list(requested_dimensions),
        },
        "cleanup": cleanup,
        "privacy": {
            "raw_queries_included": False,
            "raw_content_included": False,
            "paths_included": False,
            "vectors_included": False,
            "endpoint_included": False,
            "provider_responses_included": False,
        },
    }
    loaded_instance_id: str | None = None
    try:
        models = await client.list_models()
        calls_attempted += 1
        selected = next((item for item in models if item.key == model), None)
        if selected is None:
            report.update(
                status="blocked_runtime_capability",
                reason_code="model_not_discovered",
                selected_model_discovered=False,
            )
            return report

        report.update(
            selected_model_discovered=True,
            selected_model_state_before_run=_safe_state(selected.state),
            selected_model_loaded_before_run=selected.loaded,
        )
        if not selected.loaded:
            if not allow_model_load:
                report.update(status="blocked_runtime_capability", reason_code="model_not_loaded")
                return report
            loaded = await client.load_model(model)
            calls_attempted += 1
            loaded_instance_id = loaded.instance_id
            cleanup["model_loaded_by_runner"] = True
            cleanup["unload_status"] = "pending"

        native_dimensions = await client.probe_embedding_dimensions(model)
        calls_attempted += 1
        if native_dimensions < 1:
            raise ValueError("LM Studio returned an invalid native dimension")
        matrix: list[dict[str, object]] = []
        reduced_dimension_results: list[bool] = []
        for requested in requested_dimensions:
            returned = await client.probe_embedding_dimensions(model, dimensions=requested)
            calls_attempted += 1
            if requested < native_dimensions:
                reduced_dimension_results.append(returned == requested)
            matrix.append(
                {
                    "requested_dimensions": requested,
                    "returned_dimensions": returned,
                    "vector_length_valid": returned == requested,
                    "status": "returned_requested" if returned == requested else "returned_other_dimension",
                }
            )
        report.update(
            status="completed",
            reason_code="completed",
            native_dimensions=native_dimensions,
            matrix=matrix,
            all_reduced_dimensions_supported=bool(reduced_dimension_results) and all(reduced_dimension_results),
        )
        return report
    except Exception:
        report.update(status="failed", reason_code="live_call_failed")
        return report
    finally:
        report["calls_attempted"] = calls_attempted
        if cleanup["model_loaded_by_runner"]:
            if loaded_instance_id is None:
                cleanup["unload_status"] = "not_attempted_no_instance_id"
            else:
                try:
                    await client.unload_model(loaded_instance_id)
                    calls_attempted += 1
                    cleanup["unload_status"] = "unloaded"
                except Exception:
                    cleanup["unload_status"] = "unload_failed"
            try:
                loaded_after_cleanup = await client.list_models()
                calls_attempted += 1
                cleanup["loaded_models_after_cleanup"] = sum(
                    item.loaded for item in loaded_after_cleanup
                )
            except Exception:
                cleanup["loaded_models_after_cleanup"] = "readback_failed"
            report["calls_attempted"] = calls_attempted
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Explicitly acknowledge that this is the later LIVE stage.",
    )
    parser.add_argument("--config", type=Path, help="Optional MDRack TOML configuration source.")
    parser.add_argument("--endpoint", help="LM Studio endpoint override; never emitted in evidence.")
    parser.add_argument("--model", help="Embedding-model key override.")
    parser.add_argument("--dimensions", default="1024,768,512,384")
    parser.add_argument(
        "--allow-model-load",
        action="store_true",
        help="Permit this run to load an unloaded model and unload only its own instance afterward.",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        print(json.dumps({"status": "live_confirmation_required", "calls_attempted": 0}, sort_keys=True))
        return 2
    try:
        dimensions = _parse_dimensions(args.dimensions)
        config = load_config(toml_path=args.config)
        endpoint = args.endpoint or config.embedding.endpoint
        model = args.model or config.embedding.model
        if not endpoint or not model:
            raise ValueError("LM Studio endpoint and model must be configured")
    except Exception:
        print(json.dumps({"status": "configuration_invalid", "calls_attempted": 0}, sort_keys=True))
        return 2
    report = asyncio.run(
        _run_live_matrix(
            endpoint=endpoint,
            model=model,
            requested_dimensions=dimensions,
            allow_model_load=args.allow_model_load,
            config_source="explicit_toml" if args.config is not None else "defaults_or_environment",
        )
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
