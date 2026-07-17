---
applyTo: "*.md"
name: "WORKFLOW.RequirementsToSpec"
description: "When to use: convert a product idea or loose requirements into an implementation-ready technical specification before coding."
---

# Requirements to specification

## Responsibility

Turn an approved idea into a bounded, testable MDRack specification without inventing
features, providers, interfaces, or constraints unsupported by the repository and user goal.

## Process

1. Bind the target document path, audience, current revision, and decision authority.
2. Inspect `AGENTS.md`, current architecture/contracts, relevant code and migrations.
3. Separate stated requirements, repository constraints, inferred requirements, and open questions.
4. Resolve material ambiguity with the owner; do not hide decisions in implementation prose.
5. Define scope and non-goals, user-visible behavior, architecture impact, data/schema
   impact, public contracts, errors/degradation, privacy, migration/compatibility, and rollout.
6. Add measurable acceptance criteria and the exact verification boundary required.
7. Review the draft for contradictions with project invariants and historical/current labeling.

## MDRack constraints to carry forward

Unless the user explicitly approves an architecture change, preserve local-first operation,
SQLite-only persistence, LM Studio HTTP embeddings, no direct model loading, no specialized
vector database, no GUI/web/MCP server, no source Markdown mutation, and stable public
logical identities/source locators.

## Specification quality

A ready specification answers:

- What changes and what remains unchanged?
- Which modules, public surfaces, tables, and migrations are affected?
- How do success, empty, degraded, invalid, and failure cases behave?
- What compatibility and data migration are required?
- Which tests and real integration checks prove acceptance?
- Which risks or decisions remain explicitly open?

The specification may live at the path chosen by the active task; do not assume a fixed
`docs/SPEC.md`. Do not implement while producing a read-only specification unless the
same approved task explicitly includes both stages.
