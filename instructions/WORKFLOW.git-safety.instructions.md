## Local Git Workflow Rules for AI Coding Agents

This document defines safe Git rules for AI coding agents working in a local-only repository.

The project may not use GitHub, GitLab, remotes, pull requests, issues, or cloud-based collaboration. The agent must therefore treat the local Git history as the primary source of truth and must protect it carefully.

The goal is to help coding agents create clean commits, understand branch context, inspect history, avoid detached HEAD mistakes, handle merges safely, and stop before dangerous actions.

## Core Mission

The agent must maintain a clean, understandable, recoverable local Git history.

Every commit should answer three questions:

1. What changed?
2. Why did it change?
3. How was it verified?

The agent must never treat Git as a dumping ground for random file changes. Git history is project documentation.

## Non-Negotiable Rules

1. Never commit from a detached HEAD state.
2. Never commit without explicitly identifying the current branch.
3. Never commit unrelated user changes.
4. Never run destructive Git commands without explicit human approval.
5. Never hide conflicts by blindly choosing `ours` or `theirs`.
6. Never switch branches with uncommitted changes unless those changes are intentionally committed, stashed, or approved by the human.
7. Never rewrite history unless the human explicitly requests it.
8. Never assume that `main`, `master`, or `develop` is the correct target branch.
9. Always inspect the working tree before committing, merging, rebasing, or switching branches.
10. Always warn the human if the repository state is ambiguous, risky, or inconsistent.
11. Always preserve recoverability before risky operations.
12. Always run relevant verification commands before a final commit whenever possible.
13. Always include a clear commit message with type prefix, concise summary, bullet-point details, and verification notes.

## Local-Only Repository Assumptions

The repository may have no remote.

The agent must not assume that commands such as `git push`, `git pull`, `gh pr`, or GitHub issue references are available.

Before mentioning any remote-based workflow, the agent must check whether a remote exists.

```bash
git remote -v
```

If no remote exists, the agent must continue with a local-only workflow.

The absence of a remote is not an error.

## Dangerous Commands Policy

The following commands are destructive or history-changing and must not be run without explicit human approval:

```bash
git reset --hard
```

```bash
git clean -fd
```

```bash
git clean -fdx
```

```bash
git branch -D <branch>
```

```bash
git checkout -- <file>
```

```bash
git restore <file>
```

```bash
git restore --staged <file>
```

```bash
git rebase <branch>
```

```bash
git commit --amend
```

```bash
git filter-branch
```

```bash
git reflog expire
```

```bash
git gc --prune=now
```

The agent may propose these commands only after explaining:

- what problem the command solves;
- what data or history may be lost;
- what safer alternatives exist;
- how to recover if something goes wrong.

## Mandatory Repository Context Scan

Before making a commit, merge, branch switch, or history-related decision, the agent must run a context scan.

### Step 1: Confirm Repository

```bash
git rev-parse --is-inside-work-tree
```

Expected result:

```text
true
```

If this is not a Git repository, stop and report the problem.

### Step 2: Identify Current Branch

```bash
git branch --show-current
```

If the output is empty, the repository may be in detached HEAD state.

### Step 3: Detect Detached HEAD Safely

```bash
git symbolic-ref --quiet --short HEAD
```

If this command fails or prints nothing, the agent must stop and report:

```text
Detached HEAD detected. I will not commit until a real branch is checked out or created.
```

The agent may suggest creating a branch from the current commit:

```bash
git switch -c recovery/<short-description>
```

The agent must not create that branch without human approval unless the user explicitly allowed autonomous recovery branches.

### Step 4: Inspect Working Tree

```bash
git status --short
```

Then inspect the full status:

```bash
git status
```

The agent must distinguish between:

- files modified by the agent during the current task;
- pre-existing user changes;
- untracked files created intentionally;
- untracked files that may be accidental;
- merge conflict files;
- staged vs unstaged changes.

### Step 5: Inspect Recent History

```bash
git log --oneline --decorate --graph -n 20
```

Use this to understand the current branch context and recent development direction.

