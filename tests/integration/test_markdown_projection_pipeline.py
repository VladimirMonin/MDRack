"""SQLite indexing oracle for prose-only Markdown image projection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.config.models import MDRackConfig, PathsConfig


def test_markdown_scan_never_inspects_referenced_files_and_stores_only_prose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    referenced = root / "do-not-touch.png"
    source = (
        "# Projection\n\n"
        "Context before ![Architecture](do-not-touch.png \"private title\") after.\n\n"
        "Reference before ![Reference architecture][diagram] after.\n\n"
        '[diagram]: do-not-touch.png "reference title"\n\n'
        "<img src=do-not-touch.png alt=HTML-architecture width=320>\n\n"
        '<img src="do-not-touch.png" alt="Quoted > architecture">\n\n'
        "<img src='do-not-touch.png' alt='Single > architecture'>\n\n"
        'Before <img src="do-not-touch.png" alt="Inline-double > prose"> after.\n\n'
        "Before <img src='do-not-touch.png' alt='Inline-single > prose'> after.\n\n"
        "Before <img data-alt=PrivateMetadata aria-alt='Private aria' "
        "prefixalt=PrivateSuffix data-src=do-not-touch.png> after.\n\n"
        "Before <IMG DATA-ALT=PrivateMetadata ALT=PublicBoundary "
        "DATA-SRC=do-not-touch.png> after.\n\n"
        'Straße <IMG src=context-private/unicode.png alt="Öffentlich"> τέλος.\n\n'
        "Before `<img src=context-private/code.png alt=CodeSecret>` after.\n\n"
        r"Before \<img src=context-private/escaped.png alt=EscapedSecret> after."
        "\n\n"
        "Before <!-- <img src=context-private/comment.png alt=CommentSecret> --> after.\n\n"
        'Before <a title="<img src=context-private/nested.png alt=NestedSecret>">link</a> after.\n\n'
        '<script>const example = "<img src=context-private/raw.png alt=RawSecret>";</script>\n\n'
        "```html\n<img src=context-private/fence.png alt=FenceSecret>\n```\n\n"
        "    <img src=context-private/indent.png alt=IndentSecret>\n\n"
        "![[missing.png|Alias text]]\n"
    )
    note.write_text(source, encoding="utf-8", newline="")
    referenced.write_bytes(b"sentinel-image-bytes")
    note_before = note.read_bytes()
    referenced_before = referenced.read_bytes()
    touched: list[str] = []

    def guard(method_name: str, original: Any):
        def wrapped(path: Path, *args: Any, **kwargs: Any):
            if path.suffix.casefold() == ".png":
                touched.append(f"{method_name}:{path.name}")
                raise AssertionError(f"referenced file was touched through {method_name}")
            return original(path, *args, **kwargs)

        return wrapped

    for method_name in ("resolve", "stat", "open", "read_bytes", "is_file"):
        original = getattr(Path, method_name)
        monkeypatch.setattr(Path, method_name, guard(method_name, original))

    config = MDRackConfig(paths=PathsConfig(root=".", store=str(tmp_path / ".store")))
    storage = create_sqlite_index_storage(root, config)
    service = IndexingService(root, config, storage, provider=None)
    first = service.scan(force_reindex=True)
    first_ids = [
        str(row[0])
        for row in storage.connection.execute(
            "SELECT logical_id FROM chunks ORDER BY chunk_index"
        ).fetchall()
    ]
    second = service.scan(force_reindex=True)
    second_ids = [
        str(row[0])
        for row in storage.connection.execute(
            "SELECT logical_id FROM chunks ORDER BY chunk_index"
        ).fetchall()
    ]
    search_architecture = storage.search_text("Architecture", limit=5)
    search_reference = storage.search_text("Reference architecture", limit=5)
    search_html = storage.search_text("HTML-architecture", limit=5)
    search_quoted = storage.search_text('"Quoted > architecture"', limit=5)
    search_single = storage.search_text('"Single > architecture"', limit=5)
    search_inline_double = storage.search_text('"Inline-double > prose"', limit=5)
    search_inline_single = storage.search_text('"Inline-single > prose"', limit=5)
    search_prefixed = storage.search_text("PrivateMetadata", limit=5)
    search_exact_boundary = storage.search_text("PublicBoundary", limit=5)
    search_unicode = storage.search_text("Öffentlich", limit=5)
    search_alias = storage.search_text("Alias", limit=5)
    stored_text = "\n".join(
        str(row[0])
        for row in storage.connection.execute("SELECT content FROM chunks ORDER BY chunk_index")
    )
    diagnostics = storage.connection.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]
    legacy_rows = {
        table: storage.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("assets", "asset_references", "asset_descriptions")
    }
    service.close()

    assert first.status == second.status == "success"
    assert first_ids == second_ids
    assert len(first_ids) == len(set(first_ids))
    assert search_architecture.total_count == 1
    assert search_reference.total_count == 1
    assert search_html.total_count == 1
    assert search_quoted.total_count == 1
    assert search_single.total_count == 1
    assert search_inline_double.total_count == 1
    assert search_inline_single.total_count == 1
    assert search_prefixed.total_count == 0
    assert search_exact_boundary.total_count == 1
    assert search_unicode.total_count == 1
    assert search_alias.total_count == 1
    assert stored_text.count("Architecture") == 1
    assert stored_text.count("Reference architecture") == 1
    assert stored_text.count("HTML-architecture") == 1
    assert stored_text.count("Quoted > architecture") == 1
    assert stored_text.count("Single > architecture") == 1
    assert stored_text.count("Inline-double > prose") == 1
    assert stored_text.count("Inline-single > prose") == 1
    assert stored_text.count("PublicBoundary") == 1
    assert stored_text.count("Öffentlich") == 1
    assert stored_text.count("Alias text") == 1
    for literal_context in (
        "`<img src=context-private/code.png alt=CodeSecret>`",
        r"\<img src=context-private/escaped.png alt=EscapedSecret>",
        "<!-- <img src=context-private/comment.png alt=CommentSecret> -->",
        '<a title="<img src=context-private/nested.png alt=NestedSecret>">',
        "<img src=context-private/fence.png alt=FenceSecret>",
    ):
        assert literal_context in stored_text
    for forbidden in (
        "do-not-touch.png",
        "missing.png",
        "private title",
        "reference title",
        "diagram",
        "width=",
        "PrivateMetadata",
        "Private aria",
        "PrivateSuffix",
        "data-src=",
    ):
        assert forbidden not in stored_text
    assert diagnostics == 0
    assert legacy_rows == {"assets": 0, "asset_references": 0, "asset_descriptions": 0}
    assert touched == []
    assert note.read_bytes() == note_before
    assert hashlib.sha256(referenced_before).hexdigest() == hashlib.sha256(
        b"sentinel-image-bytes"
    ).hexdigest()
