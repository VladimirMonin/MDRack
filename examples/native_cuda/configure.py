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

_IMAGE_CONTRACT_VARIABLE = "mdrack-native-image-variable-v1"
_IMAGE_BOUNDS = {
    "width": 8192, "height": 8192, "pixels": 16_777_216,
    "RGB_bytes": 50_331_648, "PNG_bytes": 67_108_864,
    "channels": 3, "max_tokens": 2048, "image_min_tokens": 70, "image_max_tokens": 1120,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "executable", "source", "runtime-path", "vendor-path", "work-directory", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--protocol", choices=("mdrack-cuda-abi12-v1", "mdrack-cuda-retrieval-v2"),
                        default="mdrack-cuda-abi12-v1", help="Explicit opt-in; never rekeys old catalogs/profiles.")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("private configuration must remain outside the source checkout")
    profile = json.loads(args.profile.read_bytes())
    metadata = profile["metadata"]
    expected = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected != profile["fingerprint"]:
        parser.error("profile fingerprint mismatch")
    if args.protocol == "mdrack-cuda-retrieval-v2" and metadata.get("protocol") != args.protocol:
        parser.error("extended producer profile required")
    contract = metadata.get("image_input_contract")
    if contract not in (None, _IMAGE_CONTRACT_VARIABLE):
        parser.error("unknown image input contract")
    if contract == _IMAGE_CONTRACT_VARIABLE:
        if args.protocol != "mdrack-cuda-retrieval-v2":
            parser.error("variable image contract requires retrieval-v2")
        if metadata.get("bounds") != _IMAGE_BOUNDS:
            parser.error("variable image contract bounds mismatch")
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
        "protocol": args.protocol,
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