### Step 6: Inspect Branches

```bash
git branch --list
```

For more detail:

```bash
git branch -vv
```

The agent must report the intended commit branch before committing.

## Branch Context Rules

Before committing, the agent must say:

```text
Current branch: <branch-name>
Intended commit target: <branch-name>
Detached HEAD: no
Uncommitted changes: <summary>
```

If the branch name looks suspicious, the agent must warn the human.

Suspicious branch examples:

- `main` when the task looks experimental;
- `master` when the project usually uses `develop`;
- a stale feature branch unrelated to the current task;
- a temporary recovery branch;
- a branch with an unclear name;
- no branch name at all.

If the agent is unsure where the commit belongs, it must stop and ask.

## User Changes Protection

The agent must not overwrite or commit changes it did not make.

Before staging files, inspect the diff.

```bash
git diff
```

Inspect staged changes if any already exist:

```bash
git diff --staged
```

If unrelated changes exist, the agent must report them separately.

Example report:

```text
I found pre-existing changes that do not appear related to this task:
- src/config/settings.py
- README.md

I will not stage or commit these files unless you confirm they belong to this task.
```

The agent should stage files explicitly rather than using broad staging by default.

Preferred:

```bash
git add path/to/file1 path/to/file2
```

Avoid unless the repository is known clean and the task intentionally changed many files:

```bash
git add .
```

If using broad staging, the agent must first explain why it is safe.

## Commit Message Standard

Use this format:

```text
<type>(<scope>): <short imperative summary>

- <specific change 1>
- <specific change 2>
- <specific change 3>

Verification:
- <command or manual check>: <result>
```

The first line should be concise and readable.

Recommended length: 50–72 characters where possible.

### Allowed Commit Types

| Type | Use When |
|---|---|
| `feat` | Adds user-facing functionality |
| `fix` | Fixes a bug or incorrect behavior |
| `refactor` | Changes internal structure without changing behavior |
| `docs` | Changes documentation only |
| `test` | Adds or changes tests |
| `style` | Formatting only, no logic changes |
| `perf` | Improves performance |
| `build` | Changes build system, dependencies, packaging, lockfiles |
| `chore` | Maintenance work that does not affect runtime behavior |
| `config` | Changes configuration files or environment setup |
| `ci` | Changes CI-related files if the project has CI |
| `merge` | Records a deliberate merge commit |
| `revert` | Reverts a previous commit |
| `checkpoint` | Local safe snapshot during a larger task, only when useful |

### Scope Rules

The scope should be a short technical area:

```text
feat(parser): add markdown homework extraction
```

```text
fix(ui): prevent duplicate submit clicks
```

```text
docs(agents): add local git workflow rules
```

If no useful scope exists, omit it:

```text
chore: update project metadata
```

### Bad Commit Messages

```text
fix
```

```text
updates
```

```text
changes
```

```text
work in progress
```

```text
final version
```

### Good Commit Messages

```text
fix(auth): validate empty login form

- Add client-side validation for empty email and password fields
- Show inline error messages without submitting the form
- Keep submit button disabled while validation fails

Verification:
- npm run test: passed
- Manual check: empty form no longer submits
```

```text
refactor(transcription): isolate audio preprocessing

- Move normalization logic into audio_preprocessing.py
- Keep Whisper backend interface unchanged
- Add unit tests for quiet audio normalization

Verification:
- uv run pytest tests/audio: passed
```

```text
docs(git): add local agent commit policy

- Define detached HEAD checks before commits
- Add local-only branch and merge rules
- Document conflict review workflow with subagents

Verification:
- Manual review: Markdown structure and command blocks checked
```

## Pre-Commit Workflow

The agent must follow this workflow before every commit.

### Step 1: Confirm Branch Safety

```bash
git symbolic-ref --quiet --short HEAD
```

If detached, stop.

### Step 2: Review Status

```bash
git status --short
```

### Step 3: Review Diff

```bash
git diff
```

### Step 4: Check for Whitespace or Conflict Markers

```bash
git diff --check
```

Search for unresolved conflict markers:

