"""Markdown file scanner — discovers .md files under a root directory."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_ALWAYS_IGNORED: frozenset[str] = frozenset({
    ".git",
    ".venv",
    "node_modules",
    ".mdrack",
    "__pycache__",
})

_DEFAULT_EXCLUDE: list[str] = [
    "tests/**",
    "node_modules/**",
    ".git/**",
    ".venv/**",
    ".mdrack/**",
]

_DEFAULT_INCLUDE: list[str] = ["**/*.md"]

_CACHE: dict[str, re.Pattern[str]] = {}


class CorpusScanError(RuntimeError):
    """Safe corpus-level failure raised when traversal cannot be trusted."""

    code = "CORPUS_SCAN_FAILED"


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a glob pattern to a compiled regex.

    Supports ``**`` (matches any path segments), ``*`` (matches anything
    except ``/``), and ``?`` (single char except ``/``).
    """
    cached = _CACHE.get(pattern)
    if cached is not None:
        return cached

    parts: list[str] = []
    i = 0
    pat = pattern.replace("\\", "/")
    length = len(pat)

    while i < length:
        if pat[i : i + 2] == "**":
            parts.append(".*")
            i += 2
            # skip optional trailing slash after **
            if i < length and pat[i] == "/":
                i += 1
        elif pat[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pat[i] == ".":
            parts.append("\\.")
            i += 1
        else:
            parts.append(re.escape(pat[i]))
            i += 1

    regex = re.compile("^" + "".join(parts) + "$")
    _CACHE[pattern] = regex
    return regex


def _matches_any(rel: str, patterns: list[str]) -> bool:
    """Return True if *rel* (posix-style) matches any glob pattern."""
    for pat in patterns:
        if _glob_to_regex(pat).match(rel):
            return True
    return False


def scan_markdown_files(
    root: Path,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[Path]:
    """Walk *root* recursively and return sorted markdown file paths.

    The returned paths are relative to *root*.

    Args:
        root: Directory to scan.  Must exist.
        include: Glob patterns for files to include.
            Defaults to ``["**/*.md"]``.
        exclude: Glob patterns for paths to exclude.
            Defaults to ``["tests/**", "node_modules/**", ".git/**",
            ".venv/**", ".mdrack/**"]``.

    Returns:
        Sorted list of ``Path`` objects (relative to *root*).
    """
    if not root.is_dir():
        logger.error("file.scan.failed reason=invalid_root")
        raise CorpusScanError("corpus root is unavailable")

    inc = include if include is not None else _DEFAULT_INCLUDE
    exc = exclude if exclude is not None else _DEFAULT_EXCLUDE

    results: list[Path] = []

    def fail_traversal(_error: OSError) -> None:
        logger.error("file.scan.failed reason=traversal_error")
        raise CorpusScanError("corpus traversal failed") from None

    try:
        for dirpath, dirnames, filenames in os.walk(root, onerror=fail_traversal):
            dirnames[:] = [
                d for d in dirnames
                if d not in _ALWAYS_IGNORED
            ]

            for fname in filenames:
                full = Path(dirpath) / fname
                rel = full.relative_to(root).as_posix()

                if _matches_any(rel, exc):
                    logger.debug("file.scan.skipped reason=excluded_pattern")
                    continue

                if not _matches_any(rel, inc):
                    logger.debug("file.scan.skipped reason=include_mismatch")
                    continue

                results.append(Path(rel))
    except CorpusScanError:
        raise
    except OSError:
        logger.error("file.scan.failed reason=traversal_error")
        raise CorpusScanError("corpus traversal failed") from None

    results.sort()
    logger.info("file.scan.finished file_count=%d", len(results))
    return results
