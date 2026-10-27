"""Disposable synthetic mixed-folder integration through the registered surfaces.

These checks use real local SQLite/filesystem and the explicit synthetic prepared
provider. They are orchestration/locator evidence, not native model, real-corpus,
P04 image-decode or P06/P07 acceptance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.application.folder_import import FolderImportError
from mdrack.application.media_search import MediaSearchRequest
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import (
    EncodingRole,
    InputCapability,
    RouteCapabilities,
    TextInput,
)
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.ingestion.image_preprocessing import ImagePreparationError
from mdrack.public_api.engine import MDRackEngine
from mdrack_core import SearchScope
from tests.integration.test_native_image_v2 import PNG

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 12


class CountingProvider(SyntheticPreparedProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, EncodingRole]] = []

    async def encode(self, value: object, role: EncodingRole) -> object:
        self.calls.append((getattr(value, "modality", "unknown"), role))
        return await super().encode(value, role)  # type: ignore[arg-type]


class FailingImagePreparationProvider(CountingProvider):
    def prepare_image(self, content: bytes, media_type: str, revision: str) -> object:
        del content, media_type, revision
        raise ImagePreparationError("image_decode_failed")


class OtherSpaceProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.space = replace(self.space, space_id="synthetic-native-other", fingerprint="other-space")


class TinyTextInputProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.capabilities = RouteCapabilities((
            InputCapability(EncodingRole.DOCUMENT, "text", ("text",), 1),
            InputCapability(EncodingRole.DOCUMENT, "image", ("image",), 32 * 1024 * 1024),
            InputCapability(EncodingRole.QUERY, "text", ("text",), 32 * 1024 * 1024),
            InputCapability(EncodingRole.QUERY, "image", ("image",), 32 * 1024 * 1024),
        ))


def _engine(root: Path, provider: CountingProvider) -> MDRackEngine:
    return MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider)


def _catalog_counts(root: Path) -> dict[str, int]:
    path = root / ".mdrack" / "catalog.sqlite3"
    with sqlite3.connect(path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "core_resources",
                "core_representations",
                "core_search_units",
                "core_unit_embeddings",
            )
        }


def _source_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _source_tree(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def _result_by_ref(data: dict[str, object]) -> dict[str, dict[str, object]]:
    files = data["files"]
    assert isinstance(files, list)
    return {str(item["source_ref"]): item for item in files}


def _make_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise OSError("junction_unavailable")


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        _make_junction(link, target)


@pytest.mark.asyncio
async def test_mixed_folder_routes_branches_keeps_literal_txt_and_original_locators(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    md_text = "# Markdown heading\n\nMARKDOWN_TOKEN with ![alt text](missing.png).\n"
    txt_text = "# literal heading\n\n- literal list item\n\nTXT_LITERAL_TOKEN\n"
    (folder / "notes.md").write_text(md_text, encoding="utf-8")
    (folder / "plain.txt").write_text(txt_text, encoding="utf-8")
    (folder / "photo.jpg").write_bytes(JPEG)
    (folder / "scene.png").write_bytes(PNG)
    (folder / "graphic.webp").write_bytes(WEBP)
    before_hashes = _source_hashes(folder)
    before_tree = _source_tree(folder)
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        result = await engine.import_folders([("fixture", folder)])
        assert result.status == "success"
        assert result.counts["added"] == 5
        assert result.counts["failed"] == 0 and result.counts["skipped"] == 0
        assert result.model_calls == len(provider.calls) == 5

        by_ref = _result_by_ref(result.to_dict())
        assert by_ref["plain.txt"]["media_type"] == "text/plain"
        assert by_ref["plain.txt"]["kind"] == "document"
        assert by_ref["notes.md"]["media_type"] == "text/markdown"
        assert by_ref["photo.jpg"]["media_type"] == "image/jpeg"
        assert by_ref["scene.png"]["media_type"] == "image/png"
        assert by_ref["graphic.webp"]["media_type"] == "image/webp"

        txt_id = str(by_ref["plain.txt"]["resource_id"])
        md_id = str(by_ref["notes.md"]["resource_id"])
        assert engine.inspect_resource(txt_id).media_type == "text/plain"
        assert engine.inspect_resource(md_id).media_type == "text/markdown"

        kinds = {
            str(row["resource_kind"]): int(row["count"])
            for row in engine.storage.connection.execute(
                "SELECT resource_kind, COUNT(*) AS count FROM core_resources GROUP BY resource_kind"
            ).fetchall()
        }
        assert kinds == {"document": 2, "image": 3}

        literal = engine.search_text("TXT_LITERAL_TOKEN")
        assert len(literal.results) == 1
        locator = literal.results[0].source_locator
        assert (locator.root_id, locator.relative_path) == ("fixture", "plain.txt")
        unit = engine.read_unit(literal.results[0].logical_id)
        assert unit is not None and "# literal heading" in str(unit["text"])
        assert unit["evidence_locator"]["kind"] == "document_span"
        assert unit["evidence_locator"]["payload"]["relative_path"] == "plain.txt"
        assert locator.start_offset is not None and locator.end_offset is not None
        assert txt_text[locator.start_offset:locator.end_offset].startswith("# literal heading")

        markdown = engine.search_text("MARKDOWN_TOKEN")
        assert len(markdown.results) == 1
        assert markdown.results[0].source_locator.relative_path == "notes.md"
        assert markdown.results[0].source_locator.heading_path == ("Markdown heading",)

        image_search = await engine.search_media(MediaSearchRequest(
            TextInput("photo", "query"),
            SearchScope(modalities=("image",)),
            mode="semantic",
        ))
        image_refs = {
            str(item["evidence"][0]["locator"]["payload"]["source_ref"])
            for item in image_search.results
        }
        assert image_refs == {"photo.jpg", "scene.png", "graphic.webp"}
        assert all(
            item["evidence"][0]["locator"]["kind"] == "whole_image"
            for item in image_search.results
        )
        first_image_unit = engine.read_unit(str(image_search.results[0]["evidence"][0]["unit_id"]))
        assert first_image_unit is not None and first_image_unit["text"] is None
        assert "missing.png" not in image_refs

    assert _source_hashes(folder) == before_hashes
    assert _source_tree(folder) == before_tree
    assert not (folder / ".mdrack").exists()
    assert (app / ".mdrack" / "folders.json").is_file()


@pytest.mark.asyncio
async def test_two_roots_same_relative_name_are_distinct_and_b_does_not_delete_a(tmp_path: Path) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "notes.md").write_text("# A\n\nALPHA_TOKEN only in A.\n", encoding="utf-8")
    (root_b / "notes.md").write_text("# B\n\nBETA_TOKEN only in B.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()

    with _engine(app, CountingProvider()) as engine:
        first = await engine.import_folders([("root_a", root_a), ("root_b", root_b)])
        assert first.status == "success" and first.counts["added"] == 2
        by_ref = _result_by_ref(first.to_dict())
        assert by_ref["notes.md"]["resource_id"] is not None
        assert len({
            item["resource_id"] for item in first.to_dict()["files"]  # type: ignore[index]
        }) == 2

        alpha = engine.search_text("ALPHA_TOKEN")
        beta = engine.search_text("BETA_TOKEN")
        assert [(item.source_locator.root_id, item.source_locator.relative_path) for item in alpha.results] == [
            ("root_a", "notes.md")
        ]
        assert [(item.source_locator.root_id, item.source_locator.relative_path) for item in beta.results] == [
            ("root_b", "notes.md")
        ]

        before_counts = _catalog_counts(app)
        second = await engine.import_folders([("root_b", root_b)])
        assert second.status == "success"
        assert second.counts["added"] == 0 and second.counts["skipped"] == 1
        assert second.counts["failed"] == 0
        assert _catalog_counts(app) == before_counts
        assert engine.search_text("ALPHA_TOKEN").results
        assert engine.search_text("BETA_TOKEN").results


@pytest.mark.asyncio
async def test_configured_exclusions_are_kept_and_links_cannot_escape_scope(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "keep.md").write_text("# Keep\n\nKEEP_TOKEN.\n", encoding="utf-8")
    (folder / "private").mkdir()
    (folder / "private" / "secret.md").write_text("SECRET_TOKEN.\n", encoding="utf-8")
    (folder / "skip.tmp").write_text("TEMPORARY_TOKEN.\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.md").write_text("# Outside\n\nOUTSIDE_TOKEN.\n", encoding="utf-8")
    link = folder / "linked_outside"
    try:
        _make_directory_link(link, outside)
    except OSError:
        pytest.skip("directory symlink/junction unavailable")
    app = tmp_path / "app"
    app.mkdir()

    with _engine(app, CountingProvider()) as engine:
        result = await engine.import_folders(
            [("fixture", folder)],
            exclude=("private/**", "*.tmp"),
        )
        assert result.status == "partial_success"
        assert result.counts["added"] == 1
        assert result.counts["excluded"] == 2
        assert result.counts["skipped"] == 1
        reasons = {
            (item["source_ref"], item["status"], item["reason"])
            for item in result.to_dict()["files"]  # type: ignore[index]
        }
        assert ("linked_outside", "skipped", "link_not_followed") in reasons
        assert engine.search_text("KEEP_TOKEN").results
        assert not engine.search_text("SECRET_TOKEN").results
        assert not engine.search_text("TEMPORARY_TOKEN").results
        assert not engine.search_text("OUTSIDE_TOKEN").results

        linked_root = tmp_path / "linked_root"
        try:
            _make_directory_link(linked_root, folder)
        except OSError:
            pytest.skip("root symlink/junction unavailable")
        with pytest.raises(FolderImportError, match="source_root_link_refused"):
            await engine.import_folders([("other", linked_root)])


@pytest.mark.asyncio
async def test_partial_summary_continues_past_corrupt_non_utf8_unknown_and_oversize(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "good.md").write_text("# Good\n\nGOOD_TOKEN.\n", encoding="utf-8")
    (folder / "bad.txt").write_bytes(b"BAD_TOKEN \xff\xfe not utf8")
    (folder / "bad.png").write_bytes(b"not a png")
    (folder / "blank.txt").write_text(" \n\t\n", encoding="utf-8")
    (folder / "unknown.pdf").write_bytes(b"%PDF fake")
    oversize = folder / "oversize.txt"
    with oversize.open("wb") as stream:
        stream.seek(33_554_432)
        stream.write(b"X")
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        result = await engine.import_folders([("fixture", folder)])
        assert result.status == "partial_success"
        assert result.counts["added"] == 1
        assert result.counts["failed"] == 4
        assert result.counts["skipped"] == 1
        assert result.counts["unprocessed"] == 0
        assert result.model_calls == len(provider.calls) == 1
        reasons = {
            item["source_ref"]: (item["status"], item["reason"])
            for item in result.to_dict()["files"]  # type: ignore[index]
        }
        assert reasons["good.md"] == ("added", None)
        assert reasons["bad.txt"] == ("failed", "source_not_utf8")
        assert reasons["bad.png"] == ("failed", "unsupported_image_format")
        assert reasons["blank.txt"] == ("failed", "prepared_evidence_invalid")
        assert reasons["oversize.txt"] == ("failed", "source_too_large")
        assert reasons["unknown.pdf"] == ("skipped", "unsupported_media_type")
        assert engine.search_text("GOOD_TOKEN").results
        kinds = {
            str(row["resource_kind"])
            for row in engine.storage.connection.execute("SELECT resource_kind FROM core_resources")
        }
        assert kinds == {"document"}


@pytest.mark.asyncio
async def test_image_preparation_failure_is_reported_and_other_files_are_admitted(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "good.md").write_text("# Good\n\nGOOD_TOKEN.\n", encoding="utf-8")
    (folder / "bad.png").write_bytes(PNG)
    app = tmp_path / "app"
    app.mkdir()
    provider = FailingImagePreparationProvider()

    with _engine(app, provider) as engine:
        result = await engine.import_folders([("fixture", folder)])
        assert result.status == "partial_success"
        assert result.counts["added"] == 1 and result.counts["failed"] == 1
        reasons = {
            item["source_ref"]: (item["status"], item["reason"], item["model_calls"])
            for item in result.to_dict()["files"]  # type: ignore[index]
        }
        assert reasons["bad.png"] == ("failed", "image_decode_failed", 0)
        assert reasons["good.md"][0] == "added"
        assert engine.search_text("GOOD_TOKEN").results


@pytest.mark.asyncio
async def test_unchanged_fresh_process_replays_zero_provider_calls_and_no_duplicates(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "notes.md").write_text("# Notes\n\nUNCHANGED_TOKEN.\n", encoding="utf-8")
    (folder / "plain.txt").write_text("literal unchanged\n", encoding="utf-8")
    (folder / "scene.png").write_bytes(PNG)
    app = tmp_path / "app"
    app.mkdir()
    command = [
        sys.executable, "-B", "-m", "mdrack", "--root", str(app),
        "folder", "add", f"fixture={folder}", "--provider", "synthetic",
    ]
    first = subprocess.run(command, text=True, capture_output=True, env=os.environ.copy(), timeout=60)
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(first.stdout)["data"]["counts"]["added"] == 3
    before_counts = _catalog_counts(app)

    script = """
