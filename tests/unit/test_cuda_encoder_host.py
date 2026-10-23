"""Offline safety only: inert producer files, not learned/native CUDA vectors."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.domain.embedding_inputs import EncodingRole, ImageInput, TextInput
from mdrack.hosts.cuda_encoder import CUDAEncoderProvider
from mdrack.ports.embeddings import EmbeddingError


def config(tmp_path: Path) -> Path:
    def artifact(name: str) -> Path:
        p = tmp_path / name
        p.write_bytes(name.encode())
        return p
    exe, source = artifact("encoder.exe"), artifact("encoder.cpp")
    model, projector = artifact("embeddinggemma-2-Q8_0.gguf"), artifact("mmproj-F16.gguf")
    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    files = [{"path": str(p), "sha256": digest(p), "role": "weight"} for p in (model, projector)]
    metadata = {"runtime_file_identities": files, "actual_loaded_release_modules": [],
                "host_exe_sha256": digest(exe), "host_source_sha256": digest(source),
                "recipe": {"selected_device": {"uuid": "unit-device"}}}
    fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payload = {"protocol": "mdrack-cuda-abi12-v1", "executable": str(exe), "source": str(source),
               "model": str(model), "projector": str(projector), "work_directory": str(tmp_path),
               "files": files + [{"path": str(p), "sha256": digest(p)} for p in (exe, source)],
               "encoder_profile": {"dimensions": 768, "metric": "cosine", "metadata": metadata,
                                   "fingerprint": fingerprint},
               "environment": {"PATH": str(tmp_path), "CUDA_VISIBLE_DEVICES": "unit-device",
                               "ORACLE_CUDA_DEVICE": "CUDA0"}}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def test_explicit_capabilities_and_space_reopen(tmp_path: Path) -> None:
    path = config(tmp_path)
    one, two = CUDAEncoderProvider(path), CUDAEncoderProvider(path)
    assert one.space == two.space
    assert one.space.metadata == two.space.metadata
    assert one.space.space_id.startswith("external-cuda-")
    assert not one.capabilities.reuse_document_vector
    for role, modality in [(EncodingRole.DOCUMENT, "text"), (EncodingRole.QUERY, "image"),
                           (EncodingRole.SIMILARITY, "image")]:
        with pytest.raises(ValueError, match="unsupported_embedding_route"):
            one.capabilities.require(role, modality)
    assert str(tmp_path) not in json.dumps(dict(one.space.metadata), default=list)


@pytest.mark.asyncio
async def test_rejected_routes_and_png_bounds_never_dispatch(tmp_path: Path) -> None:
    provider = CUDAEncoderProvider(config(tmp_path))
    with pytest.raises(ValueError):
        await provider.encode(TextInput("secret-body", "id"), EncodingRole.DOCUMENT)
    with pytest.raises(ValueError):
        await provider.encode(ImageInput(b"not-png", "image/png", "id"), EncodingRole.DOCUMENT)
    assert provider.calls == 0
    await provider.aclose()
    with pytest.raises(EmbeddingError, match="external_host_closed"):
        await provider.encode(TextInput("query", "id"), EncodingRole.QUERY)


@pytest.mark.asyncio
async def test_config_and_producer_drift_fail_before_dispatch(tmp_path: Path) -> None:
    path = config(tmp_path)
    provider = CUDAEncoderProvider(path)
    (tmp_path / "encoder.cpp").write_bytes(b"changed")
    with pytest.raises(EmbeddingError, match="identity_changed"):
        await provider.encode(TextInput("secret-query", "id"), EncodingRole.QUERY)
    assert provider.calls == 0


def test_wrong_profile_or_model_binding_is_not_same_space(tmp_path: Path) -> None:
    p = config(tmp_path)
    value = json.loads(p.read_bytes())
    value["encoder_profile"]["metadata"]["host_exe_sha256"] = "0" * 64
    p.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="config_invalid"):
        CUDAEncoderProvider(p)


@pytest.mark.parametrize("provider", ["external", "lmstudio"])
def test_cli_explicit_config_refusal_is_one_safe_envelope(tmp_path: Path, provider: str) -> None:
    args = ["--root", str(tmp_path), "image", "native-search", "--text", "PRIVATE_QUERY",
            "--mode", "semantic", "--provider", provider]
    if provider == "lmstudio":
        args += ["--host-config", str(tmp_path / "PRIVATE_PATH")]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False and envelope["error"]["code"] == "NATIVE_SEARCH_ERROR"
    assert "PRIVATE_" not in result.output


def test_help_exposes_opt_in_without_changing_default() -> None:
    result = CliRunner().invoke(main, ["image", "native-ingest", "--help"])
    assert result.exit_code == 0 and "--host-config" in result.output and "external" in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_process_timeout_cancel_reaps_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows process ownership")
    from mdrack.hosts._windows_job import Job
    original = Job.popen
    children = []
    def leaf(self, args, **kwargs):
        child = original(self, [sys.executable, "-I", "-B", "-S", "-c", "import time;time.sleep(30)"], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(Job, "popen", leaf)
    p = config(tmp_path)
    payload = json.loads(p.read_bytes())
    payload["timeout_seconds"] = 1
    p.write_text(json.dumps(payload))
    provider = CUDAEncoderProvider(p)
    task = asyncio.create_task(provider.encode(TextInput("PRIVATE_QUERY", "id"), EncodingRole.QUERY))
    if cancel:
        for _ in range(200):
            if children or task.done():
                break
            await asyncio.sleep(.01)
        if task.done():
            await task
        assert children
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(EmbeddingError, match="external_host_timeout"):
            await task
    assert children and all(c.returncode == 1223 for c in children)
    assert not list(tmp_path.glob("mdrack-encoder-*"))
    assert provider.completed == 0 and provider.calls == 1


def test_partial_output_is_not_a_vector(tmp_path: Path) -> None:
    (tmp_path / "stdout").write_text('{"dimensions":768}\n')
    (tmp_path / "stderr").write_bytes(b"")
    with pytest.raises(ValueError, match="partial_output"):
        CUDAEncoderProvider._result(tmp_path, tmp_path / "result", "image", 42)


@pytest.mark.skipif(os.name != "nt", reason="Windows job-before-resume contract")
def test_job_before_resume_terminate_wait_and_cleanup(tmp_path: Path) -> None:
    from mdrack.hosts._windows_job import Job
    job = Job()
    with (tmp_path / "stdout").open("wb") as output:
        child = job.popen([sys.executable, "-I", "-B", "-S", "-c", "import time;time.sleep(30)"],
                          stdout=output, stderr=subprocess.STDOUT)
        try:
            assert job.creation_identity["job_assigned_before_any_child_instruction"]
            job.resume()
        finally:
            job.close()
            child.wait(timeout=15)
            child.close()
    assert job.closed and child.returncode == 1223
