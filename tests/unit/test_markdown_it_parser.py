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


def test_lists_task_lists_blockquotes_callouts_tables_and_images() -> None:
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
    images = [block for block in document.blocks if block.block_type == BlockType.IMAGE_REFERENCE]
    assert len(images) == 2
    assert {block.attributes["syntax"] for block in images} == {"markdown", "obsidian"}
    assert all(block.heading_path == ("Structures",) for block in document.blocks[1:])


def test_empty_and_no_heading_documents() -> None:
    assert _parse("").blocks == ()
    document = _parse("Русский and English text without headings.")
    assert len(document.blocks) == 1
    assert document.blocks[0].block_type == BlockType.PARAGRAPH
    assert document.blocks[0].heading_path == ()


def test_adjacent_mixed_image_references_have_individual_exact_spans() -> None:
    content = "![One](images/one.png)\n![[two.png]]\nFollowing text."
    document = _parse(content)

    assert [block.block_type for block in document.blocks] == [
        BlockType.IMAGE_REFERENCE,
        BlockType.PARAGRAPH,
        BlockType.IMAGE_REFERENCE,
        BlockType.PARAGRAPH,
    ]
    assert [
        (block.source_span.start_line, block.source_span.end_line)
        for block in document.blocks
    ] == [(1, 1), (1, 1), (2, 2), (3, 3)]
    assert document.blocks[1].raw_markdown == "\n"
    assert document.blocks[1].plain_text is None
    assert document.blocks[1].source_span.start_offset == len("![One](images/one.png)")
    assert document.blocks[1].source_span.end_offset == len("![One](images/one.png)\n")
    assert [block.attributes.get("syntax") for block in document.blocks[::2][:2]] == [
        "markdown",
        "obsidian",
    ]
    assert document.blocks[3].raw_markdown == "\nFollowing text."
    assert document.blocks[3].plain_text == "Following text."
    assert "".join(block.raw_markdown for block in document.blocks) == content


def test_commonmark_image_destinations_and_titles_keep_exact_provenance() -> None:
    content = (
        "![Balanced](images/foo(1).png)\n\n"
        '![Titled](images/foo.png "Optional title")'
    )
    document = _parse(content)

    assert [block.block_type for block in document.blocks] == [
        BlockType.IMAGE_REFERENCE,
        BlockType.IMAGE_REFERENCE,
    ]
    assert [block.raw_markdown for block in document.blocks] == [
        "![Balanced](images/foo(1).png)",
        '![Titled](images/foo.png "Optional title")',
    ]
    assert [block.attributes["reference"] for block in document.blocks] == [
        "images/foo(1).png",
        "images/foo.png",
    ]
    assert document.blocks[0].attributes.get("title") is None
    assert document.blocks[1].attributes["title"] == "Optional title"
    assert [
        content[block.source_span.start_offset : block.source_span.end_offset]
        for block in document.blocks
    ] == [block.raw_markdown for block in document.blocks]
