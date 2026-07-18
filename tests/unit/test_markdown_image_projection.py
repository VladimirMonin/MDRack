"""Replacement oracle for Markdown image syntax as prose-only projection."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.chunking import StructuralChunker, StructuralChunkingConfig
from mdrack.markdown.parser import parse_markdown


def test_structural_and_legacy_parsers_share_prose_only_projection(tmp_path: Path) -> None:
    source = "\n".join(
        (
            "Before ![Markdown alt](private/a.png \"title\") after.",
            "![[private/b.png|Obsidian alias]]",
            "![[private/c.png]]",
            "![[private/d.png|320]]",
            "![[private/e.png|320x240]]",
            '<img src="private/f.png" alt="HTML alt" width="320">',
        )
    )
    structural = MarkdownItParser().parse(
        tmp_path / "note.md",
        content=source,
        document_id="doc_projection",
        relative_path="note.md",
    )
    legacy = parse_markdown(tmp_path / "note.md", content=source)

    structural_text = "\n".join(block.plain_text or "" for block in structural.blocks)
    legacy_text = "\n".join(block.content for block in legacy.blocks)
    for projected in (structural_text, legacy_text):
        assert projected.count("Markdown alt") == 1
        assert projected.count("Obsidian alias") == 1
        assert projected.count("HTML alt") == 1
        for forbidden in ("private/", "title", "320", "240", "src="):
            assert forbidden not in projected


@pytest.mark.parametrize(
    ("image", "definition"),
    (
        ("![Full alt][diagram]", '[diagram]: private/full.png "private title"'),
        ("![Collapsed alt][]", "[Collapsed alt]: private/collapsed.png 'private title'"),
        ("![Shortcut alt]", "[Shortcut alt]: private/shortcut.png"),
    ),
)
def test_reference_images_use_document_definitions_without_indexing_them(
    tmp_path: Path,
    image: str,
    definition: str,
) -> None:
    source = f"Before {image} after.\n\n{definition}"
    parser = MarkdownItParser()
    first = parser.parse(
        tmp_path / "references.md",
        content=source,
        document_id="doc_reference_projection",
        relative_path="references.md",
    )
    second = parser.parse(
        tmp_path / "references.md",
        content=source,
        document_id="doc_reference_projection",
        relative_path="references.md",
    )
    legacy = parse_markdown(tmp_path / "references.md", content=source)

    expected_alt = image.removeprefix("![").split("]", maxsplit=1)[0]
    structural_text = "\n".join(block.plain_text or "" for block in first.blocks)
    legacy_text = "\n".join(block.content for block in legacy.blocks)
    for projected in (structural_text, legacy_text):
        assert projected == f"Before {expected_alt} after."
        assert projected.count(expected_alt) == 1
        for forbidden in ("private/", "private title", "diagram"):
            assert forbidden not in projected
    assert first.source_hash == second.source_hash
    assert [block.block_id for block in first.blocks] == [block.block_id for block in second.blocks]
    assert [
        source[block.source_span.start_offset : block.source_span.end_offset]
        for block in first.blocks
    ] == [block.raw_markdown for block in first.blocks]


@pytest.mark.parametrize(
    "html",
    (
        '<img src="private/quoted.png" alt="Quoted alt" width="320">',
        "<img src='private/single.png' alt='Single alt' height='240'>",
        "<img src=private/unquoted.png alt=Unquoted-alt width=640>",
    ),
)
@pytest.mark.parametrize("inline", (False, True))
def test_html_image_alt_projection_accepts_quoted_and_unquoted_attributes(
    tmp_path: Path,
    html: str,
    inline: bool,
) -> None:
    source = f"Before {html} after." if inline else html
    structural = MarkdownItParser().parse(
        tmp_path / "html.md",
        content=source,
        document_id="doc_html_projection",
        relative_path="html.md",
    )
    legacy = parse_markdown(tmp_path / "html.md", content=source)
    expected_alt = next(
        value for value in ("Quoted alt", "Single alt", "Unquoted-alt") if value in html
    )

    for projected in (
        "\n".join(block.plain_text or "" for block in structural.blocks),
        "\n".join(block.content for block in legacy.blocks),
    ):
        assert projected == (f"Before {expected_alt} after." if inline else expected_alt)
        assert projected.count(expected_alt) == 1
        for forbidden in ("private/", "src=", "320", "240", "640"):
            assert forbidden not in projected


@pytest.mark.parametrize(
    ("html", "expected_alt"),
    (
        ('<img src="private/double.png" alt="Double > alt">', "Double > alt"),
        ("<img src='private/single.png' alt='Single > alt'>", "Single > alt"),
        ('<img src="private/self-close.png" alt="Self > close"/>', "Self > close"),
        (
            "<IMG DATA-ALT=PrivateMetadata ARIA-ALT='Private aria' "
            'PREFIXALT="Private suffix" DATA-SRC=private/path.png '
            'ALT="Exact alt">',
            "Exact alt",
        ),
        (
            "<img data-alt=PrivateMetadata aria-alt='Private aria' "
            "prefixalt=PrivateSuffix data-src=private/path.png>",
            "",
        ),
        (
            '<img alt="First alt" ALT="Private duplicate" src=private/path.png>',
            "First alt",
        ),
    ),
)
@pytest.mark.parametrize("inline", (False, True))
def test_html_image_projection_scans_complete_tags_and_exact_attribute_names(
    tmp_path: Path,
    html: str,
    expected_alt: str,
    inline: bool,
) -> None:
    source = f"Before {html} after." if inline else html
    structural = MarkdownItParser().parse(
        tmp_path / "html-boundaries.md",
        content=source,
        document_id="doc_html_boundary_projection",
        relative_path="html-boundaries.md",
    )
    legacy = parse_markdown(tmp_path / "html-boundaries.md", content=source)
    expected = f"Before {expected_alt} after." if inline else expected_alt
    if inline and not expected_alt:
        expected = "Before  after."

    for projected in (
        "\n".join(block.plain_text or "" for block in structural.blocks),
        "\n".join(block.content for block in legacy.blocks),
    ):
        assert projected == expected
        if expected_alt:
            assert projected.count(expected_alt) == 1
        for forbidden in (
            "private/",
            "PrivateMetadata",
            "Private aria",
            "Private suffix",
            "PrivateSuffix",
            "Private duplicate",
            "src=",
            "alt=",
            "<img",
            "<IMG",
        ):
            assert forbidden not in projected


@pytest.mark.parametrize(
    "html",
    (
        '<img src="private/path.png" alt="unterminated >',
        "<img src=private/path.png alt='unterminated >",
        '<imgx src="private/path.png" alt="not an image tag">',
    ),
)
def test_html_image_projection_does_not_partially_consume_malformed_or_prefixed_tags(
    html: str,
) -> None:
    assert MarkdownItParser().project_text(html) == html


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            'Straße <IMG src=private/unicode.png alt="Öffentlich"> τέλος',
            "Straße Öffentlich τέλος",
        ),
        (
            'Straße\n<IMG src=private/unicode.png alt="Öffentlich">\nτέλος',
            "Straße\nÖffentlich\nτέλος",
        ),
    ),
)
def test_html_image_projection_keeps_original_offsets_after_casefold_expansion(
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    structural = MarkdownItParser().parse(
        tmp_path / "unicode-offsets.md",
        content=source,
        document_id="doc_unicode_offsets",
        relative_path="unicode-offsets.md",
    )
    legacy = parse_markdown(tmp_path / "unicode-offsets.md", content=source)

    assert "\n".join(block.plain_text or "" for block in structural.blocks) == expected
    assert "\n".join(block.content for block in legacy.blocks) == expected
    assert structural.source_hash == hashlib.sha256(source.encode()).hexdigest()
    assert [
        source[block.source_span.start_offset : block.source_span.end_offset]
        for block in structural.blocks
    ] == [block.raw_markdown for block in structural.blocks]


@pytest.mark.parametrize(
    "source",
    (
        'Before `<img src=private/code.png alt=CodeSecret>` after.',
        r"Before \<img src=private/escaped.png alt=EscapedSecret> after.",
        "Before <!-- <img src=private/comment.png alt=CommentSecret> --> after.",
        'Before <a title="<img src=private/nested.png alt=NestedSecret>">link</a> after.',
        '<script>const example = "<img src=private/raw.png alt=RawSecret>";</script>',
    ),
)
def test_html_image_projection_requires_a_standalone_markdown_it_html_token(
    tmp_path: Path,
    source: str,
) -> None:
    parser = MarkdownItParser()
    structural = parser.parse(
        tmp_path / "token-context.md",
        content=source,
        document_id="doc_token_context",
        relative_path="token-context.md",
    )
    legacy = parse_markdown(tmp_path / "token-context.md", content=source)

    assert parser.project_text(source) == source
    if not source.startswith("<script>"):
        assert "\n".join(block.plain_text or "" for block in structural.blocks) == source
    assert "\n".join(block.content for block in legacy.blocks) == source


@pytest.mark.parametrize(
    "source",
    (
        "```html\n<img src=private/fence.png alt=FenceSecret>\n```",
        "    <img src=private/indent.png alt=IndentSecret>",
    ),
)
def test_html_image_projection_does_not_rewrite_code_blocks(
    tmp_path: Path,
    source: str,
) -> None:
    parser = MarkdownItParser()
    structural = parser.parse(
        tmp_path / "code-context.md",
        content=source,
        document_id="doc_code_context",
        relative_path="code-context.md",
    )
    legacy = parse_markdown(tmp_path / "code-context.md", content=source)

    assert parser.project_text(source) == source
    assert "<img src=" in "\n".join(block.raw_markdown for block in structural.blocks)
    assert "<img src=" in "\n".join(block.content for block in legacy.blocks)


def test_long_projected_alt_splits_with_ordered_stable_source_spans(tmp_path: Path) -> None:
    alt = " ".join(f"слово-{index}" for index in range(60))
    source = f"Before ![{alt}](private/hidden.png) after."
    parser = MarkdownItParser()
    first_document = parser.parse(
        tmp_path / "long.md",
        content=source,
        document_id="doc_long_projection",
        relative_path="long.md",
    )
    second_document = parser.parse(
        tmp_path / "long.md",
        content=source,
        document_id="doc_long_projection",
        relative_path="long.md",
    )
    chunker = StructuralChunker(
        StructuralChunkingConfig(
            min_chars=1,
            target_chars=80,
            hard_limit_chars=100,
            max_tokens=100,
            overlap_chars=0,
        )
    )
    first = chunker.build(first_document)
    second = chunker.build(second_document)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert len(first) > 1
    spans = [(chunk.source_span.start_offset, chunk.source_span.end_offset) for chunk in first]
    assert all(start is not None and end is not None and start < end for start, end in spans)
    exact_spans = [
        (start, end) for start, end in spans if start is not None and end is not None
    ]
    assert len(exact_spans) == len(spans)
    assert all(
        left[1] <= right[0]
        for left, right in zip(exact_spans, exact_spans[1:], strict=False)
    )
    projected = "".join(chunk.display_content for chunk in first)
    assert "private/hidden.png" not in projected
    words = projected.split()
    assert all(words.count(f"слово-{index}") == 1 for index in range(60))


def test_production_tree_has_no_legacy_asset_or_image_reference_symbols() -> None:
    root = Path(__file__).parents[2] / "src" / "mdrack"
    forbidden = (
        "Asset",
        "AssetReference",
        "AssetGraph",
        "IMAGE_REFERENCE",
        '"image_reference"',
        "'image_reference'",
        "list_assets_for_file",
        "list_asset_references",
        "mdrack.domain.assets",
        "mdrack.application.assets",
    )
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "storage/sqlite/migrations" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        for symbol in forbidden:
            if symbol in text:
                offenders.append(f"{path.relative_to(root)}:{symbol}")
    assert offenders == []
    assert not (root / "domain" / "assets.py").exists()
    assert not (root / "application" / "assets.py").exists()
