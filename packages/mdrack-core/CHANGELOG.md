# Changelog

All notable changes to `mdrack-core` are documented here.

The format follows Keep a Changelog. Versions use PEP 440 in distribution
metadata and SemVer spelling for the core contract where the two differ.

## 1.0.0rc2 — 2026-10-22

- Add neutral optional publication records/port and conditional indexing entry.
- Share one I/O-free batch validator across both indexing paths; preserve minimal ports.
- Add boolean publication_applied to conditional completion events without exposing tokens.
- Migration: new consumers explicitly require PublicationPort; old indexing/retrieval remain supported.

## 1.0.0rc1 — 2026-07-19

### Added

- Standalone `mdrack-core` wheel and source distribution with no runtime
  dependencies.
- Frozen provider- and persistence-neutral domain, port, application and
  observability exports for contract `1.0.0-rc.1`.
- Explicit score/rank and similarity-basis semantics.
- Categorical-only branch-local scope narrowing.
- External in-memory catalog indexing and retrieval conformance workflow.

### Packaging

- Moved the single authoritative `mdrack_core` source tree into this distribution.
- Changed the `mdrack` application distribution to depend on `mdrack-core` instead
  of bundling another copy.

### Non-claims

- This release candidate does not declare final 1.0 stability.
- It contains no SQLite, Markdown, media, provider, filesystem, network or CLI
  implementation.
