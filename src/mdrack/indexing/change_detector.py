"""Change detector — compares filesystem state against indexed DB records."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChangePlan:
    """Result of comparing a filesystem scan against the database."""

    new_files: list[Path] = field(default_factory=list)
    changed_files: list[Path] = field(default_factory=list)
    unchanged_files: list[Path] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file's content.

    Reads the file as UTF-8 text and hashes the encoded content,
    matching the behaviour of ``parse_markdown`` so that the parser's
    ``source_hash`` and the change detector's hash are always identical
    regardless of platform newline conventions.

    Args:
        file_path: Absolute path to the file.

    Returns:
        Lowercase hex SHA-256 digest string.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: On read failure.
    """
    content = file_path.read_text(encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def detect_changes(
    conn: sqlite3.Connection,
    current_files: list[Path],
    root: Path,
) -> ChangePlan:
    """Compare *current_files* (relative paths) against indexed DB records.

    Reads ``relative_path`` and ``source_hash`` from the ``files`` table,
    computes SHA-256 for each file on disk, and classifies them into
    new, changed, unchanged, and deleted categories.

    Args:
        conn: Open SQLite connection with ``row_factory`` set.
        current_files: List of relative ``Path`` objects from the scanner.
        root: Absolute root directory where the files live on disk.

    Returns:
        A :class:`ChangePlan` with classified file lists.
    """
    db_rows = conn.execute(
        "SELECT relative_path, source_hash FROM files WHERE status = 'active'"
    ).fetchall()

    db_hashes: dict[str, str] = {row["relative_path"]: row["source_hash"] for row in db_rows}
    db_paths: set[str] = set(db_hashes)

    new_files: list[Path] = []
    changed_files: list[Path] = []
    unchanged_files: list[Path] = []

    seen: set[str] = set()

    for rel in current_files:
        rel_posix = rel.as_posix()
        seen.add(rel_posix)

        abs_path = root / rel
        try:
            disk_hash = compute_file_hash(abs_path)
        except (FileNotFoundError, OSError):
            logger.warning("cannot read file for hashing: %s", abs_path)
            disk_hash = ""

        db_hash = db_hashes.get(rel_posix)

        if db_hash is None:
            new_files.append(rel)
            logger.debug("new file: %s", rel_posix)
        elif db_hash != disk_hash:
            changed_files.append(rel)
            logger.debug("changed file: %s", rel_posix)
        else:
            unchanged_files.append(rel)
            logger.debug("unchanged file: %s", rel_posix)

    deleted_files = sorted(db_paths - seen)

    logger.info(
        "change detection: new=%d changed=%d unchanged=%d deleted=%d",
        len(new_files),
        len(changed_files),
        len(unchanged_files),
        len(deleted_files),
    )

    return ChangePlan(
        new_files=new_files,
        changed_files=changed_files,
        unchanged_files=unchanged_files,
        deleted_files=deleted_files,
    )
