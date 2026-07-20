# ADR-0006: Temporal locator specification

- **Status:** Accepted and implemented for the v0.4 offline media contract
- **Date:** 2026-07-20
- **Scope:** typed media locators in `mdrack_media` and their portable core projection
- **Evidence:** `packages/mdrack-media/API.md`, `tests/media/test_media_contracts.py`, and `tests/media/test_media_retrieval.py`

## Context

Timed transcript and frame evidence must remain seekable and portable without exposing filesystem paths, provider objects, binary media, or SQLite record IDs through public retrieval results.

## Decision

Media producers validate three typed locator forms before conversion to the opaque `mdrack_core.Locator` envelope:

- `TimeSegmentLocator`: integer `start_ms`, `end_ms`, and explicit audio/video track using `[start_ms,end_ms)` semantics;
- `VideoFrameLocator`: integer frame timestamp in milliseconds;
- `WholeMediaLocator`: whole-resource textual evidence without a fabricated interval.

The generic core treats locator kind and canonical JSON payload as opaque persistence data. It does not resolve, open, stat, fetch, or reinterpret a locator. Public media retrieval DTOs project stable logical IDs, allow-listed evidence kind, and integer-millisecond coordinates with `timestamp_unit: "ms"`; raw locator payloads, paths, URLs, text, binary data, and arbitrary metadata do not cross that boundary.

## Consequences

- Timing units and interval semantics are explicit and stable across storage adapters.
- Producer packages, not core or SQLite, own semantic validation of temporal payloads.
- Whole-resource evidence remains distinguishable from timed evidence.
- This decision does not authorize source access or live media extraction.
