---
applyTo: "**"
name: "DOCS.InstructionStyleGuide"
description: "When to use: create, rename, split, route, audit, or edit AGENTS.md and project instruction files."
---

# Project instruction style

## Canonical model

- `AGENTS.md` is a compact project identity, command, invariant, and routing index.
- `instructions/*.instructions.md` contains atomic durable rules for one subsystem or workflow.
- Current architecture/contracts explain behavior; plans and Kanban contain history/progress.

Do not duplicate a subsystem's rules into `AGENTS.md` or unrelated workflow files.

## File and frontmatter format

Use `PREFIX.topic.instructions.md` with a small stable prefix set such as `ARCH`, `DATA`,
`DOCS`, `TEST`, `OBS`, `TOOLING`, or `WORKFLOW`.

Every instruction starts with:

```yaml
---
applyTo: "relevant/glob/**"
name: "PREFIX.Topic"
description: "When to use: concrete files, subsystem, operation, or trigger words."
---
```

`description` is a routing trigger, not a generic summary. Keep `applyTo` narrow enough to
be useful; cross-cutting workflows may use `**`.

## Admission test

Create an instruction only when the rule is verified, durable, future-facing, non-obvious,
owned by one coherent responsibility, and not already covered elsewhere. Do not preserve
release notes, task/card/run IDs, temporary plans, current test counts, commit hashes,
one-off fixes, or speculative architecture.

## Content standard

A useful instruction states responsibility, scope, concrete invariants, safe change process,
stop conditions where needed, and verification. Prefer exact module/contract names that are
stable; avoid pasted implementation, lengthy role templates, universal philosophy, emojis,
and unrelated technology examples.

Current code and migrations outrank stale prose. If code and instruction disagree, verify the
code, repair the instruction when in scope, and report the contradiction rather than guessing.

## Routing and overlap

- Add, rename, or remove an instruction only with the matching `AGENTS.md` route update.
- Search all instruction headings/rules for duplicate ownership and broken file references.
- More-specific subsystem instructions override broad workflow rules.
- Architecture belongs in `ARCH`; persistence in `DATA`; docs truth/diagrams in `DOCS`;
  evidence in `TEST`; logging in `OBS`; execution process in `WORKFLOW`.

## Verification

Check filename/frontmatter/name consistency, every AGENTS route target, unique ownership,
absence of stale/broken references, Markdown structure, and `git diff --check`.
