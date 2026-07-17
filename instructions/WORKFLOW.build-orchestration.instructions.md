---
applyTo: "**"
name: "WORKFLOW.BuildOrchestration"
description: "When to use: implementation, bug fixes, refactors, tests, documentation edits, or any authorized project-file mutation."
---

# Build workflow

## Preconditions

- Confirm build/mutation is authorized by the user or active task.
- Read `AGENTS.md` and every subsystem instruction matching the editable paths.
- Inspect `git status --short` and preserve unrelated work.
- Respect explicit editable, read-only, and forbidden paths.
- If the change is broad, risky, or architecture-sensitive without an approved plan,
  return to `WORKFLOW.plan-orchestration.instructions.md` first.

## Process

1. State the objective, scope, non-goals, and acceptance checks.
2. Trace relevant symbols and sibling call paths before editing.
3. Use one writer for each shared file/contract; parallelize only non-overlapping reads or workspaces.
4. Make the smallest coherent change in the canonical architecture path.
5. Add focused regressions for behavior changes and synchronize affected contracts/docs/instructions.
6. Review the complete diff for scope, privacy, architecture, and accidental generated files.
7. Run narrow checks while iterating, then the gates in `TEST.quality-gates.instructions.md`.
8. Report changed paths, real command results, unverified boundaries, and residual risks.

## Review loop

Non-trivial code or contract changes require an independent read-only review of the
exact artifact revision. A rejection must identify reproducible defects and repair
gates. Repair one bounded defect family, extend the regression oracle, then obtain a
fresh review; do not reinterpret a terminal task state as semantic PASS.

Documentation-only instruction work still requires routing/frontmatter/overlap checks
and a downstream semantic review when the plan assigns one.

## Boundaries

- Do not commit, push, switch branches, merge, rebase, reset, or stage unless explicitly
  assigned and `WORKFLOW.git-safety.instructions.md` is loaded.
- Do not add dependencies, features, or refactors outside scope.
- Do not replace real integration evidence with fakes or fabricated output.
- Stop on code/migration contradiction, unknown destructive impact, secrets, private
  user data, unrelated writer overlap, or a required decision that cannot be inferred.

## Completion

A change is complete only when the requested artifact exists, the relevant checks were
actually run, the diff matches scope, and the handoff distinguishes proven behavior
from claims not exercised in this run.