import asyncio, json, sys
from pathlib import Path
from mdrack.config.loader import load_config
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.public_api.engine import MDRackEngine

class Counting(SyntheticPreparedProvider):
    calls = 0
    async def encode(self, value, role):
        type(self).calls += 1
        return await super().encode(value, role)

root = Path(sys.argv[1])
folder = Path(sys.argv[2])
provider = Counting()

async def main():
    engine = MDRackEngine(root=root, config=load_config(root=root), prepared_embedding_provider=provider)
    try:
        return await engine.import_folders([("fixture", folder)])
    finally:
        engine.close()

result = asyncio.run(main())
print(json.dumps({"calls": provider.calls, "result": result.to_dict()}, sort_keys=True))
"""
    second = subprocess.run(
        [sys.executable, "-B", "-c", script, str(app), str(folder)],
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        timeout=60,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    payload = json.loads(second.stdout)
    assert payload["calls"] == 0
    assert payload["result"]["status"] == "success"
    assert payload["result"]["model_calls"] == 0
    assert payload["result"]["counts"]["skipped"] == 3
    assert payload["result"]["counts"]["added"] == 0
    assert _catalog_counts(app) == before_counts


def test_cli_engine_parity_and_privacy_on_disposable_roots(tmp_path: Path) -> None:
    content_sentinel = "PRIVATE_CONTENT_SENTINEL"
    folder_a = tmp_path / "PRIVATE_ROOT_SENTINEL_a"
    folder_b = tmp_path / "PRIVATE_ROOT_SENTINEL_b"
    for folder in (folder_a, folder_b):
        folder.mkdir()
        (folder / "notes.md").write_text(f"# Notes\n\n{content_sentinel}.\n", encoding="utf-8")
        (folder / "plain.txt").write_text("literal\n", encoding="utf-8")
        (folder / "scene.png").write_bytes(PNG)
    app_a = tmp_path / "app_a"
    app_b = tmp_path / "app_b"
    app_a.mkdir()
    app_b.mkdir()

    async def engine_result() -> dict[str, object]:
        with _engine(app_a, CountingProvider()) as engine:
            return (await engine.import_folders([("fixture", folder_a)])).to_dict()

    engine_data = asyncio.run(engine_result())
    result = CliRunner().invoke(main, [
        "--root", str(app_b), "folder", "add", f"fixture={folder_b}",
        "--provider", "synthetic",
    ])
    assert result.exit_code == 0, result.output
    cli_data = json.loads(result.output)["data"]
    assert cli_data == engine_data
    assert content_sentinel not in result.output
    assert str(folder_b) not in result.output
    assert content_sentinel not in result.stderr
    assert str(folder_b) not in result.stderr


@pytest.mark.asyncio
async def test_durable_registry_refuses_remap_and_derived_overlap_precedes_store_creation(tmp_path: Path) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a.md").write_text("# A\n\nA_TOKEN.\n", encoding="utf-8")
    (root_b / "b.md").write_text("# B\n\nB_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()

    with _engine(app, CountingProvider()) as engine:
        assert (await engine.import_folders([("alpha", root_a)])).counts["added"] == 1
        with pytest.raises(FolderImportError, match="namespace_root_conflict"):
            await engine.import_folders([("alpha", root_b)])
        assert not engine.search_text("B_TOKEN").results
        assert engine.search_text("A_TOKEN").results
        with pytest.raises(FolderImportError, match="root_namespace_conflict"):
            await engine.import_folders([("beta", root_a)])

    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "inside.md").write_text("# Inside\n\nINSIDE_TOKEN.\n", encoding="utf-8")
    with _engine(inside, CountingProvider()) as engine:
        with pytest.raises(FolderImportError, match="source_overlaps_store"):
            await engine.import_folders([("inside", inside)])
    assert not (inside / ".mdrack").exists()


@pytest.mark.asyncio
async def test_long_txt_is_explicitly_chunked_without_silent_truncation(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    start = "START_CHUNK_SENTINEL"
    end = "END_CHUNK_SENTINEL"
    body = " ".join(f"literal sentence {index}." for index in range(1200))
    text = f"{start}\n\n{body}\n\n{end}\n"
    (folder / "long.txt").write_text(text, encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        result = await engine.import_folders([("fixture", folder)])
        assert result.status == "success"
        item = result.to_dict()["files"][0]  # type: ignore[index]
        assert item["model_calls"] >= 2
        resource_id = str(item["resource_id"])
        units = [
            str(row["text_content"])
            for row in engine.storage.connection.execute(
                "SELECT text_content FROM core_search_units WHERE resource_id=? AND modality='text' ORDER BY ordinal",
                (resource_id,),
            ).fetchall()
        ]
        assert any(start in unit for unit in units)
        assert any(end in unit for unit in units)
        assert engine.search_text(start).results
        assert engine.search_text(end).results


@pytest.mark.asyncio
async def test_model_input_bound_refusal_does_not_truncate_and_other_file_is_admitted(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "too_large.md").write_text("# Too large\n\nMODEL_BOUND_TOKEN.\n", encoding="utf-8")
    (folder / "scene.png").write_bytes(PNG)
    app = tmp_path / "app"
    app.mkdir()
    provider = TinyTextInputProvider()

    with _engine(app, provider) as engine:
        result = await engine.import_folders([("fixture", folder)])
        assert result.status == "partial_success"
        assert result.counts["added"] == 1 and result.counts["failed"] == 1
        reasons = {
            item["source_ref"]: (item["status"], item["reason"], item["model_calls"])
            for item in result.to_dict()["files"]  # type: ignore[index]
        }
        assert reasons["too_large.md"] == ("failed", "embedding_input_too_large", 0)
        assert reasons["scene.png"][0] == "added"
        assert result.model_calls == 1
        assert not engine.search_text("MODEL_BOUND_TOKEN").results



@pytest.mark.asyncio
async def test_windows_junction_is_not_followed_and_junction_root_is_refused(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction policy control")
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "keep.md").write_text("# Keep\n\nKEEP_TOKEN.\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.md").write_text("# Outside\n\nOUTSIDE_TOKEN.\n", encoding="utf-8")
    junction = folder / "junction_outside"
    try:
        _make_junction(junction, outside)
    except OSError:
        pytest.skip("junction unavailable")
    app = tmp_path / "app"
    app.mkdir()

    with _engine(app, CountingProvider()) as engine:
        result = await engine.import_folders([("fixture", folder)])
        reasons = {
            (item["source_ref"], item["status"], item["reason"])
            for item in result.to_dict()["files"]  # type: ignore[index]
        }
        assert ("junction_outside", "skipped", "link_not_followed") in reasons
        assert engine.search_text("KEEP_TOKEN").results
        assert not engine.search_text("OUTSIDE_TOKEN").results

        root_junction = tmp_path / "junction_root"
        try:
            _make_junction(root_junction, folder)
        except OSError:
            pytest.skip("root junction unavailable")
        with pytest.raises(FolderImportError, match="source_root_link_refused"):
            await engine.import_folders([("other", root_junction)])


@pytest.mark.asyncio
async def test_incompatible_text_space_refuses_before_model_writes(tmp_path: Path) -> None:
    first_folder = tmp_path / "first"
    first_folder.mkdir()
    (first_folder / "first.md").write_text("# First\n\nFIRST_TOKEN.\n", encoding="utf-8")
    second_folder = tmp_path / "second"
    second_folder.mkdir()
    (second_folder / "second.md").write_text("# Second\n\nSECOND_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()

    with _engine(app, CountingProvider()) as engine:
        assert (await engine.import_folders([("first", first_folder)])).counts["added"] == 1
        resources_before = _catalog_counts(app)

    other = OtherSpaceProvider()
    with _engine(app, other) as engine:
        with pytest.raises(IncompatibleEmbeddingProfileError):
            await engine.import_folders([("second", second_folder)])
        assert other.calls == []
        assert _catalog_counts(app) == resources_before
        assert not engine.search_text("SECOND_TOKEN").results


@pytest.mark.asyncio
async def test_incompatible_image_space_is_refused_without_mutating_old_collection(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "scene.png").write_bytes(PNG)
    app = tmp_path / "app"
    app.mkdir()

    with _engine(app, CountingProvider()) as engine:
        first = await engine.import_folders([("fixture", folder)])
        assert first.counts["added"] == 1
        resource_id = str(first.to_dict()["files"][0]["resource_id"])  # type: ignore[index]
        before = _catalog_counts(app)

    other = OtherSpaceProvider()
    with _engine(app, other) as engine:
        refused = await engine.import_folders([("fixture", folder)])
        assert refused.status == "failed"
        assert refused.counts["failed"] == 1
        assert refused.files[0].reason == "incompatible_embedding_space"
        assert other.calls == []
        assert _catalog_counts(app) == before

    with _engine(app, CountingProvider()) as reopened:
        inspected = reopened.inspect_resource(resource_id)
        assert inspected.media_type == "image/png"
        assert inspected.counts["vectors"] == 1
        assert reopened.storage.connection.execute(
            "SELECT COUNT(*) FROM core_unit_embeddings WHERE space_id=?",
            ("synthetic-native-v1",),
        ).fetchone()[0] == 1
