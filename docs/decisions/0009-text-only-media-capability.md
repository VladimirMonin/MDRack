# ADR-0009: Text-only media capability

- **Status:** Accepted for the v0.4 offline capability boundary
- **Date:** 2026-07-20
- **Scope:** audio/video transcript and frame-caption preparation, retrieval, and similarity
- **Evidence:** `packages/mdrack-media/API.md`, `docs/current-architecture/public-interfaces.md`, and `tests/media/test_media_retrieval.py`

## Context

MDRack can index textual evidence derived outside the core from audio and video. Offline tests use deterministic prepared artifacts and caller-supplied vectors; they do not prove OCR, speech recognition, vision-language quality, acoustic embeddings, or visual embeddings.

## Decision

The v0.4 media capability is text-only. Supported evidence is transcript text, timed passages, frame-caption text, and whole-resource text aggregation. `retrieve_media()` may compose transcript-only, frame-only, or weighted hybrid textual branches and nearby frame-caption evidence, while categorical and facet filters apply before result limits.

Prepared builders are provider-, filesystem-, persistence-, and media-byte-neutral. They accept validated artifacts and optional caller-supplied vectors, never open source media, and never call a provider. Frame-caption retrieval remains experimental and non-default pending stronger quality evidence.

Public DTOs expose stable logical IDs, source/unit kinds, integer-millisecond timing, allow-listed fingerprints, and provenance. They do not expose transcript/caption text, paths, URLs, binaries, arbitrary metadata, or claims of visual/acoustic semantics.

## Consequences

- Offline deterministic and local SQLite evidence is not promoted to live-provider or media-quality evidence.
- Optional LM Studio/OpenRouter adapters remain opt-in contracts, not a default capability.
- A future visual or acoustic capability requires a distinct representation, embedding space, evidence basis, privacy review, and live-quality gate.
