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
        logger.warning("scan root does not exist or is not a directory: %s", root)
        return []

    inc = include if include is not None else _DEFAULT_INCLUDE
    exc = exclude if exclude is not None else _DEFAULT_EXCLUDE

    results: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _ALWAYS_IGNORED
        ]

        for fname in filenames:
            full = Path(dirpath) / fname
            rel = full.relative_to(root).as_posix()

            if _matches_any(rel, exc):
                logger.debug("excluded: %s", rel)
                continue

            if not _matches_any(rel, inc):
                logger.debug("not matched by include: %s", rel)
                continue

            results.append(Path(rel))

    results.sort()
    logger.info("scanned %s: found %d markdown file(s)", root, len(results))
    return results
