"""Aggregate, content-free audits of baseline Markdown chunking behavior."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.chunking import StructuralChunker, StructuralChunkingConfig
from mdrack.domain.blocks import BlockType as SourceBlockType
from mdrack.markdown.chunk_builder import build_chunks
from mdrack.markdown.ir import BlockType, FinalChunk, MarkdownBlock
from mdrack.markdown.parser import parse_markdown
from mdrack.markdown.section_builder import build_sections

_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)|!\[\[[^\]]+\]\]|<img\s", re.IGNORECASE)
_DEFAULT_CHUNKING = {
    "min_chunk_chars": 1200,
    "target_chunk_chars": 3200,
    "hard_limit_chars": 8000,
    "overlap_chars": 300,
}


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, ((len(ordered) * percentile + 99) // 100) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _overlap_chars(previous: str, current: str, maximum: int) -> int:
    upper = min(maximum, len(previous), len(current))
    for size in range(upper, 0, -1):
        if previous[-size:] == current[:size]:
            return size
    return 0


def _content_counts(blocks: list[MarkdownBlock]) -> dict[str, int]:
    diagram_count = sum(
        1
        for block in blocks
        if block.type == BlockType.CODE and block.language and "mermaid" in block.language.lower()
    )
    return {
        "code_count": sum(
            1
            for block in blocks
            if block.type == BlockType.CODE
            and not (block.language and "mermaid" in block.language.lower())
        ),
        "table_count": sum(block.type == BlockType.TABLE for block in blocks),
        "diagram_count": diagram_count,
        "image_count": sum(len(_IMAGE_PATTERN.findall(block.content)) for block in blocks),
    }


@dataclass(frozen=True)
class ChunkAuditReport:
    """Versioned chunk audit report containing aggregates only."""

    corpus_ref: str
    metrics: dict[str, int | float]
    parser_name: str = "markdown_it"
    chunk_strategy_name: str = "structural_blocks"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "report_type": "chunk_audit",
            "corpus_ref": self.corpus_ref,
            "parser_name": self.parser_name,
            "chunk_strategy_name": self.chunk_strategy_name,
            "metrics": self.metrics,
        }


def _audit_structural_files(
    selected: list[Path],
    *,
    corpus_ref: str,
    config: dict[str, int],
) -> ChunkAuditReport:
    parser = MarkdownItParser()
    chunker = StructuralChunker(
        StructuralChunkingConfig(
            target_chars=config["target_chunk_chars"],
            hard_limit_chars=config["hard_limit_chars"],
            max_tokens=config.get("max_chunk_tokens", 2000),
            overlap_chars=config["overlap_chars"],
            code_window_lines=config.get("code_window_lines", 80),
            table_rows_per_chunk=config.get("table_rows_per_chunk", 40),
            mermaid_window_lines=config.get("mermaid_window_lines", 80),
        )
    )
    all_blocks = []
    all_chunks = []
    errors_count = 0
    orphan_blocks = 0
    overlap_total = 0

    for index, path in enumerate(selected):
        document_ref = f"file-{index:06d}"
        try:
            document = parser.parse(
                path,
                document_id=document_ref,
                relative_path=f"{document_ref}.md",
            )
            chunks = chunker.build(document)
        except (OSError, UnicodeError, ValueError):
            errors_count += 1
            continue
        all_blocks.extend(document.blocks)
        all_chunks.extend(chunks)
        parent_ids = {parent for chunk in chunks for parent in chunk.parent_block_ids}
        orphan_blocks += sum(
            block.block_id not in parent_ids
            for block in document.blocks
            if block.block_type
            not in {SourceBlockType.FRONTMATTER, SourceBlockType.HEADING, SourceBlockType.THEMATIC_BREAK}
            and block.raw_markdown.strip()
        )
        for previous, current in zip(chunks, chunks[1:]):
            if previous.parent_block_ids == current.parent_block_ids:
                overlap_total += _overlap_chars(
                    previous.display_content,
                    current.display_content,
                    config["overlap_chars"],
                )

    chunk_lengths = [len(chunk.display_content) for chunk in all_chunks]
    token_estimates = [chunk.estimated_tokens for chunk in all_chunks]
    block_ids = [block.block_id for block in all_blocks]
    metrics: dict[str, int | float] = {
        "files_count": len(selected) - errors_count,
        "files_attempted_count": len(selected),
        "files_succeeded_count": len(selected) - errors_count,
        "files_failed_count": errors_count,
        "blocks_count": len(all_blocks),
        "chunks_count": len(all_chunks),
        "chunk_length_p50": _percentile(chunk_lengths, 50),
        "chunk_length_p90": _percentile(chunk_lengths, 90),
        "chunk_length_p99": _percentile(chunk_lengths, 99),
        "chunk_tokens_p50": _percentile(token_estimates, 50),
        "chunk_tokens_p90": _percentile(token_estimates, 90),
        "chunk_tokens_p99": _percentile(token_estimates, 99),
        "small_chunk_ratio": _ratio(
            sum(length < config["min_chunk_chars"] for length in chunk_lengths),
            len(chunk_lengths),
        ),
        "oversize_chunk_ratio": _ratio(
            sum(length > config["hard_limit_chars"] for length in chunk_lengths),
            len(chunk_lengths),
        ),
        "overlap_ratio": _ratio(overlap_total, sum(chunk_lengths)),
        "code_count": sum(block.block_type == SourceBlockType.CODE for block in all_blocks),
        "table_count": sum(block.block_type == SourceBlockType.TABLE for block in all_blocks),
        "diagram_count": sum(block.block_type == SourceBlockType.MERMAID for block in all_blocks),
        "image_count": sum(block.block_type == SourceBlockType.IMAGE_REFERENCE for block in all_blocks),
        "orphan_block_count": orphan_blocks,
        "duplicate_block_count": len(block_ids) - len(set(block_ids)),
        "source_span_missing_count": 0,
    }
    return ChunkAuditReport(corpus_ref=corpus_ref, metrics=metrics)


def audit_markdown_files(
    paths: list[Path],
    corpus_ref: str,
    max_files: int,
    chunking: dict[str, int] | None = None,
    parser_backend: str = "markdown_it",
) -> ChunkAuditReport:
    """Audit a bounded Markdown corpus without retaining names or note content."""
    if max_files <= 0:
        raise ValueError("max_files must be positive")
    if parser_backend not in {"markdown_it", "legacy"}:
        raise ValueError("parser_backend must be 'markdown_it' or 'legacy'")

    config = {**_DEFAULT_CHUNKING, **(chunking or {})}
    selected = sorted((path.resolve() for path in paths), key=lambda path: path.as_posix())[:max_files]
    if parser_backend == "markdown_it":
        return _audit_structural_files(selected, corpus_ref=corpus_ref, config=config)
    all_blocks: list[MarkdownBlock] = []
    all_chunks: list[FinalChunk] = []
    orphan_blocks = 0
    errors_count = 0
    overlap_total = 0

    for index, path in enumerate(selected):
        file_ref = f"file-{index:06d}"
        try:
            parsed = parse_markdown(path)
            sections = build_sections(parsed.blocks, file_id=file_ref)
            chunks = build_chunks(parsed.blocks, sections, file_id=file_ref, config=config)
        except (OSError, UnicodeError, ValueError):
            errors_count += 1
            continue

        all_blocks.extend(parsed.blocks)
        all_chunks.extend(chunks)
        for block in parsed.blocks:
            if block.type == BlockType.THEMATIC_BREAK or not block.content.strip():
                continue
            if not any(
                block.start_line >= section.start_line and block.end_line <= section.end_line
                for section in sections
            ):
                orphan_blocks += 1
        for previous, current in zip(chunks, chunks[1:]):
            overlap_total += _overlap_chars(
                previous.content, current.content, config["overlap_chars"]
            )

    chunk_lengths = [len(chunk.content) for chunk in all_chunks]
    token_estimates = [(length + 3) // 4 for length in chunk_lengths]
    duplicate_blocks = 0
    seen_hashes: set[str] = set()
    for block in all_blocks:
        digest = hashlib.sha256(
            f"{block.type.value}\0{block.content}".encode("utf-8")
        ).hexdigest()
        if digest in seen_hashes:
            duplicate_blocks += 1
        else:
            seen_hashes.add(digest)

    content_counts = _content_counts(all_blocks)
    total_chunk_chars = sum(chunk_lengths)
    metrics: dict[str, int | float] = {
        "files_count": len(selected) - errors_count,
        "files_attempted_count": len(selected),
        "files_succeeded_count": len(selected) - errors_count,
        "files_failed_count": errors_count,
        "blocks_count": len(all_blocks),
        "chunks_count": len(all_chunks),
        "chunk_length_p50": _percentile(chunk_lengths, 50),
        "chunk_length_p90": _percentile(chunk_lengths, 90),
        "chunk_length_p99": _percentile(chunk_lengths, 99),
        "chunk_tokens_p50": _percentile(token_estimates, 50),
        "chunk_tokens_p90": _percentile(token_estimates, 90),
        "chunk_tokens_p99": _percentile(token_estimates, 99),
        "small_chunk_ratio": _ratio(
            sum(length < config["min_chunk_chars"] for length in chunk_lengths),
            len(chunk_lengths),
        ),
        "oversize_chunk_ratio": _ratio(
            sum(length > config["hard_limit_chars"] for length in chunk_lengths),
            len(chunk_lengths),
        ),
        "overlap_ratio": _ratio(overlap_total, total_chunk_chars),
        **content_counts,
        "orphan_block_count": orphan_blocks,
        "duplicate_block_count": duplicate_blocks,
        "source_span_missing_count": len(all_chunks),
    }
    return ChunkAuditReport(
        corpus_ref=corpus_ref,
        metrics=metrics,
        parser_name="legacy_markdown",
        chunk_strategy_name="buffered_blocks",
    )
