"""Create a bounded privacy-safe chunk audit report for a Markdown corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from mdrack.eval.chunk_audit import audit_markdown_files
from mdrack.eval.privacy import scan_privacy


class _EmptyCorpusError(ValueError):
    """Raised when an audit root contains no Markdown files."""


class _CorpusParseError(ValueError):
    """Raised when no selected Markdown file can be parsed."""


def _raise_walk_error(error: OSError) -> None:
    raise error


def _discover_markdown_paths(root: Path, max_files: int) -> list[Path]:
    if max_files <= 0:
        raise ValueError("max_files must be positive")

    root_stat = root.stat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise NotADirectoryError

    paths: list[Path] = []
    for directory, _, filenames in os.walk(root, onerror=_raise_walk_error):
        directory_path = Path(directory)
        paths.extend(directory_path / name for name in filenames if name.endswith(".md"))

    selected = sorted(paths, key=lambda path: path.as_posix())[:max_files]
    if not selected:
        raise _EmptyCorpusError
    return selected


def _error_category(error: OSError | ValueError) -> str:
    if isinstance(error, FileNotFoundError):
        return "corpus_missing"
    if isinstance(error, PermissionError):
        return "corpus_inaccessible"
    if isinstance(error, _EmptyCorpusError):
        return "corpus_empty"
    if isinstance(error, _CorpusParseError):
        return "corpus_parse_failed"
    if isinstance(error, OSError):
        return "corpus_io_error"
    return "invalid_arguments"


def _corpus_ref(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return f"sha256:{digest.hexdigest()[:16]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Markdown corpus root")
    parser.add_argument("--max-files", type=int, default=100)
    parser.add_argument("--parser-backend", choices=("markdown_it", "legacy"), default="markdown_it")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local-reports/chunk-audit.json"),
    )
    args = parser.parse_args()

    root = args.root.resolve()
    try:
        paths = _discover_markdown_paths(root, args.max_files)
        report = audit_markdown_files(
            paths,
            corpus_ref=_corpus_ref(paths),
            max_files=args.max_files,
            parser_backend=args.parser_backend,
        ).to_dict()
        if report["metrics"]["files_succeeded_count"] == 0:
            raise _CorpusParseError
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error_category": _error_category(error)}))
        return 2
    privacy = scan_privacy(
        report,
        forbidden_values=[path.name for path in paths] + [root.as_posix()],
    )
    if not privacy.safe:
        print(json.dumps({"ok": False, "privacy": privacy.to_dict()}, ensure_ascii=True))
        return 2

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report": report}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
