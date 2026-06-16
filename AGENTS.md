# AGENTS.md — Project Agent Rules

## Project Overview

MDRack is a local command-line Markdown knowledge rack for AI agents.

It indexes Markdown files, splits them into meaningful structural chunks, stores
metadata and search indexes in SQLite, creates embeddings through LM Studio only,
and allows agents to search, inspect, and retrieve document context through
stable JSON commands.

Python package manager: **uv** — always use `uv run`, `uv sync`, `uv run pytest`.

---

## Mode Awareness

OpenCode sends a **system reminder** at the top of every message. It explicitly
states the current mode:

| System reminder says | Mode | Behaviour |
|---|---|---|
| `Plan mode ACTIVE` / `READ-ONLY` | **Plan Mode** | Read-only. No file edits. Explore, investigate, design, decompose. |
| `Build mode` / no read-only restriction | **Build Mode** | File edits allowed. Implement, fix, refactor, commit. |

When you see the mode change in the system reminder, switch your workflow
accordingly:

- **Plan Mode** → follow `WORKFLOW.plan-orchestration.instructions.md`
- **Build Mode** → follow `WORKFLOW.build-orchestration.instructions.md`

If unsure which mode you are in, re-read the system reminder at the top of the
current message.

---

## Quick Decision Table

Match the user's first request against this table. Load the corresponding
instruction **before** taking action.

| User asks to... | Load instruction | Priority |
|---|---|---|
| Plan, investigate, explore, estimate, architect, review structure, decompose tasks | `WORKFLOW.plan-orchestration` | on-trigger |
| Implement, write code, fix bug, refactor, add feature, change UI, add tests | `WORKFLOW.build-orchestration` | on-trigger |
| Commit, merge, switch branch, resolve conflicts, any Git operation | `WORKFLOW.git-safety` | on-trigger |
| Turn requirements/idea into a technical specification | `WORKFLOW.requirements-to-spec` | on-trigger |
| Add logging, fix log output, audit logs, any logging question | `OBS.logging` | always |
| Create or edit an instruction file (AGENTS.md, anything under `instructions/`) | `DOCS.instruction-style-guide` | on-trigger |
| Describe an image, visual check, screenshot comparison via MCP | `TOOLING.mcp-instrument` | on-trigger |

**Always loaded** instructions apply to every session regardless of mode.
**On-trigger** instructions load only when the user's request matches.

---

## Instruction Index

### `/instructions/WORKFLOW.plan-orchestration.instructions.md`

**What it governs:** Planning mode workflow — how the main session delegates
read-only exploration to subagents, collects evidence, synthesises a build plan.

**When to load:** User asks to investigate, plan, architect, estimate, decompose,
or review before implementation. Also load when the system reminder says
`Plan mode ACTIVE`.

**Priority:** on-trigger (Plan Mode)

---

### `/instructions/WORKFLOW.build-orchestration.instructions.md`

**What it governs:** Build mode workflow — how the main session assigns
narrow implementation tasks to subagents, runs QA review loops, handles
verification, screenshots, Git operations, and final reporting.

**When to load:** User asks to implement, fix, refactor, add tests, change UI,
or any code/behaviour change. Also load when the system reminder switches
to Build Mode.

**Priority:** on-trigger (Build Mode)

---

### `/instructions/WORKFLOW.git-safety.instructions.md`

**What it governs:** All Git operations — detached HEAD detection, branch safety,
commit message format, staging policy, merge workflow, conflict resolution,
recovery rules.

**When to load:** User asks to commit, merge, switch branch, resolve conflicts,
or any operation that touches Git history.

**Priority:** on-trigger

---

### `/instructions/WORKFLOW.requirements-to-spec.instructions.md`

**What it governs:** Transforming a human idea or loose requirements into a
complete, implementation-ready technical specification (`docs/SPEC.md`).

**When to load:** User provides a project idea or requirements and asks for a
specification, or when `docs/SPEC.md` needs to be created/re-generated.

**Priority:** on-trigger

---

### `/instructions/OBS.logging.instructions.md`

