"""Offline image asset/reference contracts."""

from __future__ import annotations

from pathlib import Path

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.assets import build_asset_graph
from mdrack.application.chunking import StructuralChunker
from mdrack.domain.blocks import BlockType


def _parse(content: str, *, relative_path: str = "notes/example.md"):
    return MarkdownItParser().parse(
        Path("unused.md"),
        content=content,
        document_id="doc_test",
        relative_path=relative_path,
    )


def test_markdown_obsidian_and_html_references_keep_exact_provenance(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "notes" / "images").mkdir(parents=True)
    png = root / "notes" / "images" / "diagram.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x02\x00\x00\x00\x03" + b"\x00" * 16)
    content = """# Assets

Context before.
![Diagram](images/diagram.png)
![[images/diagram.png|300]]
<img src="images/diagram.png" alt="HTML diagram">
Context after.
"""
    document = _parse(content)
    chunks = StructuralChunker().build(document)

    graph = build_asset_graph(document, chunks, root=root, root_id="vault")

    assert len(graph.assets) == 1
    asset = graph.assets[0]
    assert asset.relative_path == "notes/images/diagram.png"
    assert asset.content_hash is not None
    assert asset.mime_type == "image/png"
    assert (asset.width, asset.height) == (2, 3)
    assert [reference.syntax for reference in graph.references] == ["markdown", "obsidian", "html"]
    assert [reference.source_span.start_line for reference in graph.references] == [4, 5, 6]
    assert all(reference.document_relative_path == "notes/example.md" for reference in graph.references)
    assert all(reference.asset_id == asset.asset_id for reference in graph.references)
    assert graph.references[0].raw_reference == "images/diagram.png"
    assert graph.references[1].raw_reference == "images/diagram.png|300"
    assert graph.references[2].alt_text == "HTML diagram"


def test_reference_resolution_is_fail_closed_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    document = _parse("![secret](../../secret.png)\n![remote](https://example.invalid/a.png)")
    graph = build_asset_graph(document, StructuralChunker().build(document), root=root, root_id="vault")

    assert graph.assets == ()
    assert [reference.resolution_status for reference in graph.references] == ["unsafe_reference", "external_reference"]
    assert all(reference.asset_id is None for reference in graph.references)


def test_image_chunks_search_only_alt_and_surrounding_text() -> None:
    raw_reference = "private/family-photo.png"
    document = _parse(f"Context before.\n{f'![Architecture]({raw_reference})'}\nContext after.")
    blocks = [block for block in document.blocks if block.block_type == BlockType.IMAGE_REFERENCE]
    chunks = [chunk for chunk in StructuralChunker().build(document) if chunk.parent_block_ids == (blocks[0].block_id,)]

    assert len(chunks) == 1
    searchable = chunks[0].embedding_text
    assert "Architecture" in searchable
    assert "Context before" in searchable
    assert "Context after" in searchable
    assert raw_reference not in searchable
    assert raw_reference not in chunks[0].display_content


def test_inline_image_reference_has_exact_character_offsets() -> None:
    content = "Before ![Diagram](images/diagram.png) after."
    document = _parse(content)
    image = next(block for block in document.blocks if block.block_type == BlockType.IMAGE_REFERENCE)

    assert image.raw_markdown == "![Diagram](images/diagram.png)"
    assert image.source_span.start_line == image.source_span.end_line == 1
    assert content[image.source_span.start_offset : image.source_span.end_offset] == image.raw_markdown
    assert image.attributes["surrounding_text"] == "Before\nafter."


def test_image_without_alt_or_context_is_registered_but_not_searchable(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    document = _parse("![[missing.png]]")
    chunks = StructuralChunker().build(document)
    graph = build_asset_graph(document, chunks, root=root, root_id="vault")

    assert chunks == ()
    assert len(graph.assets) == 1
    assert graph.assets[0].exists is False
    assert graph.references[0].resolution_status == "missing"


def test_commonmark_destination_and_title_resolve_only_the_asset_path(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "notes" / "images").mkdir(parents=True)
    (root / "notes" / "images" / "foo(1).png").write_bytes(b"balanced")
    (root / "notes" / "images" / "foo.png").write_bytes(b"titled")
    content = (
        "Context before.\n"
        "![Balanced](images/foo(1).png)\n"
        '![Titled](images/foo.png "Optional title")\n'
        "Context after."
    )
    document = _parse(content)
    chunks = StructuralChunker().build(document)

    graph = build_asset_graph(document, chunks, root=root, root_id="vault")

    assert [asset.relative_path for asset in graph.assets] == [
        "notes/images/foo(1).png",
        "notes/images/foo.png",
    ]
    assert [reference.raw_reference for reference in graph.references] == [
        "images/foo(1).png",
        "images/foo.png",
    ]
    assert all(reference.resolution_status == "resolved" for reference in graph.references)
    searchable = "\n".join(chunk.embedding_text for chunk in chunks)
    assert "Balanced" in searchable
    assert "Titled" in searchable
    assert "Context before" in searchable
    assert "Context after" in searchable
    assert "foo(1).png" not in searchable
    assert "foo.png" not in searchable
    assert "Optional title" not in searchable
