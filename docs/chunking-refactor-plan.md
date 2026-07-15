# Chunking Refactor Plan

> **Status: Superseded.** Retained as the historical buffered-chunking plan.
> Current structural chunking requirements are in
> [`mdrack-v0.2-retrieval-modernization-plan.md`](mdrack-v0.2-retrieval-modernization-plan.md).

## Goal

Fix Markdown chunking without changing the public MDRack architecture.

The final chunk must be built from a buffered sequence of `MarkdownBlock`
items, not from each individual block.

## Scope

Files to change:

1. `src/mdrack/markdown/section_builder.py`
2. `src/mdrack/markdown/chunk_builder.py`
3. `src/mdrack/config/models.py`
4. `tests/unit/test_section_builder.py`
5. `tests/unit/test_chunk_builder.py`

## Non-Goals

Do not:

1. add new chunk types;
2. add parent/child chunks;
3. add semantic chunking;
4. add a new CLI command;
5. change the database schema;
6. change public CLI JSON contracts.

## Phase 1: Section Ownership

Minimal section-builder fixes are required so the chunker receives every
meaningful block exactly once.

Required behavior:

1. H1-only documents must keep body content after the H1.
2. Content before the first H2 must be assigned to a synthetic preamble section.
3. One `MarkdownBlock` must not appear in final chunks more than once.

Implementation note:

The section tree does not need a broad redesign. The important outcome is
correct block ownership for chunking. When multiple sections cover the same
block, the deepest section must win.

## Phase 2: Buffered Chunk Assembly

Replace per-block chunk emission with buffered assembly inside each section.

Rules:

1. Skip empty blocks.
2. Skip `thematic_break` blocks completely.
3. Never emit a heading-only chunk.
4. `paragraph`, `list`, and `blockquote` blocks go into the buffer.
5. `code`, `table`, and `mermaid` blocks are never split internally.
6. Small `code`/`table`/`mermaid` blocks may stay in the same buffer as nearby text.
7. If adding a block would exceed `hard_limit_chars`, flush the current buffer first.
8. Emit a chunk only when the buffer reaches a normal size or the section ends.
9. After primary packing, merge undersized chunks inside the same section.
10. Recompute `chunk_index`, `previous_chunk_id`, and `next_chunk_id` after all merges.

## Phase 3: Default Sizes

Keep existing config field names and update defaults in `src/mdrack/config/models.py`:

```text
min_chunk_chars = 1200
target_chunk_chars = 3200
hard_limit_chars = 8000
overlap_chars = 300
```

## Phase 4: Small-Chunk Merge

If a chunk is smaller than `min_chunk_chars`:

1. try to merge with the previous chunk from the same section;
2. if that fails, try to merge with the next chunk from the same section;
3. allow a single small chunk when the whole document is small;
4. never keep a final chunk that is only a heading, only a thematic break, or only one short throwaway line on a large document.

## Phase 5: Tests

Add or update tests for:

1. H1 plus body keeps the body.
2. Preamble before first H2 is indexed.
3. `thematic_break` does not create a chunk.
4. Heading-only chunk is never emitted.
5. Small text blocks are merged.
6. Small tables do not float alone without surrounding text.
7. Small Mermaid blocks do not float alone without surrounding text.
8. Large `code`/`table`/`mermaid` blocks are not split internally.
9. `previous_chunk_id` and `next_chunk_id` remain correct after merges.
10. `min_chunk_chars` materially reduces tiny-chunk count.

## Acceptance Criteria

1. No chunks equal to `---`.
2. No chunks containing only a heading.
3. No standalone one-line throwaway chunks on large Markdown documents.
4. Real Markdown articles produce far fewer chunks than before.
5. Average text chunk size moves closer to roughly 2000-4500 characters.
6. Existing CLI, indexing, and search tests still pass.
