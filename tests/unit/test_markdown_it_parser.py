"""Contracts for the markdown-it-py Document IR adapter."""

from __future__ import annotations

from pathlib import Path

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.domain.blocks import BlockType


def _parse(content: str):
    return MarkdownItParser().parse(
        Path("/tmp/example.md"),
        content=content,
        document_id="doc_test",
        relative_path="example.md",
    )


def test_heading_levels_paths_preamble_and_stable_ids() -> None:
    content = """Preamble text.

# Один
Body one.

### Skipped
Body skipped.

###### Six
Body six.
"""
    first = _parse(content)
    second = _parse(content)

    headings = [block for block in first.blocks if block.block_type == BlockType.HEADING]
    paragraphs = [block for block in first.blocks if block.block_type == BlockType.PARAGRAPH]
    assert [block.heading_level for block in headings] == [1, 3, 6]
    assert [block.heading_path for block in headings] == [
        ("Один",),
        ("Один", "Skipped"),
        ("Один", "Skipped", "Six"),
    ]
    assert paragraphs[0].heading_path == ()
    assert paragraphs[-1].heading_path == ("Один", "Skipped", "Six")
    assert [block.block_id for block in first.blocks] == [block.block_id for block in second.blocks]
    assert first.source_hash == second.source_hash


def test_frontmatter_fences_mermaid_and_source_spans() -> None:
    content = """---
title: Проверка
tags: [python]
---

~~~python
def answer():
    return 42
~~~

```mermaid
graph TD
  A-->B
```
"""
    document = _parse(content)

    assert document.title == "Проверка"
    assert document.frontmatter["tags"] == ["python"]
    assert document.blocks[0].block_type == BlockType.FRONTMATTER
    code = next(block for block in document.blocks if block.block_type == BlockType.CODE)
    diagram = next(block for block in document.blocks if block.block_type == BlockType.MERMAID)
    assert code.language == "python"
    assert code.raw_markdown.startswith("~~~python")
    assert code.source_span.start_line == 6
    assert code.source_span.end_line == 9
    assert content[code.source_span.start_offset : code.source_span.end_offset] == code.raw_markdown
    assert diagram.language == "mermaid"
    assert diagram.source_span.start_line == 11
    assert diagram.source_span.end_line == 14


def test_repeated_metadata_diagnostics_preserve_aggregate_counts_deterministically() -> None:
    content = "---\nfirst: .nan\nsecond: .inf\n---\n# Body\n\nVisible body."

    first = _parse(content)
    second = _parse(content)

    assert [
        (diagnostic.category, diagnostic.count)
        for diagnostic in first.metadata_diagnostics
    ] == [("METADATA_NON_FINITE_NUMBER", 2)]
    assert first.metadata_diagnostics == second.metadata_diagnostics


def test_lists_task_lists_blockquotes_callouts_tables_and_image_prose() -> None:
    content = """# Structures

- parent
  - child
- [x] complete

> quoted
> continuation

> [!NOTE] Callout title
> Callout body

| A | B |
|---|---|
| 1 | 2 |

![Diagram](images/diagram.png)

![[private-image.png]]
"""
    document = _parse(content)
    types = [block.block_type for block in document.blocks]

    assert BlockType.LIST in types
    task_list = next(block for block in document.blocks if block.block_type == BlockType.LIST)
    assert task_list.attributes["task_list"] is True
    assert BlockType.BLOCKQUOTE in types
    callout = next(block for block in document.blocks if block.block_type == BlockType.CALLOUT)
    assert callout.attributes["callout_kind"] == "NOTE"
    assert BlockType.TABLE in types
    assert all(block.block_type.value != "image_reference" for block in document.blocks)
    projected = [
        block.plain_text
        for block in document.blocks
        if block.block_type == BlockType.PARAGRAPH and block.raw_markdown.startswith("!")
    ]
    assert projected == ["Diagram", None]
    assert all(block.heading_path == ("Structures",) for block in document.blocks[1:])


def test_empty_and_no_heading_documents() -> None:
    assert _parse("").blocks == ()
    document = _parse("Русский and English text without headings.")
    assert len(document.blocks) == 1
    assert document.blocks[0].block_type == BlockType.PARAGRAPH
    assert document.blocks[0].heading_path == ()


def test_adjacent_mixed_image_references_project_into_one_lossless_paragraph() -> None:
    content = "![One](images/one.png)\n![[two.png]]\nFollowing text."
    document = _parse(content)

    assert len(document.blocks) == 1
    block = document.blocks[0]
    assert block.block_type == BlockType.PARAGRAPH
    assert block.raw_markdown == content
    assert block.plain_text == "One\n\nFollowing text."
    assert block.source_span.start_offset == 0
    assert block.source_span.end_offset == len(content)
    assert set(block.attributes) == {"projection_spans"}


def test_commonmark_image_destinations_and_titles_project_only_alt_text() -> None:
    content = (
        "![Balanced](images/foo(1).png)\n\n"
        '![Titled](images/foo.png "Optional title")'
    )
    document = _parse(content)

    assert [block.block_type for block in document.blocks] == [
        BlockType.PARAGRAPH,
        BlockType.PARAGRAPH,
    ]
    assert [block.raw_markdown for block in document.blocks] == [
        "![Balanced](images/foo(1).png)",
        '![Titled](images/foo.png "Optional title")',
    ]
    assert [block.plain_text for block in document.blocks] == ["Balanced", "Titled"]
    assert all(set(block.attributes) == {"projection_spans"} for block in document.blocks)
    assert [
        content[block.source_span.start_offset : block.source_span.end_offset]
        for block in document.blocks
    ] == [block.raw_markdown for block in document.blocks]


def test_image_projection_drops_non_prose_fields_and_keeps_text_once() -> None:
    content = "\n\n".join(
        (
            "Before ![Архитектура](private/path.png \"Secret title\") after.",
            "![[private/bare.png]]",
            "![[private/empty.png|]]",
            "![[private/numeric.png|640]]",
            "![[private/dimensions.png|640x480]]",
            "![[private/alias.png|Схема системы]]",
            '<img src="private/html.png" alt="HTML diagram" width="640" height="480">',
            '<img src="private/empty-html.png">',
        )
    )

    first = _parse(content)
    second = _parse(content)
    projected = [block.plain_text for block in first.blocks]

    assert projected == [
        "Before Архитектура after.",
        None,
        None,
        None,
        None,
        "Схема системы",
        "HTML diagram",
        None,
    ]
    joined = "\n".join(text for text in projected if text)
    assert joined.count("Архитектура") == 1
    assert joined.count("Схема системы") == 1
    assert joined.count("HTML diagram") == 1
    for forbidden in ("private/", "Secret title", "640", "480", "src="):
        assert forbidden not in joined
    assert first.source_hash == second.source_hash
    assert [block.block_id for block in first.blocks] == [block.block_id for block in second.blocks]
    assert all(block.block_type == BlockType.PARAGRAPH for block in first.blocks)
