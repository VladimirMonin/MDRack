# MDRack architecture documentation hardening

Status: ACTIVE PLAN
Approved by: user voice instruction, 2026-07-17
Scope: documentation, project instructions, verification, commit and ordinary push

## Goal

Publish a current, code-grounded architecture package for MDRack and replace the
current generic/stale agent guidance with a compact project-specific instruction
system. The result must explain how MDRack works today, not the historical MVP
that was once planned.

## Deliverables

1. Compact `AGENTS.md` router with exact setup, verification and Git boundaries.
2. Atomic project instructions for architecture, SQLite/storage, documentation
   maintenance and quality gates; existing workflow/Git/logging rules remain
   routed without duplicating product architecture.
3. `docs/current-architecture/` package covering:
   - system overview and module boundaries;
   - indexing and structural chunking;
   - SQLite persistence, migrations and current schema;
   - text, semantic and hybrid retrieval;
   - image asset handling;
   - public CLI/API capabilities and explicit limitations.
4. Mermaid diagrams with separate roles:
   - component/module overview;
   - indexing sequence;
   - retrieval sequence;
   - class/port relationships;
   - current SQLite ER diagram.
5. Updated root `README.md` and documentation index/cross-links.

## Stages

1. Read-only code/schema/instruction audit with a claim ledger.
2. Instruction-system hardening.
3. Architecture package and README implementation.
4. Independent semantic/documentation review.
5. Bounded repair and fresh review if the first review rejects.
6. Main-session verification, scoped commits and ordinary `origin/master` push.

## Constraints

- Repository: the current MDRack checkout; board: `mdrack`; branch: `master`.
- One writer at a time in the shared checkout.
- Code, tests, migrations and runtime behavior are read-only in this wave.
- No user vault content, private absolute paths, credentials, logs, databases,
  generated indexes or temporary reports may be committed.
- Current code and migrations outrank historical plans and stale prose.
- Historical plans remain historical and must not be presented as current truth.
- Mermaid blocks must be validated/rendered or receive an explicit tool waiver.
- Claims about tests must cite commands actually executed for this revision.
- No force push, rebase, amend, reset or history rewrite.

## Done condition

- Instruction routing has no broken or contradictory current-state rules.
- The architecture package matches live modules and migrations `0000`–`0006`.
- Chunking, storage, provenance, asset and retrieval claims are source-backed.
- Relative links, headings and Mermaid blocks validate.
- Project tests, Ruff, forbidden-dependency check and `git diff --check` pass.
- Independent review returns semantic PASS for the exact documentation revision.
- Main session creates scoped conventional commits, pushes normally and proves
  `HEAD == origin/master == remote refs/heads/master` with a clean tree.

## Stop conditions

Stop and report instead of guessing if code and migrations disagree, a required
diagram cannot be validated, unrelated worktree changes appear, Git diverges, or
publication would expose private user data.