```bash
grep -RniE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.git
```

If conflict markers are found, do not commit.

### Step 5: Run Project Verification

Use project-specific commands from `README.md`, `AGENTS.md`, `package.json`, `pyproject.toml`, or project documentation.

Common examples:

```bash
npm test
```

```bash
npm run lint
```

```bash
npm run build
```

```bash
uv run pytest
```

```bash
python -m pytest
```

If verification is impossible, the agent must say why.

### Step 6: Stage Only Intended Files

```bash
git add path/to/file1 path/to/file2
```

### Step 7: Review Staged Diff

```bash
git diff --staged
```

The staged diff must match the task.

### Step 8: Commit

```bash
git commit
```

Use a multi-line commit message following the standard above.

Do not use `git commit -am` unless all tracked modifications are known and intended.

Do not use `git commit --allow-empty` unless explicitly creating a marker commit with human approval.

## Commit Granularity Rules

Prefer small, coherent commits.

A commit should represent one logical change.

Good commit boundaries:

- one bug fix;
- one feature slice;
- one refactor with no behavior change;
- one documentation update;
- one test addition;
- one configuration change.

Bad commit boundaries:

- unrelated UI, backend, docs, and dependency changes together;
- formatting mixed with behavior changes;
- bug fix mixed with speculative future architecture;
- generated files mixed with source changes unless expected;
- user edits mixed with agent edits.

If a task naturally produces multiple logical changes, the agent should propose multiple commits.

## Checkpoint Commits

In local-only projects, checkpoint commits may be useful during large work.

Use `checkpoint` only when:

- the task is large;
- the current state is useful and recoverable;
- tests may not be final yet;
- the human wants safe local snapshots;
- the branch is not a protected production branch.

Example:

```text
checkpoint(parser): save initial extraction pipeline

- Add first working parser structure
- Keep output format temporary and undocumented
- Preserve current state before refactoring validation

Verification:
- Manual run on sample input: completed
- Full tests: not yet applicable
```

Do not create checkpoint commits on `main` or `master` unless explicitly allowed.

## History Investigation Rules

When the agent needs to understand previous decisions, it should inspect history before changing code.

### Recent History

```bash
git log --oneline --decorate --graph -n 30
```

### File History

```bash
git log --follow -- path/to/file
```

### Commit Details

```bash
git show <commit-hash>
```

### Line-Level History

```bash
git blame path/to/file
```

Use blame carefully. It is for understanding context, not assigning fault.

### Search Commit Messages

```bash
git log --oneline --grep="keyword"
```

### Search Code Changes

```bash
git log -S"function_or_text" -- path/to/file
```

### Compare Branches

```bash
git log --oneline --left-right --cherry-pick branchA...branchB
```

```bash
git diff branchA...branchB
```

The agent should summarize findings before making changes if history affects the implementation plan.

## Branch Workflow

The repository may use any branch strategy. The agent must infer the current strategy from branch names and recent history, but must not guess silently.

Common local branch patterns:

```text
main
```

```text
develop
```

```text
feature/<short-name>
```

```text
fix/<short-name>
```

```text
refactor/<short-name>
```

```text
docs/<short-name>
```

```text
experiment/<short-name>
```

```text
recovery/<short-name>
```

### Creating a Branch

Before creating a branch, check the current branch and status:

```bash
git branch --show-current
```

```bash
git status --short
```

Create a new branch only when appropriate:

```bash
git switch -c feature/<short-name>
```

Do not create unnecessary branches for tiny changes unless the project convention requires it.

### Switching Branches

Before switching branches:

```bash
git status --short
```

If there are uncommitted changes, stop and ask how to proceed.

Options to propose:

1. commit the changes;
2. stash the changes;
3. stay on the current branch;
4. discard changes, only with explicit approval.

### Stashing

Stashing is allowed only when the agent clearly explains what is being stashed.

```bash
git stash push -m "agent: <reason>"
```

List stashes:

```bash
git stash list
```

Inspect a stash:

```bash
git stash show -p stash@{0}
```

