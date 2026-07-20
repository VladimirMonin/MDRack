# ADR-0008: Whole-resource text aggregation

- **Status:** Accepted and implemented for the v0.4 textual-similarity slice
- **Date:** 2026-07-20
- **Scope:** transcript-derived whole-resource text and vector identity
- **Evidence:** `packages/mdrack-media/API.md`, `packages/mdrack-media/src/mdrack_media/aggregation.py`, `src/mdrack/application/compatibility.py`, and `tests/media/test_whole_resource_similarity.py`

## Context

Resource-level similarity needs one deterministic representation without pretending that transcript or frame-caption text is visual or acoustic evidence. Short resources can be embedded as joined text; bounded long resources need an aggregation path that preserves the exact basis and policy identity.

## Decision

`WholeResourceTextPolicy` explicitly selects and fingerprints the textual aggregation policy. The short path builds one `whole_resource` text unit from deterministic source text. The long path uses `weighted_centroid()` over caller-supplied compatible unit vectors and positive weights, then L2-normalizes the result. Dimension mismatch, missing weights, non-finite input, and zero norm fail closed.

The whole-resource representation, unit, and embedding space carry aggregation and embedding fingerprints. Public similarity results state an explicit textual basis. They must not use visual, acoustic, or multimodal wording unless a separately reviewed capability supplies that evidence.

## Consequences

- Resource-level similarity is reproducible from explicit textual inputs and fingerprints.
- Input order does not change the weighted centroid.
- Policy or embedding changes require a fresh replacement batch and new derived identity.
- This decision does not infer vectors from media bytes or select a live embedding provider.
