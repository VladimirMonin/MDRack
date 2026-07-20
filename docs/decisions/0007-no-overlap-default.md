# ADR-0007: No-overlap default for timed passages

- **Status:** Accepted and implemented for the v0.4 deterministic grouper
- **Date:** 2026-07-20
- **Scope:** `group_timed_atoms()` passage boundaries
- **Evidence:** `packages/mdrack-media/API.md` and `tests/media/test_timed_grouper.py`

## Context

Overlapping output passages duplicate evidence and can bias retrieval toward a resource with more repeated units. Source observations may themselves overlap, so a boundary rule must preserve their provenance without splitting an overlap-connected component.

## Decision

The default `TimedChunkingPolicy.overlap_atoms` is zero. A passage boundary is safe only when the next atom starts at or after the maximum end time already covered by the candidate passage. Overlapping source atoms therefore remain in the same provenance component, while emitted passage ranges do not overlap and every source atom belongs to exactly one output passage.

The deterministic grouper does not reorder or trim overlapping source observations. If an overlap-connected component exceeds a hard token or duration limit, the default is `TimedGroupingError("unsplittable_hard_limit")`; callers may explicitly request truthful flagged output.

## Consequences

- Default retrieval passages do not duplicate source atoms or temporal coverage.
- Unit-count dominance is not introduced by hidden sliding-window overlap.
- Source overlap remains measurable through grouping metrics.
- A future overlapping-output policy requires a separate contract revision, evidence for ranking effects, and a new fingerprint.
