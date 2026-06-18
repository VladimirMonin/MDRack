"""Render parsed Markdown chunks as a single Markdown artifact.

This is a debugging helper for visually inspecting how the current
parser/section-builder/chunk-builder pipeline splits a real document.

Usage:
    uv run python tests/render_chunk_debug.py \
        "tests/Агенты Hermes/6. От одного профиля к агентной команде.md"

The script prints one Markdown document to stdout. Redirect it to a temp file
outside the repository if you want to inspect it in an editor.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from mdrack.markdown.chunk_builder import build_chunks
from mdrack.markdown.parser import parse_markdown
from mdrack.markdown.section_builder import build_sections

_SEPARATOR = "*" * 20
_DEFAULT_DOCUMENT = (
    "tests/Агенты Hermes/6. От одного профиля к агентной команде.md"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render the current Markdown chunking result as a single Markdown artifact."
        )
    )
    parser.add_argument(
        "document",
        nargs="?",
        default=_DEFAULT_DOCUMENT,
        help="Path to the Markdown document, relative to the repo root by default.",
    )
    parser.add_argument(
        "--target-chars",
        type=int,
        default=None,
        help="Optional override for target_chunk_chars.",
    )
    parser.add_argument(
        "--hard-limit-chars",
        type=int,
        default=None,
        help="Optional override for hard_limit_chars.",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=None,
        help="Optional override for overlap_chars.",
    )
    parser.add_argument(
        "--min-chunk-chars",
        type=int,
        default=None,
        help="Optional override for min_chunk_chars.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output file path. Written as UTF-16 for Windows viewers.",
    )
    return parser.parse_args()


def _resolve_document_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / candidate).resolve()


def _build_config(args: argparse.Namespace) -> dict[str, int]:
    config: dict[str, int] = {}
    if args.min_chunk_chars is not None:
        config["min_chunk_chars"] = args.min_chunk_chars
    if args.target_chars is not None:
        config["target_chunk_chars"] = args.target_chars
    if args.hard_limit_chars is not None:
        config["hard_limit_chars"] = args.hard_limit_chars
    if args.overlap_chars is not None:
        config["overlap_chars"] = args.overlap_chars
    return config


def _render_markdown(document_path: Path, config: dict[str, int]) -> str:
    parsed = parse_markdown(document_path)
    file_id = str(document_path)
    sections = build_sections(parsed.blocks, file_id=file_id)
    chunks = build_chunks(parsed.blocks, sections, file_id=file_id, config=config)

    block_counts = Counter(block.type.value for block in parsed.blocks)
    section_map = {section.id: section for section in sections}

    lines: list[str] = [
        "# Chunk Debug Artifact",
        "",
        f"- Source file: `{document_path}`",
        f"- Title: `{parsed.title or document_path.stem}`",
        f"- Total blocks: `{len(parsed.blocks)}`",
        f"- Total sections: `{len(sections)}`",
        f"- Total chunks: `{len(chunks)}`",
        f"- Chunk config override: `{config or 'defaults'}`",
        "",
        "## Block Inventory",
        "",
    ]

    for block_type, count in sorted(block_counts.items()):
        lines.append(f"- `{block_type}`: `{count}`")

    lines.extend([
        "",
        "## Chunks",
        "",
        "Each chunk is separated by a line of 20 stars.",
        "",
    ])

    for chunk in chunks:
        section = section_map.get(chunk.section_id)
        heading_path = " > ".join(chunk.heading_path) if chunk.heading_path else "(root)"
        lines.extend([
            _SEPARATOR,
            "",
            f"## Chunk {chunk.chunk_index:03d}",
            "",
            f"- Content type: `{chunk.content_type.value}`",
            f"- Characters: `{len(chunk.content)}`",
            f"- Heading path: `{heading_path}`",
            f"- Section title: `{section.title if section else 'unknown'}`",
            f"- Section lines: `{section.start_line if section else '?'}-{section.end_line if section else '?'}`",
            "",
            chunk.content,
            "",
        ])

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    args = _parse_args()
    document_path = _resolve_document_path(args.document)
    config = _build_config(args)
    rendered = _render_markdown(document_path, config)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = (Path(__file__).resolve().parents[1] / output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-16")
        return

    sys.stdout.reconfigure(encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