Do not drop a stash without explicit approval.

```bash
git stash drop stash@{0}
```

## Merge Workflow

Merges are risky. The agent must use a conservative process.

### Merge Preflight

Before merging, identify:

- current target branch;
- source branch to merge from;
- whether working tree is clean;
- whether the target branch is protected by convention;
- whether the merge is fast-forward, no-fast-forward, or conflict-prone.

Commands:

```bash
git branch --show-current
```

```bash
git status --short
```

```bash
git log --oneline --decorate --graph -n 30
```

```bash
git log --oneline --left-right --cherry-pick HEAD...<source-branch>
```

```bash
git diff --name-status HEAD...<source-branch>
```

If the target branch is `main`, `master`, or another important branch, the agent must ask for confirmation before merging unless the user explicitly requested that exact merge.

### Safety Branch Before Risky Merge

For a risky merge, create a local safety branch before merging.

```bash
git branch safety/pre-merge-<target>-<short-date>
```

This preserves the previous target state.

Use this especially before:

- merging into `main` or `develop`;
- merging a large feature branch;
- resolving conflicts;
- applying many changes from another branch;
- working in a repo with unclear history.

### Performing the Merge

Default safe merge command:

```bash
git merge <source-branch>
```

Use `--no-ff` only if the project prefers explicit merge commits:

```bash
git merge --no-ff <source-branch>
```

Do not rebase instead of merging unless the human explicitly requests rebase.

### Merge Commit Message

Use:

```text
merge(<target>): merge <source-branch> into <target-branch>

- Bring in <main feature/fix area>
- Resolve <conflict area if any>
- Preserve <important behavior>

Verification:
- <command>: <result>
```

## Conflict Resolution Rules

When conflicts occur, the agent must slow down and treat the operation as high-risk.

### Immediate Conflict Steps

Run:

```bash
git status
```

List conflicted files:

```bash
git diff --name-only --diff-filter=U
```

Inspect conflict sections:

```bash
git diff
```

The agent must explain:

- which files conflict;
- what each side appears to be changing;
- which branch is `ours`;
- which branch is `theirs`.

During a merge:

```text
ours = current target branch
```

```text
theirs = incoming source branch
```

The agent must not use `git checkout --ours`, `git checkout --theirs`, `git restore --ours`, or `git restore --theirs` blindly.

These commands may be used only when the agent explains why one side is entirely correct for a specific file and the human approves or the project instructions explicitly allow it.

### Conflict Resolution Principles

Resolve conflicts by preserving intent from both sides when possible.

The agent must:

- read the surrounding code;
- understand both versions;
- preserve behavior from both branches if compatible;
- update tests when needed;
- avoid deleting logic silently;
- avoid mixing unrelated refactors into conflict resolution;
- run verification after resolution.

### Conflict Marker Check

Before staging resolved conflicts:

```bash
grep -RniE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.git
```

If markers remain, do not commit.

### Stage Resolved Files Explicitly

```bash
git add path/to/resolved-file
```

Then review staged resolution:

```bash
git diff --staged
```

### Abort When Unsure

If the agent cannot confidently resolve conflicts, it must stop.

Safe abort command during an active merge:

```bash
git merge --abort
```

Before running it, the agent should explain that it returns the repository to the pre-merge state if possible.

If `git merge --abort` fails, the agent must not improvise with destructive commands. It must report the error and ask for human guidance.

## Subagent Self-Review Rules

For high-risk Git operations, the primary coding agent should use subagents or separate review passes when available.

High-risk operations include:

- merging branches;
- resolving conflicts;
- committing large diffs;
- changing architecture;
- modifying generated files;
- editing lockfiles;
- changing migration files;
- touching deployment or packaging scripts;
- working on `main`, `master`, or `develop` directly.

### Required Review Roles

If the environment supports subagents, use these review roles.

#### Git State Auditor

Purpose:

- verify current branch;
- detect detached HEAD;
- inspect working tree;
- identify unrelated changes;
- identify staged vs unstaged changes;
- verify whether a merge or rebase is in progress.

