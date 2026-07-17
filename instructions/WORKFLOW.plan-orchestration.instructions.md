---
applyTo: "**"
name: "WORKFLOW.PlanOrchestration"
description: "When to use: read-only investigation, architecture review, estimation, planning, decomposition, or evidence gathering before implementation."
---

# Planning workflow

## Mode

Planning is read-only. Do not edit project files, stage, commit, push, mutate the
runtime, index a user vault, or create runnable implementation work without explicit
approval.

## Process

1. Bind the repository, board, workdir, branch, active plan, and allowed read scope.
2. Read `AGENTS.md`, matching subsystem instructions, and only relevant current docs.
3. Inspect live code, migrations, tests, and public entry points; do not infer behavior from plans.
4. For a broad question, split independent read-only investigations by subsystem.
5. Record a claim ledger with source anchors, contradictions, assumptions, and confidence.
6. Produce a bounded implementation plan with stages, editable paths, non-goals,
   acceptance checks, stop conditions, and review boundaries.
7. Obtain approval before mutation when the request is exploratory or the plan changes scope.

## Kanban guidance

Use a Kanban card or DAG when work has multiple owners/artifacts, durable dependencies,
review, or human/live gates. Each card owns one bounded question and includes exact
scope, required reads, done condition, checks, and structured handoff. Deterministic
successors should be dependencies; semantic PASS/REJECT choices need one decision owner.
Do not use timer polling or a model loop to wait for workers.

## Evidence standard

- Separate verified facts, estimates, and hypotheses.
- Current code/migrations outrank stale docs.
- Worker summaries are inputs, not independent proof.
- Identify exact unknowns instead of filling them with plausible prose.
- Planning completion is a plan or audit artifact, not implementation completion.

## Handoff

Return the objective, inspected sources, findings, proposed stages, file ownership,
verification commands, risks, unresolved decisions, and recommended next action.
