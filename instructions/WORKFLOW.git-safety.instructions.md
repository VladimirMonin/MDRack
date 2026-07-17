---
applyTo: "**"
name: "WORKFLOW.GitSafety"
description: "When to use: staging, committing, pushing, pulling, branch changes, merges, conflicts, tags, rebases, resets, or any Git history mutation."
---

# Git safety

## Authorization boundary

Read-only commands such as status, diff, log, branch inspection, and remote inspection
are allowed. Staging, commits, pushes, pulls, branch creation/switching, merges, tags,
rebases, resets, restores, cleaning, and history rewrites require explicit user/task scope.
Do not initialize Git automatically in an existing project task.

## Mandatory context scan

Before any authorized mutation run:

```bash
git rev-parse --is-inside-work-tree
git symbolic-ref --quiet --short HEAD
git status --short
git diff
git diff --staged
git remote -v
git log --oneline --decorate -n 10
```

Stop on detached HEAD, unresolved conflicts, unexpected staged content, unrelated user
changes that overlap the target, or branch/remote ambiguity.

## Safety rules

- Never use `git add .` when explicit paths can be staged.
- Never commit unrelated or pre-existing changes.
- Never discard changes with `reset --hard`, `clean`, `restore`, or checkout-path commands
  without explicit destructive approval and a recovery plan.
- Never rebase, amend, force-push, or rewrite history without explicit approval.
- Never resolve conflicts by blindly choosing `ours` or `theirs`; understand each hunk.
- Do not stash automatically; a stash can hide ownership and still alter state.
- Re-read staged diff after staging and before committing.
- Run applicable quality gates before the commit and record real results.

## Commit format

Use a conventional, scoped subject such as:

```text
docs(architecture): publish current system map
fix(retrieval): preserve source locator parity
```

Keep commits coherent and dependency-safe. Include verification in the body when useful.
Do not create empty or checkpoint commits unless explicitly requested.

## Push rules

- Confirm the named remote and upstream live; do not assume `origin/master`.
- Use an ordinary fast-forward push only unless a stronger operation is explicitly approved.
- After pushing, verify local HEAD, remote-tracking ref, and remote branch ref when access permits.
- Do not claim publication from a successful local commit alone.

## Final report

State branch, intended files, commit(s) if authorized, verification, push/read-back result,
unrelated changes left untouched, and any divergence or unverified remote condition.