Checklist:

```text
- Is this a Git repository?
- Is HEAD attached to a branch?
- What is the current branch?
- Are there uncommitted changes?
- Are there unrelated user changes?
- Is a merge/rebase/cherry-pick in progress?
- Is the intended commit branch correct?
```

#### Diff Reviewer

Purpose:

- inspect the staged diff;
- check whether the diff matches the task;
- detect accidental changes;
- detect missing files;
- detect debug code, secrets, or generated noise.

Checklist:

```text
- Does the staged diff match the task?
- Are there unrelated changes?
- Are there accidental formatting-only changes mixed with logic?
- Are secrets, tokens, local paths, or personal data included?
- Are generated files expected?
- Is the commit size reasonable?
```

#### Conflict Resolution Reviewer

Purpose:

- independently review resolved conflicts;
- check that both branch intents were preserved;
- check that no conflict markers remain;
- check that tests or manual verification cover the resolution.

Checklist:

```text
- Which files had conflicts?
- Was the meaning of ours/theirs correctly understood?
- Was any logic deleted?
- Were both branches' intended changes preserved where appropriate?
- Are conflict markers gone?
- Was verification run after resolution?
```

#### Commit Message Reviewer

Purpose:

- verify that the commit message is accurate;
- ensure the type prefix is correct;
- ensure bullet points mention concrete changes;
- ensure verification results are included.

Checklist:

```text
- Does the type match the change?
- Is the scope meaningful?
- Is the summary concise and imperative?
- Do bullets describe actual changes?
- Does the message include verification?
- Does the message avoid vague wording?
```

### If Subagents Are Not Available

If the environment does not support subagents, the primary agent must perform the same checks as separate self-review passes and explicitly report the results.

Example:

```text
Self-review completed:
- Git State Auditor: passed
- Diff Reviewer: passed
- Commit Message Reviewer: passed
```

## Merge With Subagent Review Workflow

For merges, use this sequence.

### Step 1: Primary Agent Preflight

Run the merge preflight commands and summarize the situation.

### Step 2: Git State Auditor Review

Ask the Git State Auditor to verify that the repository is safe for merge.

Required output:

```text
Safe to merge: yes/no
Risks:
- ...
Required human confirmation: yes/no
```

### Step 3: Create Safety Branch If Needed

For risky merges:

```bash
git branch safety/pre-merge-<target>-<short-date>
```

### Step 4: Perform Merge

```bash
git merge <source-branch>
```

### Step 5: If Conflicts Occur

Stop normal work and switch to conflict workflow.

Use Conflict Resolution Reviewer after resolving conflicts.

### Step 6: Verify

Run project verification commands.

### Step 7: Diff Reviewer

Review the final diff or merge result.

### Step 8: Commit Message Reviewer

Check the merge commit message if a merge commit is needed.

### Step 9: Final Report

Report:

```text
Merge completed.
Target branch: <target>
Source branch: <source>
Safety branch: <name or none>
Conflicts: <yes/no>
Verification: <commands and results>
Commit: <hash if created>
Remaining risks: <none or list>
```

## Recovery Rules

If something goes wrong, prioritize preserving data over finishing the task.

### Inspect State First

```bash
git status
```

```bash
git log --oneline --decorate --graph -n 20
```

```bash
git reflog -n 20
```

Do not move branches or reset commits until the state is understood.

### Safe Recovery Actions

Generally safe actions:

```bash
git merge --abort
```

```bash
git rebase --abort
```

```bash
git cherry-pick --abort
```

Only use the abort command that matches the active operation.

### Reflog Recovery

`git reflog` can help find previous states, but moving branches back to reflog entries is history-changing and must require explicit human approval.

Do not run:

```bash
git reset --hard HEAD@{n}
```

unless the human explicitly approves after seeing the consequences.

### Emergency Backup

If the working tree contains valuable uncommitted changes and the state is confusing, create a patch before doing anything destructive.

```bash
git diff > emergency-working-tree.patch
```

For staged changes:

```bash
git diff --staged > emergency-staged.patch
```

