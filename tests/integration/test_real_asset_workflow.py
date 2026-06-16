"""Integration tests for the real Markdown asset workflow.

Verifies that:
- the real-asset fixture directory exists and contains at least one file
- the sample file is parseable by the Markdown parser
- the scanner discovers the real-asset files when pointed at the directory
"""

from __future__ import annotations

from pathlib import Path

from mdrack.indexing.scanner import scan_markdown_files
from mdrack.markdown.ir import BlockType
from mdrack.markdown.parser import parse_markdown

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
REAL_ASSETS_DIR = FIXTURES_DIR / "markdown_real"
SAMPLE_FILE = REAL_ASSETS_DIR / "sample.md"


# --- Directory & file existence ---

class TestRealAssetDirectory:
    """Ensure the real-asset fixture directory is present."""

    def test_directory_exists(self) -> None:
        assert REAL_ASSETS_DIR.is_dir(), (
            f"Real asset directory not found: {REAL_ASSETS_DIR}"
        )

    def test_directory_contains_at_least_one_md_file(self) -> None:
        md_files = list(REAL_ASSETS_DIR.glob("*.md"))
        assert len(md_files) >= 1, (
            "Expected at least one .md file in the real-asset directory"
        )


# --- Sample file parsing ---

class TestSampleFileParsability:
    """Verify the sample Markdown file is parseable."""

    def test_sample_file_exists(self) -> None:
        assert SAMPLE_FILE.is_file(), (
            f"Sample file not found: {SAMPLE_FILE}"
        )

    def test_parse_returns_parsed_document(self) -> None:
        doc = parse_markdown(SAMPLE_FILE)
        assert doc.file_path == str(SAMPLE_FILE.resolve())
        assert len(doc.blocks) >= 1, "Parsed document should have at least one block"

    def test_parse_extracts_headings(self) -> None:
        doc = parse_markdown(SAMPLE_FILE)
        headings = [b for b in doc.blocks if b.type == BlockType.HEADING]
        assert len(headings) >= 2, (
            f"Expected at least 2 headings, got {len(headings)}"
        )

    def test_parse_extracts_frontmatter_title(self) -> None:
        doc = parse_markdown(SAMPLE_FILE)
        assert doc.title == "Sample Markdown Asset"

    def test_parse_source_hash_is_deterministic(self) -> None:
        doc1 = parse_markdown(SAMPLE_FILE)
        doc2 = parse_markdown(SAMPLE_FILE)
        assert doc1.source_hash == doc2.source_hash


# --- Scanner discovery ---

class TestScannerDiscoversRealAssets:
    """Verify the scanner finds real-asset files under the fixture dir."""

    def test_scan_finds_sample_file(self) -> None:
        files = scan_markdown_files(
            REAL_ASSETS_DIR,
            include=["**/*.md"],
            exclude=[],
        )
        names = [f.name for f in files]
        assert "sample.md" in names, (
            f"Scanner did not find sample.md; found: {names}"
        )

    def test_scan_returns_only_md_files(self) -> None:
        files = scan_markdown_files(
            REAL_ASSETS_DIR,
            include=["**/*.md"],
            exclude=[],
        )
        for f in files:
            assert f.suffix == ".md", f"Expected only .md files, got: {f}"

    def test_scan_with_default_exclude_skips_tests(self) -> None:
        """The default exclude list includes tests/**; override to scan inside."""
        files_excluded = scan_markdown_files(REAL_ASSETS_DIR)
        files_included = scan_markdown_files(
            REAL_ASSETS_DIR,
            exclude=[],
        )
        # When scanning from the fixture dir itself, default exclude
        # should not block discovery of files inside it.
        assert len(files_included) >= len(files_excluded), (
            "Custom exclude should not reduce results"
        )