**What it governs:** All logging across the project — event naming, structured
fields, privacy-safe sanitisation, GUI/CLI audit trails, error logging,
credential safety, diagnostics exports.

**When to load:** **Always.** Every code change must respect logging and privacy
rules. Also load explicitly when user asks about logging.

**Priority:** always

---

### `/instructions/DOCS.instruction-style-guide.instructions.md`

**What it governs:** How to create, update, split, name, and maintain instruction
files. Defines prefix canon, naming conventions, quality checklist.

**When to load:** User asks to create or edit any file under `instructions/`,
or to update `AGENTS.md` itself.

**Priority:** on-trigger

---

### `/instructions/TOOLING.mcp-instrument.instructions.md`

**What it governs:** The `polza-vision` MCP tool — how to describe images
through the polza.ai vision API for visual checks.

**When to load:** User asks for visual verification, screenshot review,
or image description.

**Priority:** on-trigger

---

## Language and Communication

- **Speak to the user in their language.** If the user writes in Russian,
  reply in Russian. Match the language of the conversation.
- **Keep it simple.** Avoid anglicisms, jargon, and specialist terminology
  unless the user uses them first.
- **Be direct and brief.** Answer the question, then stop. Do not add
  unnecessary explanations, introductions, or conclusions unless asked.
- **Technical identifiers** (file names, commands, class names, module paths)
  remain in English. Only human conversation adapts.

---

## Project Rules

- Follow the task order in `docs/plan.md` if it exists. Otherwise, follow the
  build plan from the planning workflow.
- Do not add features outside the defined MVP scope.
- Do not introduce new runtime dependencies without a documented reason.
- Do not delete files unless explicitly required.
- Do not commit secrets, paths, or user data.
- Do not add GUI, web server, or MCP server.
- Do not add Qdrant, LanceDB, Chroma, or any specialized vector database.
- Do not add `torch`, `transformers`, or `sentence-transformers`.
- Do not load embedding models directly in Python; use LM Studio HTTP API only.
- Keep SQLite as the only persistent database.
- Do not modify source Markdown files during indexing.
- Code style: PEP 8 + type hints everywhere; no `# noqa` without reason.
- Logging: use Python `logging` module with structured fields; do not `print`
  to stdout in production code. See `OBS.logging.instructions.md` for all
  logging and privacy rules.
- Package manager: **uv** for all Python operations.
  - Install: `uv sync --all-extras`
  - Run tests: `uv run pytest`
  - Run scripts: `uv run python path/to/script.py`

---

## Verification

Run in order, all must pass:

- `uv run pytest`                              # unit tests
- `uv run ruff check src/ tests/`               # lint
- `uv run python scripts/check_no_forbidden_deps.py`  # verify no ML deps

---

## Updating Docs

- If a technical specification exists (`docs/plan.md`), update it when you
  change architecture, scope, schema, or verification commands.
- If you change the schema, bump `schema_version` and add a migration note.
- If you change a verification command, update the "Verification" section above.
- If you add or rename an instruction file, update the "Instruction Index"
  and "Quick Decision Table" sections above.

---

## Git

This is a local-only repository unless a remote is explicitly configured.

### Proactive Initialization

When starting work on a project that has no Git repository yet, **initialize Git
immediately** — do not wait for the user to ask. Create an initial commit with
existing project files before beginning any code changes. This protects against
data loss and provides a safe rollback point.

### Rules

Before committing, the agent must:
- confirm this is a Git repository (`git rev-parse --is-inside-work-tree`);
- if not a repo, **initialize it** (`git init`) and make an initial commit;
- confirm HEAD is attached to a branch (not detached);
- report the current branch;
- inspect `git status --short` and `git diff`;
- avoid staging unrelated user changes;
- run relevant verification commands when possible;
- use structured commit messages per `/instructions/WORKFLOW.git-safety.instructions.md`.

The agent must not:
- commit from detached HEAD;
- rewrite history without explicit approval;
- run destructive commands without explicit approval;
- use `git add .` unless the diff is completely known and safe;
- resolve conflicts by blindly choosing ours/theirs;
- switch branches with uncommitted changes without approval.

Full rules: `/instructions/WORKFLOW.git-safety.instructions.md`.
