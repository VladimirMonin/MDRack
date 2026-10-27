"""Selective P06 lifecycle through the real engine, local SQLite and filesystem.

The roots, sources and provider vectors are explicit synthetic fixtures. These
tests are local orchestration evidence, not a native/model, installed-release,
real-corpus or retrieval-quality claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mdrack.application import folder_import
from mdrack.application.folder_import import FolderImportError
from mdrack.application.media_search import MediaSearchRequest
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import EncodingRole, TextInput
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.public_api.engine import MDRackEngine
from mdrack_core import SearchScope
from tests.integration.test_native_image_v2 import PNG


class CountingProvider(SyntheticPreparedProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, EncodingRole]] = []

    async def encode(self, value: object, role: EncodingRole) -> object:
        self.calls.append((getattr(value, "modality", "unknown"), role))
        return await super().encode(value, role)  # type: ignore[arg-type]


def _engine(root: Path, provider: CountingProvider) -> MDRackEngine:
    return MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=provider)


def _state(root: Path) -> dict[str, dict[str, dict[str, object]]]:
    raw = json.loads((root / ".mdrack" / "folder-lifecycle.json").read_text(encoding="utf-8"))
    return {
        str(group["namespace"]): {
            str(item["source_ref"]): item
            for item in group["files"]  # type: ignore[index]
        }
        for group in raw["roots"]
    }


def _source_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _counts(root: Path) -> dict[str, int]:
    with sqlite3.connect(root / ".mdrack" / "catalog.sqlite3") as connection:
        return {
            str(table): int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "core_resources",
                "core_representations",
                "core_search_units",
                "core_unit_embeddings",
            )
        }


def _scope_counts(root: Path) -> dict[str, int]:
    with sqlite3.connect(root / ".mdrack" / "catalog.sqlite3") as connection:
        return {
            str(table): int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "core_resources",
                "core_representations",
                "core_search_units",
                "core_search_units_fts",
                "core_unit_embeddings",
            )
        }


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction policy control")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def _release_junction(link: Path | None) -> None:
    if link is None:
        return
    if link.exists() or link.is_junction():
        link.rmdir()


def _assert_consistent(root: Path) -> None:
    with sqlite3.connect(root / ".mdrack" / "catalog.sqlite3") as connection:
        units = int(connection.execute("SELECT COUNT(*) FROM core_search_units").fetchone()[0])
        fts = int(connection.execute("SELECT COUNT(*) FROM core_search_units_fts").fetchone()[0])
        textual = int(connection.execute(
            "SELECT COUNT(*) FROM core_search_units "
            "WHERE text_content IS NOT NULL AND trim(text_content) <> ''"
        ).fetchone()[0])
        orphan_vectors = int(connection.execute(
            "SELECT COUNT(*) FROM core_unit_embeddings e "
            "LEFT JOIN core_search_units u ON u.unit_id=e.unit_id WHERE u.unit_id IS NULL"
        ).fetchone()[0])
        assert fts == textual
        assert orphan_vectors == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert units >= textual


@pytest.mark.asyncio
async def test_changed_text_image_and_added_file_refresh_only_affected_graphs(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "SYNTHETIC_FIXTURE_selected"
    folder.mkdir()
    (folder / "a.md").write_text("# A\n\nOLD_TEXT_TOKEN.\n", encoding="utf-8")
    (folder / "b.md").write_text("# B\n\nSTABLE_TEXT_TOKEN.\n", encoding="utf-8")
    (folder / "scene.png").write_bytes(PNG)
    (folder / "stable.png").write_bytes(PNG)
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        first = await engine.import_folders([("fixture", folder)])
        assert first.status == "success"
        assert first.counts["added"] == 4
        assert first.model_calls == len(provider.calls) == 4
        first_files = {str(item["source_ref"]): item for item in first.to_dict()["files"]}  # type: ignore[index]
        a_id = str(first_files["a.md"]["resource_id"])
        b_id = str(first_files["b.md"]["resource_id"])
        image_id = str(first_files["scene.png"]["resource_id"])
        stable_image_id = str(first_files["stable.png"]["resource_id"])
        assert len({a_id, b_id, image_id, stable_image_id}) == 4
        assert image_id != stable_image_id
        b_before = engine.storage.resource_store.read_snapshot(b_id)
        a_before = engine.storage.resource_store.read_snapshot(a_id)
        image_before = engine.storage.resource_store.read_snapshot(image_id)
        stable_image_before = engine.storage.resource_store.read_snapshot(stable_image_id)
        state_before = _state(app)
        assert state_before["fixture"]["a.md"]["availability"] == "available"
        assert _state(app)["fixture"]["scene.png"]["source_hash"] == image_before.batch.resource.content_hash
        initial_counts = _counts(app)
        assert initial_counts["core_resources"] == 4
        assert initial_counts["core_search_units"] == initial_counts["core_unit_embeddings"]

        (folder / "a.md").write_text("# A\n\nNEW_TEXT_TOKEN.\n", encoding="utf-8")
        (folder / "scene.png").write_bytes(PNG + b"changed-before")
        (folder / "new.txt").write_text("literal NEW_FILE_TOKEN\n", encoding="utf-8")
        after_change_hashes = _source_hashes(folder)
        provider.calls.clear()

        second = await engine.import_folders([("fixture", folder)])
        assert second.status == "success"
        assert second.counts["added"] == 1
        assert second.counts["updated"] == 2
        assert second.counts["skipped"] == 2
        assert second.model_calls == len(provider.calls) == 3
        assert _source_hashes(folder) == after_change_hashes

        a_after = engine.storage.resource_store.read_snapshot(a_id)
        b_after = engine.storage.resource_store.read_snapshot(b_id)
        image_after = engine.storage.resource_store.read_snapshot(image_id)
        stable_image_after = engine.storage.resource_store.read_snapshot(stable_image_id)
        assert a_after.batch is not None and a_before.batch is not None
        assert b_after.batch is not None
        assert image_after.batch is not None and image_before.batch is not None
        assert a_after.batch.resource.resource_id == a_before.batch.resource.resource_id
        assert a_after.batch.resource.content_hash != a_before.batch.resource.content_hash
        assert b_after == b_before
        assert image_after.batch.resource.resource_id == image_before.batch.resource.resource_id
        assert image_after.batch.resource.content_hash != image_before.batch.resource.content_hash
        assert image_after.batch.vectors != image_before.batch.vectors
        assert stable_image_after.batch == stable_image_before.batch

        assert not engine.search_text("OLD_TEXT_TOKEN").results
        assert len(engine.search_text("NEW_TEXT_TOKEN").results) == 1
        assert len(engine.search_text("STABLE_TEXT_TOKEN").results) == 1
        assert len(engine.search_text("NEW_FILE_TOKEN").results) == 1
        image_search = await engine.search_media(MediaSearchRequest(
            TextInput("scene", "query"),
            SearchScope(modalities=("image",)),
            mode="semantic",
        ))
        assert {
            str(item["evidence"][0]["locator"]["payload"]["source_ref"])
            for item in image_search.results
        } == {"scene.png", "stable.png"}
        final_counts = _counts(app)
        assert final_counts["core_resources"] == 5
        assert final_counts["core_search_units"] == final_counts["core_unit_embeddings"]
        state_after = _state(app)
        assert state_after["fixture"]["a.md"]["source_hash"] == a_after.batch.resource.content_hash
        assert state_after["fixture"]["b.md"]["source_hash"] == b_before.batch.resource.content_hash
        assert state_after["fixture"]["scene.png"]["source_hash"] == image_after.batch.resource.content_hash
        assert state_after["fixture"]["stable.png"]["source_hash"] == stable_image_before.batch.resource.content_hash
        assert state_after["fixture"]["new.txt"]["availability"] == "available"
        _assert_consistent(app)

    reopened_provider = CountingProvider()
    before_reopen_counts = _counts(app)
    with _engine(app, reopened_provider) as reopened:
        third = await reopened.import_folders([("fixture", folder)])
        assert third.status == "success"
        assert third.counts["skipped"] == 5
        assert third.counts["added"] == 0 and third.counts["updated"] == 0
        assert third.model_calls == len(reopened_provider.calls) == 0
        assert len(reopened.search_text("NEW_TEXT_TOKEN").results) == 1
        assert len(reopened.search_text("NEW_FILE_TOKEN").results) == 1
        assert reopened.storage.resource_store.read_snapshot(b_id) == b_before
        assert reopened.storage.resource_store.read_snapshot(stable_image_id).batch == stable_image_before.batch
        _assert_consistent(app)
    assert _counts(app) == before_reopen_counts


@pytest.mark.asyncio
async def test_rename_and_equal_bytes_are_missing_old_new_source_without_duplicates(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    shared = "# Shared\n\nEQUAL_BYTE_TOKEN.\n"
    (root_a / "shared.md").write_text(shared, encoding="utf-8")
    (root_b / "shared.md").write_text(shared, encoding="utf-8")
    (root_a / "old.md").write_text("# Move\n\nMOVE_TOKEN.\n", encoding="utf-8")
    (root_b / "stable.md").write_text("# Stable\n\nSTABLE_B_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        first = await engine.import_folders([("root_a", root_a), ("root_b", root_b)])
        assert first.counts["added"] == 4
        first_files = {
            (str(item["namespace"]), str(item["source_ref"])): item
            for item in first.to_dict()["files"]  # type: ignore[index]
        }
        a_shared = str(first_files[("root_a", "shared.md")]["resource_id"])
        b_shared = str(first_files[("root_b", "shared.md")]["resource_id"])
        old_id = str(first_files[("root_a", "old.md")]["resource_id"])
        b_stable = str(first_files[("root_b", "stable.md")]["resource_id"])
        assert a_shared != b_shared
        assert len(engine.search_text("EQUAL_BYTE_TOKEN").results) == 2
        b_shared_before = engine.storage.resource_store.read_snapshot(b_shared)
        b_stable_before = engine.storage.resource_store.read_snapshot(b_stable)

        (root_a / "shared.md").unlink()
        (root_a / "old.md").replace(root_a / "new.md")
        provider.calls.clear()

        second = await engine.import_folders([("root_a", root_a), ("root_b", root_b)])
        assert second.status == "success"
        assert second.counts["unavailable"] == 2
        assert second.counts["added"] == 1
        assert second.counts["skipped"] == 2
        assert second.counts["failed"] == 0
        assert second.model_calls == len(provider.calls) == 1
        second_files = {
            (str(item["namespace"]), str(item["source_ref"])): item
            for item in second.to_dict()["files"]  # type: ignore[index]
        }
        assert second_files[("root_a", "shared.md")]["status"] == "unavailable"
        assert second_files[("root_a", "shared.md")]["reason"] == "source_missing"
        assert second_files[("root_a", "old.md")]["status"] == "unavailable"
        assert second_files[("root_a", "old.md")]["reason"] == "source_missing"
        new_id = str(second_files[("root_a", "new.md")]["resource_id"])
        assert new_id != old_id

        assert engine.storage.resource_store.read_snapshot(old_id).state.status == (
            "live_without_projections"
        )
        assert engine.storage.resource_store.read_snapshot(old_id).batch is None
        assert engine.storage.resource_store.read_snapshot(a_shared).batch is None
        assert engine.storage.resource_store.read_snapshot(b_shared) == b_shared_before
        assert engine.storage.resource_store.read_snapshot(b_stable) == b_stable_before
        move_results = engine.search_text("MOVE_TOKEN").results
        assert [(item.source_locator.root_id, item.source_locator.relative_path) for item in move_results] == [
            ("root_a", "new.md")
        ]
        equal_results = engine.search_text("EQUAL_BYTE_TOKEN").results
        assert [(item.source_locator.root_id, item.source_locator.relative_path) for item in equal_results] == [
            ("root_b", "shared.md")
        ]
        assert _counts(app)["core_resources"] == 3
        state = _state(app)
        assert state["root_a"]["shared.md"]["availability"] == "unavailable"
        assert state["root_a"]["old.md"]["availability"] == "unavailable"
        assert state["root_a"]["old.md"]["source_hash"] == state["root_a"]["new.md"]["source_hash"]
        assert state["root_a"]["new.md"]["availability"] == "available"
        assert state["root_b"]["shared.md"]["availability"] == "available"
        _assert_consistent(app)

        provider.calls.clear()
        third = await engine.import_folders([("root_a", root_a), ("root_b", root_b)])
        assert third.status == "success"
        assert third.counts["unavailable"] == 2
        assert third.counts["skipped"] == 3
        assert third.model_calls == len(provider.calls) == 0
        assert len(engine.search_text("MOVE_TOKEN").results) == 1
        assert len(engine.search_text("EQUAL_BYTE_TOKEN").results) == 1
        assert _counts(app)["core_resources"] == 3
        _assert_consistent(app)


@pytest.mark.asyncio
async def test_incomplete_enumeration_and_missing_root_never_delete_unseen_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "keep.md").write_text("# Keep\n\nKEEP_A_TOKEN.\n", encoding="utf-8")
    (root_b / "live.md").write_text("# Live\n\nLIVE_B_TOKEN.\n", encoding="utf-8")
    (root_b / "locked").mkdir()
    (root_b / "locked" / "inside.md").write_text("# Inside\n\nINSIDE_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        first = await engine.import_folders([("root_a", root_a), ("root_b", root_b)])
        assert first.counts["added"] == 3
        assert len(engine.search_text("INSIDE_TOKEN").results) == 1

        (root_a / "keep.md").unlink()
        (root_b / "locked" / "inside.md").unlink()
        original_scandir = os.scandir

        def failing_scandir(path: object) -> object:
            if Path(path).name == "locked":  # type: ignore[arg-type]
                raise PermissionError("synthetic unavailable subtree")
            return original_scandir(path)

        monkeypatch.setattr(folder_import.os, "scandir", failing_scandir)
        provider.calls.clear()
        second = await engine.import_folders([("root_a", root_a), ("root_b", root_b)])
        assert second.status == "partial_success"
        assert second.counts["unavailable"] == 1
        assert second.counts["unprocessed"] == 1
        assert second.counts["skipped"] == 1
        assert second.model_calls == len(provider.calls) == 0
        assert not engine.search_text("KEEP_A_TOKEN").results
        # The subtree was unreadable, so its known file must not be declared missing.
        assert len(engine.search_text("INSIDE_TOKEN").results) == 1
        state = _state(app)
        assert state["root_a"]["keep.md"]["availability"] == "unavailable"
        assert state["root_b"]["locked/inside.md"]["availability"] == "available"
        _assert_consistent(app)

        monkeypatch.undo()
        missing_root = tmp_path / "root_b_missing"
        root_b.rename(missing_root)
        with pytest.raises(FolderImportError, match="source_root_unavailable"):
            await engine.import_folders([("root_a", root_a), ("root_b", root_b)])
        assert _state(app) == state
        assert len(engine.search_text("INSIDE_TOKEN").results) == 1

    with _engine(app, CountingProvider()) as reopened:
        assert not reopened.search_text("KEEP_A_TOKEN").results
        assert len(reopened.search_text("INSIDE_TOKEN").results) == 1
        _assert_consistent(app)


@pytest.mark.asyncio
async def test_failed_replacement_preserves_previous_valid_graph_and_checkpoint(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "SYNTHETIC_FIXTURE_selected"
    folder.mkdir()
    (folder / "good.md").write_text("# Good\n\nOLD_VALID_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        first = await engine.import_folders([("fixture", folder)])
        assert first.counts["added"] == 1
        resource_id = str(first.to_dict()["files"][0]["resource_id"])  # type: ignore[index]
        old_snapshot = engine.storage.resource_store.read_snapshot(resource_id)
        old_state = _state(app)["fixture"]["good.md"]
        counts_before = _counts(app)

        (folder / "good.md").write_bytes(b"# Broken\n\nNEW_INVALID_TOKEN \xff\xfe")
        provider.calls.clear()
        failed = await engine.import_folders([("fixture", folder)])
        assert failed.status == "failed"
        assert failed.counts["failed"] == 1
        assert failed.counts["updated"] == 0
        assert failed.model_calls == len(provider.calls) == 0
        assert engine.storage.resource_store.read_snapshot(resource_id) == old_snapshot
        assert _counts(app) == counts_before
        assert len(engine.search_text("OLD_VALID_TOKEN").results) == 1
        assert not engine.search_text("NEW_INVALID_TOKEN").results
        assert _state(app)["fixture"]["good.md"] == old_state
        _assert_consistent(app)

        (folder / "good.md").write_text("# Good\n\nNEW_VALID_TOKEN.\n", encoding="utf-8")
        provider.calls.clear()
        repaired = await engine.import_folders([("fixture", folder)])
        assert repaired.status == "success"
        assert repaired.counts["updated"] == 1
        assert repaired.model_calls == len(provider.calls) == 1
        repaired_snapshot = engine.storage.resource_store.read_snapshot(resource_id)
        assert repaired_snapshot.batch is not None
        assert repaired_snapshot.batch.resource.content_hash != old_snapshot.batch.resource.content_hash
        assert not engine.search_text("OLD_VALID_TOKEN").results
        assert len(engine.search_text("NEW_VALID_TOKEN").results) == 1
        assert _state(app)["fixture"]["good.md"]["source_hash"] != old_state["source_hash"]
        assert _counts(app)["core_resources"] == 1
        _assert_consistent(app)


@pytest.mark.asyncio
async def test_interruption_after_completed_file_resumes_without_reencoding_it(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "SYNTHETIC_FIXTURE_selected"
    folder.mkdir()
    for name, token in (
        ("a.md", "INTERRUPT_A_TOKEN"),
        ("b.md", "INTERRUPT_B_TOKEN"),
        ("c.md", "INTERRUPT_C_TOKEN"),
    ):
        (folder / name).write_text(f"# {name}\n\n{token}.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()

    child_script = """
