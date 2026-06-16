"""Tests for the Markdown file scanner."""

from __future__ import annotations

from pathlib import Path

from mdrack.indexing.scanner import scan_markdown_files


def _touch(path: Path) -> Path:
    """Create an empty file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


class TestBasicScanning:
    """Walk a temp tree and verify all .md files are found."""

    def test_finds_nested_md(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "sub" / "b.md")
        _touch(tmp_path / "sub" / "deep" / "c.md")

        result = scan_markdown_files(tmp_path)

        assert result == [
            Path("a.md"),
            Path("sub/b.md"),
            Path("sub/deep/c.md"),
        ]

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "b.txt")
        _touch(tmp_path / "c.py")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md")]

    def test_returns_relative_paths(self, tmp_path: Path) -> None:
        _touch(tmp_path / "doc.md")

        result = scan_markdown_files(tmp_path)
        assert result[0] == Path("doc.md")


class TestExcludedDirectories:
    """Always-ignored directories are pruned during traversal."""

    def test_ignores_git(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / ".git" / "config")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md")]

    def test_ignores_venv(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / ".venv" / "lib" / "pkg.md")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md")]

    def test_ignores_node_modules(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "node_modules" / "pkg" / "readme.md")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md")]

    def test_ignores_mdrack_store(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / ".mdrack" / "store.db")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md")]

    def test_ignores_pycache(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "__pycache__" / "module.md")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md")]


class TestExcludePatterns:
    """Custom exclude patterns filter additional directories."""

    def test_excludes_tests_by_default(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "tests" / "test_foo.md")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md")]

    def test_custom_exclude_pattern(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "vendor" / "lib.md")

        result = scan_markdown_files(
            tmp_path, exclude=["vendor/**"],
        )
        assert result == [Path("a.md")]

    def test_exclude_overrides_default(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "tests" / "test_a.md")

        # Override defaults: no tests exclusion
        result = scan_markdown_files(tmp_path, exclude=[])
        assert result == [Path("a.md"), Path("tests/test_a.md")]


class TestIncludePatterns:
    """Custom include patterns control which files are collected."""

    def test_custom_include(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "b.md")
        _touch(tmp_path / "c.txt")

        result = scan_markdown_files(tmp_path, include=["**/*.md"])
        assert len(result) == 2

    def test_include_txt_files(self, tmp_path: Path) -> None:
        _touch(tmp_path / "notes.md")
        _touch(tmp_path / "notes.txt")

        result = scan_markdown_files(tmp_path, include=["**/*.md", "**/*.txt"])
        assert len(result) == 2

    def test_include_subdir_only(self, tmp_path: Path) -> None:
        _touch(tmp_path / "root.md")
        _touch(tmp_path / "docs" / "guide.md")

        result = scan_markdown_files(tmp_path, include=["docs/**/*.md"])
        assert result == [Path("docs/guide.md")]


class TestEmptyAndNoMatch:
    """Edge cases: empty root, no matching files."""

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = scan_markdown_files(tmp_path)
        assert result == []

    def test_no_md_files(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.txt")
        _touch(tmp_path / "b.py")

        result = scan_markdown_files(tmp_path)
        assert result == []

    def test_nonexistent_root(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        result = scan_markdown_files(missing)
        assert result == []


class TestSortedOutput:
    """Results must be sorted alphabetically by relative path."""

    def test_sorted(self, tmp_path: Path) -> None:
        _touch(tmp_path / "z.md")
        _touch(tmp_path / "a.md")
        _touch(tmp_path / "m.md")

        result = scan_markdown_files(tmp_path)
        assert result == [Path("a.md"), Path("m.md"), Path("z.md")]
