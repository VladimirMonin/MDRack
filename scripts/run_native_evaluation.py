#!/usr/bin/env python3
"""Offline native corpus preparation and public-engine scenario runner.

No default provider and no network acquisition. Explicit host factories supply
already configured providers; draft corpora are refused before factory loading.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

from mdrack.eval.native_corpus import NativeCorpusError, array, decode, require_evaluable, validate_native_bundle
from mdrack.eval.native_retained import import_retained_pack
from mdrack.eval.native_runner import RunConfig, run_native_experiment
from mdrack.eval.privacy import scan_privacy
from mdrack.ports.embeddings import PreparedEmbeddingProvider


def _factory(spec: str) -> PreparedEmbeddingProvider:
    module, separator, name = spec.partition(":")
    if not separator or not module or not name:
        raise NativeCorpusError("provider_factory_required")
    factory: object = getattr(importlib.import_module(module), name)
    if not callable(factory):
        raise NativeCorpusError("provider_factory_required")
    provider: object = factory()
    if not isinstance(provider, PreparedEmbeddingProvider):
        raise NativeCorpusError("invalid_prepared_provider")
    return provider


def _read(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise NativeCorpusError("input_too_large")
    return decode(data.decode("utf-8"))


def _write(path: Path, value: object) -> None:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2).encode() + b"\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".native-eval-", delete=False) as f:
            temporary = Path(f.name)
            f.write(encoded)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise NativeCorpusError("invalid_arguments")


def main() -> int:
    try:
        parser = _Parser(description=__doc__)
        sub = parser.add_subparsers(dest="operation", required=True, parser_class=_Parser)
        prepare = sub.add_parser("prepare-retained")
        prepare.add_argument("--pack-root", type=Path, required=True)
        prepare.add_argument("--manifest-ref", required=True)
        prepare.add_argument("--output", type=Path, required=True)
        inspect = sub.add_parser("inspect")
        inspect.add_argument("--bundle", type=Path, required=True)
        inspect.add_argument("--media-root", type=Path, required=True)
        run = sub.add_parser("run")
        for name in ("bundle", "config", "media-root", "output-root", "output"):
            run.add_argument("--" + name, type=Path, required=True)
        run.add_argument("--provider-a", required=True)
        run.add_argument("--provider-b", required=True)
        args = parser.parse_args()
        exit_code = 0
        if args.operation == "prepare-retained":
            if args.output.resolve().is_relative_to(args.pack_root.resolve()):
                raise NativeCorpusError("output_overlaps_inputs")
            doc = import_retained_pack(args.pack_root, args.manifest_ref)
            _write(args.output, doc)
            result: dict[str, object] = {
                "ok": True,
                "state": "draft",
                "evaluable": False,
                "media_samples": len(array(doc["resources"])),
                "grades_created": 0,
            }
        else:
            bundle = validate_native_bundle(_read(args.bundle), media_root=args.media_root)
            if args.operation == "inspect":
                result = {
                    "ok": True,
                    "state": bundle.state,
                    "media_samples": len(bundle.resources),
                    "query_cases": len(bundle.queries),
                    "corpus_ref": bundle.corpus_ref,
                    "publication_ready": all(r.publishable for r in bundle.resources),
                }
                try:
                    require_evaluable(bundle)
                    result["evaluable"] = True
                except NativeCorpusError:
                    result["evaluable"] = False
            else:
                if args.output.resolve().is_relative_to(args.media_root.resolve()) or args.output.resolve() in {
                    args.bundle.resolve(),
                    args.config.resolve(),
                }:
                    raise NativeCorpusError("output_overlaps_inputs")
                require_evaluable(bundle)  # Before importing/calling a host factory.
                configuration = RunConfig.parse(_read(args.config)).document
                result = run_native_experiment(
                    bundle,
                    configuration,
                    media_root=args.media_root,
                    output_root=args.output_root,
                    provider_a=_factory(args.provider_a),
                    provider_b=_factory(args.provider_b),
                )
                if not scan_privacy(result).safe:
                    raise NativeCorpusError("unsafe_report")
                _write(args.output, result)
                exit_code = 0 if result["fitness"] in {"SYNTHETIC_MECHANICS_ONLY", "POLICY_SATISFIED"} else 2
                result = {"ok": True, "executed_cells": result["executed_cells"], "fitness": result["fitness"]}
        print(json.dumps(result, allow_nan=False))
        return exit_code
    except Exception:
        # A CLI privacy boundary, not a success fallback: preserve the existing
        # output and return nonzero, including for unexpected host exceptions.
        print('{"ok":false,"error":"native_evaluation_refused"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
