"""Outer composition for the pinned caller-owned CUDA ABI12 executable.

No DLL/model loading occurs in Python. Configuration is a trusted local caller
selection, NOT an imported resource manifest. Never load it from indexed content.
The child receives private prepared bytes, never source locators or caller IDs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from mdrack.domain.embedding_inputs import (
    EmbeddingResult,
    EncodingRole,
    ImageInput,
    InputCapability,
    PreparedInput,
    RouteCapabilities,
    TextInput,
)
from mdrack.ports.embeddings import EmbeddingError
from mdrack_core import EmbeddingSpaceRecord, JSONValue

_PROTOCOL = "mdrack-cuda-abi12-v1"
_PREFIX = "task: search result | query: "
_PROTOCOL_V2 = "mdrack-cuda-retrieval-v2"
_DOCUMENT_PREFIX = "title: none | text: "
_ROLE_RECIPE = {
    "document_prefix": _DOCUMENT_PREFIX, "query_prefix": _PREFIX,
    "format_owner": "python-once-body-unchanged", "title": "literal-none-no-filename",
    "text_tokenization": "native-llama_tokenize-add_special=true-parse_special=true",
    "formatted_wire_bytes": 65536, "context_batch_ubatch": 2048,
    "roles": ("document:image:image", "document:text:text", "query:text:image,text"),
}
_MAX = 2 * 1024 * 1024


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def _canonical(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file(value: object) -> Path:
    if not isinstance(value, str):
        raise ValueError("external_host_config_invalid")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("external_host_config_invalid")
    return path


class CUDAEncoderProvider:
    """Prepared provider owned/closed by the outer caller, never by Core.

    Only image documents and text queries for image candidates are admitted.
    Every call is a fresh process/forward. No stored/old vector fallback exists.
    """

    def __init__(self, config_path: Path) -> None:
        try:
            self._configure(config_path)
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError("external_host_config_invalid") from None

    def _configure(self, config_path: Path) -> None:
        self._closed = False
        self._lock = asyncio.Lock()
        self.calls = 0
        self.completed = 0
        self.diagnostic_losses = 0
        self._config_path = _file(str(config_path))
        if self._config_path.stat().st_size > _MAX:
            raise ValueError("external_host_config_invalid")
        self._config_hash = _hash(self._config_path)
        config = json.loads(self._config_path.read_bytes())
        if not isinstance(config, dict) or config.get("protocol") not in {_PROTOCOL, _PROTOCOL_V2}:
            raise ValueError("external_host_config_invalid")
        self._protocol = str(config["protocol"])
        self._extended = self._protocol == _PROTOCOL_V2
        self._config: dict[str, Any] = config
        self._exe = _file(config["executable"])
        self._source = _file(config["source"])
        self._model = _file(config["model"])
        self._projector = _file(config["projector"])
        self._pins: tuple[tuple[Path, str], ...] = tuple(
            (_file(row["path"]), row["sha256"]) for row in config["files"]
        )
        if not self._pins or len(self._pins) > 64:
            raise ValueError("external_host_config_invalid")
        for _, digest in self._pins:
            if not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
                raise ValueError("external_host_config_invalid")
        if not {self._exe, self._source, self._model, self._projector} <= {p for p, _ in self._pins}:
            raise ValueError("external_host_config_invalid")
        self._timeout = config.get("timeout_seconds", 120)
        if type(self._timeout) not in (int, float) or not math.isfinite(self._timeout) or not 1 <= self._timeout <= 300:
            raise ValueError("external_host_config_invalid")
        if self._extended and self._timeout > 120:
            raise ValueError("external_host_config_invalid")
        # An explicit caller-selected work directory; no model/cache/global mutation.
        self._work = Path(config["work_directory"])
        if not self._work.is_absolute() or not self._work.is_dir() or self._work.is_symlink():
            raise ValueError("external_host_config_invalid")
        self._diagnostics: Path | None = None
        if config.get("private_diagnostic_directory") is not None:
            self._diagnostics = Path(config["private_diagnostic_directory"])
            if (not self._diagnostics.is_absolute() or not self._diagnostics.is_dir()
                    or self._diagnostics.is_symlink()):
                raise ValueError("external_host_config_invalid")
        profile = config["encoder_profile"]
        if _canonical(profile["metadata"]) != profile["fingerprint"]:
            raise ValueError("external_host_profile_invalid")
        if profile["dimensions"] != 768 or profile["metric"] != "cosine":
            raise ValueError("external_host_profile_invalid")
        encoder = profile["metadata"]
        if self._extended and (encoder.get("protocol") != _PROTOCOL_V2
                               or _canonical(encoder.get("role_recipe")) != _canonical(_ROLE_RECIPE)):
            raise ValueError("external_host_profile_invalid")
        pinned = dict(self._pins)
        expected = encoder["runtime_file_identities"]
        if (pinned[self._exe] != encoder["host_exe_sha256"]
                or pinned[self._source] != encoder["host_source_sha256"]
                or any(pinned.get(Path(row["path"])) != row["sha256"] for row in expected)
                or {self._model, self._projector} != {Path(row["path"]) for row in expected if row["role"] == "weight"}
                or self._model.name != "embeddinggemma-2-Q8_0.gguf"
                or self._projector.name != "mmproj-F16.gguf"
                or config["environment"]["CUDA_VISIBLE_DEVICES"] != encoder["recipe"]["selected_device"]["uuid"]):
            raise ValueError("external_host_profile_invalid")
        for row in encoder["actual_loaded_release_modules"]:
            if pinned.get(Path(row["path"])) != row["sha256"]:
                raise ValueError("external_host_profile_invalid")
        metadata: dict[str, JSONValue] = {
            "producer": self._protocol,
            "encoder_profile_fingerprint": profile["fingerprint"],
            "executable_sha256": dict(self._pins)[self._exe],
            "source_sha256": dict(self._pins)[self._source],
            "query_prefix": _PREFIX,
            "document_input": "image/png;224x224;8bit RGB;native WIC CPU decode",
            "pooling": "native mean;raw-f32;double-L2-to-f32",
            "roles": ("document:image", "query:text:image"),
            "vector_value_policy": "ieee754-f32-canonical-v1",
            "vector_codec": "ieee754-f32-le-v1",
        }
        if self._extended:
            metadata.update({
                "document_prefix": _DOCUMENT_PREFIX, "title_rule": "literal-none-no-filename",
                "document_input": "image/png224RGB-or-text-UTF8;no-derived-image-text",
                "roles": ("document:image:image", "document:text:text", "query:text:image,text"),
                "role_recipe_fingerprint": _canonical(_ROLE_RECIPE),
                "formatted_wire_bytes": 65536, "context_batch_ubatch": 2048,
            })
        fingerprint = _canonical(metadata)
        self.space = EmbeddingSpaceRecord("external-cuda-" + fingerprint, 768, "cosine", fingerprint, metadata)
        self.capabilities = RouteCapabilities((
            InputCapability(EncodingRole.DOCUMENT, "image", ("image",), 1048576),
            InputCapability(EncodingRole.QUERY, "text", ("image",), 65536 - len(_PREFIX)),
        ))
        if self._extended:
            self.capabilities = RouteCapabilities((
                InputCapability(EncodingRole.DOCUMENT, "image", ("image",), 1048576),
                InputCapability(EncodingRole.DOCUMENT, "text", ("text",),
                                65536 - len(_DOCUMENT_PREFIX.encode("utf-8"))),
                InputCapability(EncodingRole.QUERY, "text", ("image", "text"),
                                65536 - len(_PREFIX.encode("utf-8"))),
            ))
        self._verify()

    def _verify(self) -> None:
        if _hash(self._config_path) != self._config_hash:
            raise EmbeddingError("external_host_identity_changed")
        for path, digest in self._pins:
            if path.is_symlink() or _hash(path) != digest:
                raise EmbeddingError("external_host_identity_changed")

    async def aclose(self) -> None:
        # Concurrent encode owns its process through finally; close waits for reap.
        self._closed = True
        async with self._lock:
            pass

    def _prepare(self, value: PreparedInput, role: EncodingRole) -> tuple[bytes, str]:
        self.capabilities.validate(value, role, () if self._extended else ("image",))
        if isinstance(value, ImageInput):
            data = value.content
            if (value.media_type != "image/png" or len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n"
                    or data[8:16] != b"\x00\x00\x00\rIHDR"
                    or struct.unpack(">II", data[16:24]) != (224, 224) or data[24:29] != b"\x08\x02\x00\x00\x00"):
                raise ValueError("external_host_image_bounds")
            kind = "image"
        elif isinstance(value, TextInput):
            document = self._extended and role is EncodingRole.DOCUMENT
            data = ((_DOCUMENT_PREFIX if document else _PREFIX) + value.text).encode("utf-8")
            if len(data) > 65536:
                raise ValueError("embedding_input_too_large")
            kind = "document" if document else "query"
        else:
            raise ValueError("unsupported_embedding_route")
        return data, kind

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        data, kind = self._prepare(value, role)
        async with self._lock:
            if self._closed:
                raise EmbeddingError("external_host_closed")
            try:
                self._verify()
                return await self._execute(data, kind)
            except (OSError, ValueError, KeyError, struct.error, subprocess.SubprocessError):
                raise EmbeddingError("external_host_failed") from None

    async def _execute(self, data: bytes, kind: str) -> EmbeddingResult:
        if os.name != "nt":
            raise EmbeddingError("external_host_platform_unsupported")
        from mdrack.hosts._windows_job import Job, _Process

        # Entire directory is private; exclusive temp ownership, including failures.
        with tempfile.TemporaryDirectory(prefix="mdrack-encoder-", dir=self._work) as temporary:
            work = Path(temporary)
            for name in ("fixtures", "inputs", "wire", "cache", "tmp"):
                (work / name).mkdir()
            exe = work / "encoder.exe"
            shutil.copyfile(self._exe, exe)
            if _hash(exe) != dict(self._pins)[self._exe]:
                raise EmbeddingError("external_host_identity_changed")
            selected = work / ("fixtures" if kind == "image" else "inputs") / "input"
            selected.write_bytes(data)
            pins = work / "pins.tsv"
            pins.write_text("".join(d + "\t" + str(p) + "\n" for p, d in self._pins), encoding="utf-8")
            output = work / "wire" / "result"
            env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "COMSPEC") if k in os.environ}
            environment = self._config["environment"]
            # Finite child-local environment, not arbitrary config options or shell expansion.
            for name in ("PATH", "CUDA_VISIBLE_DEVICES", "ORACLE_CUDA_DEVICE"):
                if not isinstance(environment[name], str) or not environment[name]:
                    raise ValueError("external_host_config_invalid")
                env[name] = environment[name]
            if env["ORACLE_CUDA_DEVICE"] != "CUDA0" or env["CUDA_VISIBLE_DEVICES"] == "-1":
                raise ValueError("external_host_config_invalid")
            env.update(TEMP=str(work / "tmp"), TMP=str(work / "tmp"), CUDA_CACHE_PATH=str(work / "cache"),
                       OMP_NUM_THREADS="4", OMP_WAIT_POLICY="PASSIVE")
            operation = uuid.uuid4().hex if self._extended else "prepared"
            argv = [str(exe), str(self._model), str(self._projector), kind,
                    str(selected), str(output), operation, str(pins)]
            binding: dict[str, str | int] | None = None
            if self._extended:
                role = "query" if kind == "query" else "document"
                wire_digest = hashlib.sha256(data).hexdigest()
                argv += [self._protocol, self.space.fingerprint, wire_digest, role]
                binding = {"protocol": self._protocol, "operation": operation,
                           "profile_fingerprint": self.space.fingerprint,
                           "formatted_input_sha256": wire_digest, "role": role}
            with (work / "stdout").open("wb") as stdout, (work / "stderr").open("wb") as stderr:
                job = Job()
                child: _Process | None = None
                try:
                    self.calls += 1  # Failed dispatch/timeout also counts; never a fresh-vector cache hit.
                    child = job.popen(argv, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                    job.resume()
                    deadline = time.monotonic() + self._timeout
                    while child.poll() is None:
                        if time.monotonic() >= deadline:
                            raise EmbeddingError("external_host_timeout")
                        if stdout.tell() > _MAX or stderr.tell() > _MAX:
                            raise EmbeddingError("external_host_output_limit")
                        await asyncio.sleep(.02)
                    child.wait()
                    if child.returncode != 0:
                        raise EmbeddingError("external_host_failed")
                finally:
                    try:
                        try:
                            job.close()  # Only this owned tree, including cancellation.
                        finally:
                            if child is not None:
                                try:
                                    child.wait(timeout=15)
                                finally:
                                    child.close()
                    finally:
                        stdout.flush()
                        stderr.flush()
                        self._retain_diagnostics(work, kind, child, job.closed, argv)
            self._verify()
            assert child is not None
            vector = self._result(work, output, kind, child.pid, binding)
            self.completed += 1
            return EmbeddingResult(vector, self.space)

    def _retain_diagnostics(
        self, work: Path, kind: str, child: Any, job_closed: bool, argv: list[str] | None = None,
    ) -> None:
        # Explicit caller opt-in to PRIVATE producer diagnostics. Never a log,
        # event or CLI payload. Producer details may be sensitive. Input and
        # vector files are never copied to this optional diagnostic directory.
        if self._diagnostics is None:
            return
        try:
            directory = Path(tempfile.mkdtemp(prefix="call-", dir=self._diagnostics))
            for name in ("stdout", "stderr"):
                with (work / name).open("rb") as stream:
                    (directory / name).write_bytes(stream.read(_MAX))
            receipt = {
                "kind": kind, "pid": None if child is None else child.pid,
                "native_exit": None if child is None else child.returncode,
                "owned_job_closed": job_closed,
                "producer_executable_sha256": dict(self._pins)[self._exe],
                "profile_fingerprint": self.space.fingerprint,
                "argv": argv if argv is not None else [
                    str(work / "encoder.exe"), str(self._model), str(self._projector), kind,
                    str(work / ("fixtures" if kind == "image" else "inputs") / "input"),
                    str(work / "wire" / "result"), "prepared", str(work / "pins.tsv"),
                ],
                "admission": "native exit only; result validation occurs separately",
            }
            (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        except OSError:
            self.diagnostic_losses = min(2**31 - 1, self.diagnostic_losses + 1)

    @staticmethod
    def _result(
        work: Path, output: Path, kind: str, pid: int, binding: dict[str, str | int] | None = None,
    ) -> tuple[float, ...]:
        def bounded(path: Path, maximum: int) -> bytes:
            if path.is_symlink() or path.stat().st_size > maximum:
                raise ValueError("external_host_output_invalid")
            return path.read_bytes()
        stdout = bounded(work / "stdout", _MAX)
        stderr = bounded(work / "stderr", _MAX)
        records = [json.loads(line) for line in stdout.splitlines()]
        if len(records) != 2 or records[-1] != {"owned_cleanup_completed": True}:
            raise ValueError("external_host_partial_output")
        result = records[0]
        if binding is not None and (result.get("pid") != pid
                                    or any(result.get(key) != value for key, value in binding.items())):
            raise ValueError("external_host_binding_mismatch")
        if result["dimensions"] != 768 or result["cuda_device"] != "CUDA0" or result["kind"] != kind:
            raise ValueError("external_host_output_invalid")
        ops = [json.loads(line[9:]) for line in stderr.splitlines() if line.startswith(b"GPU_EVAL ")]
        stages = {op["stage"] for op in ops}
        if (not {"backbone"} <= stages or (kind == "image" and "vision" not in stages)
                or any(op["pid"] != pid or op["operation"] != "MUL_MAT"
                       or op["device"] != "CUDA0" or op["after_evaluation"] is not True
                       or op["finite_nonzero_synchronous_readback"] is not True for op in ops)):
            raise ValueError("external_host_accelerator_unverified")
        raw_data = bounded(Path(str(output) + ".raw.f32"), 3072)
        vector_data = bounded(Path(str(output) + ".f32"), 3072)
        if len(raw_data) != 3072 or len(vector_data) != 3072:
            raise ValueError("external_host_output_invalid")
        raw = struct.unpack("<768f", raw_data)
        vector = struct.unpack("<768f", vector_data)
        norm = math.sqrt(sum(x*x for x in raw))
        if (not math.isfinite(norm) or norm <= 0 or not all(math.isfinite(x) for x in vector)
                or struct.pack("<768f", *(x/norm for x in raw)) != vector_data
                or abs(math.sqrt(sum(x*x for x in vector)) - 1) > .0001):
            raise ValueError("external_host_output_invalid")
        return vector
