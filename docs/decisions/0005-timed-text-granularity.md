# ADR-0005: Timed-text granularity

- **Status:** Accepted and implemented for the v0.4 offline media contract
- **Date:** 2026-07-20
- **Scope:** `mdrack_media` transcript atoms and deterministic timed passages
- **Evidence:** `packages/mdrack-media/API.md`, `tests/media/test_media_contracts.py`, and `tests/media/test_timed_grouper.py`

## Context

Audio and video transcript producers emit observations at different granularities. Retrieval needs stable units without making provider-specific segments part of the reusable core or silently rewriting source timing and text.

## Decision

`TimedTextAtom` is the canonical producer observation. It owns non-empty text, integer-millisecond `[start_ms,end_ms)` timing, contiguous caller-supplied ordinal, resource identity, and producer/normalization fingerprints.

`TimedPassage` is the retrieval granularity. `group_timed_atoms()` deterministically projects canonical atoms through an explicit `TimedChunkingPolicy` and caller-owned token counter. Every passage retains the complete ordered source-atom IDs, exact covered range, token-count kind, and grouper fingerprint. Atom text is preserved; the grouper inserts only the documented missing separator and never sorts, normalizes, truncates, or repairs observations.

Empty artifacts are valid and produce no passages. A single atom or overlap-connected component that exceeds a hard limit fails closed by default; the explicit `flag` mode records the violation instead of claiming compliance.

## Consequences

- Producer segmentation remains evidence, not the public retrieval unit contract.
- Policy, tokenizer, normalization, or producer changes churn the relevant fingerprints and derived identities.
- Passage coverage is complete and deterministic, with no duplicated source atom.
- This decision does not choose a live transcription provider or claim transcript quality.
