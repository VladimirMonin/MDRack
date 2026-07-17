---
applyTo: "docs/**/*.md"
name: "DOCS.Architecture"
description: "When to use: current architecture docs, schema or flow descriptions, Mermaid diagrams, documentation indexes, links, limitations, or historical/current labeling."
---

# Architecture documentation

## Responsibility

Keep human-facing architecture documentation synchronized with current code,
migrations, public contracts, and explicit limitations.

## Source hierarchy

1. Executed behavior and tests.
2. SQL migrations and domain/port/application/adapter code.
3. CLI and `MDRackEngine` public DTOs.
4. Current architecture and contract docs.
5. Historical plans and superseded prose.

Never copy a claim from an old plan into current documentation without verifying it
against the current revision.

## Current versus historical material

- Current architecture docs describe what the checked-out code does now.
- Files under `docs/plans/` and legacy planning documents remain historical unless
  explicitly marked as the active plan for the current task.
- Keep historical files, but do not route them as current product truth.
- Future work belongs in limitations, roadmap, or ADR sections and must be labeled as such.

## Required coverage for architecture changes

When applicable, document:

- module boundaries and dependency direction;
- indexing, parsing, structural chunking, provenance, and asset flow;
- schema migrations and exact foreign-key/transaction semantics;
- text, semantic, hybrid, degradation, and reranking behavior;
- CLI and embedded-engine capabilities, DTOs, and asymmetries;
- operational limits such as linear vector scans and LM Studio dependency.

Use source anchors (`path:line` or symbol names) during audit/review. Published docs
may use stable paths/symbols when line numbers would age quickly.

## Mermaid and links

- Give each diagram one role: component, sequence, class/port, or current ER model.
- Keep node labels free of secrets and private machine paths.
- Validate Mermaid syntax/rendering with an available project tool; if unavailable,
  record an explicit waiver instead of claiming validation.
- Use relative repository links and verify every target and heading anchor.
- Update README/documentation indexes when current docs are added, renamed, or removed.

## Honesty rules

- Document current limits instead of smoothing them over.
- Do not claim structural overlap, remote asset fetch, reranking, or public APIs that code does not implement.
- Distinguish fake/offline, local-component, installed-package, and live external evidence.
- Cite only verification commands actually run for the documented revision.

## Verification

Run link/heading/Mermaid checks available in the repository, then the gates in
`TEST.quality-gates.instructions.md` and `git diff --check`.
