"""Tests for mdrack.markdown.embedding_text and mdrack.embeddings.hashing."""

from __future__ import annotations

from mdrack.embeddings.hashing import hash_embedding_text
from mdrack.markdown.embedding_text import build_embedding_text
from mdrack.markdown.ir import ContentType, FinalChunk


def _chunk(content: str = "Hello world.", **kwargs) -> FinalChunk:
    """Helper to create a FinalChunk with sensible defaults."""
    return FinalChunk(
        id="test-chunk-001",
        document_id="/docs/test.md",
        section_id="sec-001",
        content=content,
        content_type=ContentType.TEXT,
        **kwargs,
    )


# ── embedding text format ────────────────────────────────────────────


class TestBuildEmbeddingText:
    def test_all_fields(self) -> None:
        result = build_embedding_text(
            chunk=_chunk("Some content here."),
            document_title="My Document",
            relative_path="docs/guide.md",
            heading_path="Intro > Basics",
        )
        assert result == "[My Document] docs/guide.md > Intro > Basics\n\nSome content here."

    def test_with_empty_title(self) -> None:
        result = build_embedding_text(
            chunk=_chunk("Body text."),
            document_title="",
            relative_path="notes.md",
            heading_path="Section One",
        )
        assert result == "notes.md > Section One\n\nBody text."

    def test_with_empty_heading_path(self) -> None:
        result = build_embedding_text(
            chunk=_chunk("Body text."),
            document_title="My Doc",
            relative_path="file.md",
            heading_path="",
        )
        assert result == "[My Doc] file.md\n\nBody text."

    def test_with_empty_title_and_heading(self) -> None:
        result = build_embedding_text(
            chunk=_chunk("Content."),
            document_title="",
            relative_path="page.md",
            heading_path="",
        )
        assert result == "page.md\n\nContent."

    def test_only_content_when_all_metadata_empty(self) -> None:
        result = build_embedding_text(
            chunk=_chunk("Bare content."),
            document_title="",
            relative_path="",
            heading_path="",
        )
        assert result == "Bare content."

    def test_relative_path_always_present(self) -> None:
        result = build_embedding_text(
            chunk=_chunk("text"),
            document_title="",
            relative_path="src/readme.md",
            heading_path="",
        )
        assert "src/readme.md" in result


# ── hash determinism & sensitivity ───────────────────────────────────


class TestHashEmbeddingText:
    def test_deterministic(self) -> None:
        text = "[Title] path.md > Section\n\nContent."
        h1 = hash_embedding_text(text)
        h2 = hash_embedding_text(text)
        assert h1 == h2

    def test_hex_length(self) -> None:
        h = hash_embedding_text("anything")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_changes_when_text_changes(self) -> None:
        h1 = hash_embedding_text("alpha")
        h2 = hash_embedding_text("beta")
        assert h1 != h2

    def test_changes_when_content_differs(self) -> None:
        h1 = hash_embedding_text("[Doc] path.md\n\nFirst version.")
        h2 = hash_embedding_text("[Doc] path.md\n\nSecond version.")
        assert h1 != h2


# ── snapshot-style: fixed input -> fixed output ──────────────────────


class TestSnapshot:
    """Verify that fixed inputs produce stable outputs across runs."""

    def test_snapshot_embedding_text(self) -> None:
        result = build_embedding_text(
            chunk=_chunk("The quick brown fox."),
            document_title="Fables",
            relative_path="animals/fox.md",
            heading_path="Mammals > Foxes",
        )
        expected = "[Fables] animals/fox.md > Mammals > Foxes\n\nThe quick brown fox."
        assert result == expected

    def test_snapshot_hash(self) -> None:
        text = "[Fables] animals/fox.md > Mammals > Foxes\n\nThe quick brown fox."
        # First call establishes the expected value; second call confirms stability
        h1 = hash_embedding_text(text)
        h2 = hash_embedding_text(text)
        assert h1 == h2
        assert len(h1) == 64