import asyncio
import os
import sys
from pathlib import Path

from mdrack.config.models import MDRackConfig
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.public_api.engine import MDRackEngine


class KillOnSecondEncode(SyntheticPreparedProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def encode(self, value, role):
        self.calls += 1
        if self.calls >= 2:
            os._exit(23)
        return await super().encode(value, role)


async def main():
    engine = MDRackEngine(
        root=Path(sys.argv[1]),
        config=MDRackConfig(),
        prepared_embedding_provider=KillOnSecondEncode(),
    )
    try:
        await engine.import_folders([("fixture", Path(sys.argv[2]))])
    finally:
        engine.close()


asyncio.run(main())
"""
    child = subprocess.run(
        [sys.executable, "-B", "-c", child_script, str(app), str(folder)],
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        timeout=120,
    )
    assert child.returncode == 23, child.stdout + child.stderr
    assert _counts(app)["core_resources"] == 1
    interrupted_state = _state(app)
    assert list(interrupted_state["fixture"]) == ["a.md"]
    assert interrupted_state["fixture"]["a.md"]["availability"] == "available"

    provider = CountingProvider()
    with _engine(app, provider) as engine:
        first_files = [
            row for row in engine.storage.connection.execute(
                "SELECT resource_id FROM core_resources ORDER BY resource_id"
            ).fetchall()
        ]
        assert len(first_files) == 1
        a_id = str(first_files[0]["resource_id"])
        a_state = interrupted_state["fixture"]["a.md"]
        a_before = engine.storage.resource_store.read_snapshot(a_id)

        resumed = await engine.import_folders([("fixture", folder)])
        assert resumed.status == "success"
        assert resumed.counts["updated"] == 0
        assert resumed.counts["added"] == 2
        assert resumed.counts["skipped"] == 1
        assert resumed.model_calls == len(provider.calls) == 2
        assert engine.storage.resource_store.read_snapshot(a_id) == a_before
        assert _state(app)["fixture"]["a.md"]["source_hash"] == a_state["source_hash"]
        for token in ("INTERRUPT_A_TOKEN", "INTERRUPT_B_TOKEN", "INTERRUPT_C_TOKEN"):
            assert len(engine.search_text(token).results) == 1
        resumed_counts = _counts(app)
        assert resumed_counts["core_resources"] == 3
        assert resumed_counts["core_search_units"] == resumed_counts["core_unit_embeddings"]
        _assert_consistent(app)


@pytest.mark.asyncio
async def test_linked_subtree_is_not_missing_authority_and_restore_reuses_work(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "SYNTHETIC_FIXTURE_selected"
    nested = selected / "nested"
    nested.mkdir(parents=True)
    original = nested / "inside.md"
    original.write_text("# Original\n\nUNSEEN_SCOPE_TOKEN.\n", encoding="utf-8")
    witness = hashlib.sha256(original.read_bytes()).hexdigest()
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        first = await engine.import_folders([("fixture", selected)])
        assert first.status == "success"
        assert first.counts["added"] == 1
        resource_id = str(first.to_dict()["files"][0]["resource_id"])  # type: ignore[index]
        old_snapshot = engine.storage.resource_store.read_snapshot(resource_id)
        assert old_snapshot.batch is not None
        unit_id = old_snapshot.batch.units[0].unit_id
        before_counts = _scope_counts(app)
        before_state = _state(app)["fixture"]["nested/inside.md"]
        assert engine.search_text("UNSEEN_SCOPE_TOKEN").results

    held = tmp_path / "held_originals"
    nested.rename(held)
    link: Path | None = selected / "nested"
    try:
        _make_junction(link, held)
        assert link.is_junction() and link.resolve() == held.resolve()
        assert _source_hashes(held) == {"inside.md": witness}

        refresh_provider = CountingProvider()
        with _engine(app, refresh_provider) as engine:
            second = await engine.import_folders([("fixture", selected)])
            assert second.counts["unavailable"] == 0
            assert second.counts["skipped"] == 1
            assert second.model_calls == len(refresh_provider.calls) == 0
            files = {str(item["source_ref"]): item for item in second.to_dict()["files"]}  # type: ignore[index]
            assert files["nested"]["status"] == "skipped"
            assert files["nested"]["reason"] == "link_not_followed"
            assert "nested/inside.md" not in files
            assert engine.storage.resource_store.read_snapshot(resource_id) == old_snapshot
            assert engine.read_unit(unit_id) is not None
            assert len(engine.search_text("UNSEEN_SCOPE_TOKEN").results) == 1
            assert _scope_counts(app) == before_counts
            assert _state(app)["fixture"]["nested/inside.md"] == before_state
            _assert_consistent(app)

        reopen_script = """
import json
import sys
from pathlib import Path
from mdrack.config.models import MDRackConfig
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.public_api.engine import MDRackEngine

root = Path(sys.argv[1])
resource_id = sys.argv[2]
unit_id = sys.argv[3]
engine = MDRackEngine(root=root, config=MDRackConfig(),
                      prepared_embedding_provider=SyntheticPreparedProvider())
try:
    snapshot = engine.storage.resource_store.read_snapshot(resource_id)
    unit = engine.read_unit(unit_id)
    found = engine.search_text("UNSEEN_SCOPE_TOKEN")
    print(json.dumps({
        "publication_status": snapshot.state.status,
        "graph_present": snapshot.batch is not None,
        "unit_present": unit is not None,
        "search_count": len(found.results),
    }, sort_keys=True))
finally:
    engine.close()
"""
        reopened = subprocess.run(
            [sys.executable, "-B", "-c", reopen_script, str(app), resource_id, unit_id],
            text=True,
            capture_output=True,
            env=os.environ.copy(),
            timeout=120,
        )
        assert reopened.returncode == 0, reopened.stdout + reopened.stderr
        assert json.loads(reopened.stdout) == {
            "graph_present": True,
            "publication_status": "published",
            "search_count": 1,
            "unit_present": True,
        }
    finally:
        _release_junction(link)
        if held.exists():
            held.rename(nested)

    recovery_provider = CountingProvider()
    with _engine(app, recovery_provider) as engine:
        restored = await engine.import_folders([("fixture", selected)])
        assert restored.status == "success"
        assert restored.counts["skipped"] == 1
        assert restored.counts["added"] == 0 and restored.counts["updated"] == 0
        assert restored.model_calls == len(recovery_provider.calls) == 0
        assert engine.storage.resource_store.read_snapshot(resource_id) == old_snapshot
        assert len(engine.search_text("UNSEEN_SCOPE_TOKEN").results) == 1
        assert _source_hashes(selected) == {"nested/inside.md": witness}
        assert _state(app)["fixture"]["nested/inside.md"] == before_state
        assert _scope_counts(app) == before_counts
        _assert_consistent(app)


@pytest.mark.asyncio
async def test_blocked_link_prefix_does_not_suppress_sibling_true_missing(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "SYNTHETIC_FIXTURE_selected"
    nested = selected / "nested"
    nested.mkdir(parents=True)
    inside = nested / "inside.md"
    inside.write_text("# Original\n\nBLOCKED_SCOPE_TOKEN.\n", encoding="utf-8")
    gone = selected / "gone.md"
    gone.write_text("# Gone\n\nSIBLING_MISSING_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    with _engine(app, CountingProvider()) as engine:
        first = await engine.import_folders([("fixture", selected)])
        assert first.counts["added"] == 2
        by_ref = {str(item["source_ref"]): item for item in first.to_dict()["files"]}  # type: ignore[index]
        nested_id = str(by_ref["nested/inside.md"]["resource_id"])
        gone_id = str(by_ref["gone.md"]["resource_id"])
        nested_snapshot = engine.storage.resource_store.read_snapshot(nested_id)

    held = tmp_path / "held_originals"
    nested.rename(held)
    gone.unlink()
    link: Path | None = selected / "nested"
    try:
        _make_junction(link, held)
        provider = CountingProvider()
        with _engine(app, provider) as engine:
            refreshed = await engine.import_folders([("fixture", selected)])
            assert refreshed.counts["unavailable"] == 1
            assert refreshed.counts["skipped"] == 1
            assert refreshed.model_calls == len(provider.calls) == 0
            files = {str(item["source_ref"]): item for item in refreshed.to_dict()["files"]}  # type: ignore[index]
            assert files["gone.md"]["status"] == "unavailable"
            assert files["nested"]["reason"] == "link_not_followed"
            assert "nested/inside.md" not in files
            assert engine.storage.resource_store.read_snapshot(nested_id) == nested_snapshot
            assert engine.storage.resource_store.read_snapshot(gone_id).batch is None
            assert len(engine.search_text("BLOCKED_SCOPE_TOKEN").results) == 1
            assert not engine.search_text("SIBLING_MISSING_TOKEN").results
            assert _counts(app)["core_resources"] == 1
            _assert_consistent(app)
    finally:
        _release_junction(link)
        if held.exists():
            held.rename(nested)


@pytest.mark.asyncio
async def test_excluded_history_is_preserved_while_selected_true_missing_reconciles(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "SYNTHETIC_FIXTURE_selected"
    nested = selected / "nested"
    nested.mkdir(parents=True)
    inside = nested / "inside.md"
    inside.write_text("# Original\n\nEXCLUDED_SCOPE_TOKEN.\n", encoding="utf-8")
    witness = hashlib.sha256(inside.read_bytes()).hexdigest()
    other = selected / "other.md"
    other.write_text("# Other\n\nTRUE_MISSING_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    provider = CountingProvider()

    with _engine(app, provider) as engine:
        first = await engine.import_folders([("fixture", selected)])
        assert first.counts["added"] == 2
        by_ref = {str(item["source_ref"]): item for item in first.to_dict()["files"]}  # type: ignore[index]
        nested_id = str(by_ref["nested/inside.md"]["resource_id"])
        other_id = str(by_ref["other.md"]["resource_id"])
        nested_snapshot = engine.storage.resource_store.read_snapshot(nested_id)
        assert nested_snapshot.batch is not None
        nested_unit = nested_snapshot.batch.units[0].unit_id
        before_nested_state = _state(app)["fixture"]["nested/inside.md"]
        before_counts = _scope_counts(app)

    held = tmp_path / "held_originals"
    held.mkdir()
    inside.rename(held / "inside.md")
    other.unlink()
    refresh_provider = CountingProvider()
    with _engine(app, refresh_provider) as engine:
        second = await engine.import_folders(
            [("fixture", selected)], exclude=("nested/**",),
        )
        assert second.status == "success"
        assert second.counts["unavailable"] == 1
        assert second.counts["excluded"] == 0
        assert second.counts["added"] == 0 and second.counts["updated"] == 0
        assert second.model_calls == len(refresh_provider.calls) == 0
        files = {str(item["source_ref"]): item for item in second.to_dict()["files"]}  # type: ignore[index]
        assert files["other.md"]["status"] == "unavailable"
        assert files["other.md"]["reason"] == "source_missing"
        assert "nested/inside.md" not in files
        assert engine.storage.resource_store.read_snapshot(other_id).batch is None
        assert engine.storage.resource_store.read_snapshot(nested_id) == nested_snapshot
        assert engine.read_unit(nested_unit) is not None
        assert len(engine.search_text("EXCLUDED_SCOPE_TOKEN").results) == 1
        assert not engine.search_text("TRUE_MISSING_TOKEN").results
        state = _state(app)
        assert state["fixture"]["nested/inside.md"] == before_nested_state
        assert state["fixture"]["other.md"]["availability"] == "unavailable"
        assert _counts(app)["core_resources"] == 1
        assert _scope_counts(app)["core_resources"] == before_counts["core_resources"] - 1
        assert _source_hashes(selected) == {}
        _assert_consistent(app)

    (held / "inside.md").rename(inside)
    recovery_provider = CountingProvider()
    with _engine(app, recovery_provider) as engine:
        restored = await engine.import_folders([("fixture", selected)])
        assert restored.status == "success"
        assert restored.counts["skipped"] == 1
        assert restored.counts["unavailable"] == 1
        assert restored.model_calls == len(recovery_provider.calls) == 0
        assert engine.storage.resource_store.read_snapshot(nested_id) == nested_snapshot
        assert len(engine.search_text("EXCLUDED_SCOPE_TOKEN").results) == 1
        assert _source_hashes(selected) == {"nested/inside.md": witness}
        _assert_consistent(app)
