"""Create an opt-in PRIVATE config from a caller-qualified ABI12 producer profile.

This copies metadata only, never runtime libraries/models/vectors. No inference,
provider registration, server, environment mutation or source discovery occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "executable", "source", "runtime-path", "vendor-path", "work-directory", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("private configuration must remain outside the source checkout")
    profile = json.loads(args.profile.read_bytes())
    metadata = profile["metadata"]
    expected = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected != profile["fingerprint"]:
        parser.error("profile fingerprint mismatch")
    pins = {row["path"]: row["sha256"] for row in metadata["runtime_file_identities"]}
    for row in metadata["actual_loaded_release_modules"]:
        pins[row["path"]] = row["sha256"]
    pins[str(args.executable.resolve())] = metadata["host_exe_sha256"]
    pins[str(args.source.resolve())] = metadata["host_source_sha256"]
    weights = {Path(row["path"]).name: row["path"] for row in metadata["runtime_file_identities"]
               if row["role"] == "weight"}
    for path in (args.executable, args.source):
        if hashlib.sha256(path.read_bytes()).hexdigest() != pins[str(path.resolve())]:
            parser.error("producer identity mismatch")
    payload = {
        "protocol": "mdrack-cuda-abi12-v1",
        "executable": str(args.executable.resolve()), "source": str(args.source.resolve()),
        "model": weights["embeddinggemma-2-Q8_0.gguf"], "projector": weights["mmproj-F16.gguf"],
        "files": [{"path": p, "sha256": d} for p, d in pins.items()],
        "encoder_profile": profile, "timeout_seconds": 120,
        "work_directory": str(args.work_directory.resolve()),
        "environment": {
            "PATH": os.pathsep.join(str(p.resolve()) for p in
                                    (args.runtime_path, args.vendor_path, Path(os.environ["SystemRoot"]) / "System32")),
            "CUDA_VISIBLE_DEVICES": metadata["recipe"]["selected_device"]["uuid"],
            "ORACLE_CUDA_DEVICE": "CUDA0",
        },
    }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