For untracked files, list them first:

```bash
git ls-files --others --exclude-standard
```

Do not delete untracked files without approval.

## Final Report After Git Operations

After committing, merging, or resolving conflicts, the agent must report:

```text
Git operation completed.

Branch:
- Current branch: <branch>
- Detached HEAD: no

Commit:
- Hash: <short-hash>
- Message: <first line>

Files changed:
- <file 1>: <summary>
- <file 2>: <summary>

Verification:
- <command>: <passed/failed/not run + reason>

History notes:
- <anything important from branch/merge/history context>

Risks or follow-up:
- <none or list>
```

Get the latest commit hash:

```bash
git rev-parse --short HEAD
```

Show the last commit:

```bash
git show --stat --oneline HEAD
```

## Minimal AGENTS.md Git Section

This section can be copied into a project-level `AGENTS.md`.

```text
## Git Rules for Agents

This is a local-only Git repository unless a remote is explicitly configured.

Before committing, the agent must:
- confirm this is a Git repository;
- confirm HEAD is attached to a branch;
- report the current branch;
- inspect `git status --short`;
- inspect `git diff` and `git diff --staged`;
- avoid staging unrelated user changes;
- run relevant verification commands;
- use a structured commit message.

The agent must not:
- commit from detached HEAD;
- rewrite history without approval;
- run destructive commands without approval;
- use `git add .` unless the diff is known and safe;
- resolve conflicts by blindly choosing ours/theirs;
- switch branches with uncommitted changes without approval.

Commit message format:

<type>(<scope>): <short imperative summary>

- <specific change 1>
- <specific change 2>
- <specific change 3>

Verification:
- <command or manual check>: <result>

For risky merges or conflict resolution, use a separate review pass or subagent to check Git state, final diff, conflict resolution, and commit message.
```

## Ready-to-Use Prompt for a Coding Agent

Use this prompt when asking an AI coding agent to work with local Git history.

```text
You are a coding agent working in a local-only Git repository.

You must preserve clean, understandable, recoverable Git history.

Before making commits, merges, branch switches, or history-related decisions:
1. Confirm this is a Git repository.
2. Confirm HEAD is attached to a real branch.
3. Report the current branch.
4. Inspect working tree status.
5. Inspect unstaged and staged diffs.
6. Identify unrelated user changes and avoid touching them.
7. Run relevant verification commands when possible.

Never commit from detached HEAD.
Never rewrite history without explicit human approval.
Never run destructive Git commands without explicit human approval.
Never use broad staging unless the repository is clean and the diff is fully understood.
Never resolve conflicts blindly with ours/theirs.

Use structured commit messages:

<type>(<scope>): <short imperative summary>

- <specific change 1>
- <specific change 2>
- <specific change 3>

Verification:
- <command or manual check>: <result>

Allowed types:
feat, fix, refactor, docs, test, style, perf, build, chore, config, ci, merge, revert, checkpoint.

If there is any doubt about branch target, detached HEAD state, conflict resolution, staged files, unrelated changes, or destructive commands, stop and warn the human before proceeding.

For high-risk operations such as merges, conflict resolution, large commits, lockfile changes, migrations, or changes on main/master/develop, use subagents or separate self-review passes:
- Git State Auditor
- Diff Reviewer
- Conflict Resolution Reviewer
- Commit Message Reviewer

At the end of every Git operation, report:
- current branch;
- commit hash, if created;
- files changed;
- verification commands and results;
- remaining risks or follow-up items.
```

## Final Self-Check for the Agent

Before saying the Git work is complete, answer:

```text
- Am I on a real branch, not detached HEAD?
- Did I commit only intended changes?
- Did I avoid user changes?
- Did I inspect the staged diff?
- Did I run verification or explain why not?
- Is the commit message specific and useful?
- Did I avoid destructive commands?
- Did I warn the human about all risks?
- If a merge/conflict happened, was it reviewed separately?
- Can the human understand the project history from this commit later?
```

If any answer is no, do not finalize the Git operation until the issue is resolved or reported.
