---
name: mdrack
description: >
  Use when a student or agent needs to install, configure, inspect, or search an
  MDRack checkout from the terminal. Triggers: MDRack, mdrack, Markdown search,
  local notes index, mdrack guide, mdrack status, mdrack doctor.
---

# MDRack command-line skill

Read this file completely. Читай этот файл целиком. This skill describes the
supported, local, text-first workflow for the MDRack CLI.

## Scope

MDRack indexes supplied Markdown and prepared transcript or frame-caption text
into one local store and returns structured retrieval results. It can use a
local LM Studio HTTP endpoint for embeddings, but it never loads model weights
itself.

This skill does not add commands, change output formats, publish packages, or
claim transcription, pixel analysis, or acoustic search. It does not inspect
source files other than the material the user explicitly selected for indexing.

## Safety contract

- Ask for, or require, an explicit project root. Do not infer a private vault,
  home directory, or current working tree as the user's data root.
- Ask for an explicit configuration path when one is needed. Otherwise use the
  CLI's documented root-relative configuration discovery.
- Begin with read-only `guide`, `--help`, `status`, `doctor`, or text `search`.
- Initialization, scanning, rebuilding, importing, deletion, image operations,
  and model/provider lifecycle actions are state-changing. Run them only when
  the user explicitly requests that operation and names the intended root.
- Keep secrets, endpoint credentials, query text, document contents, and
  private paths out of reports and logs.
- Use only the registered `mdrack` CLI. Do not invent flags, alternate stores,
  database inspection commands, provider calls, or hidden model operations.

## First response

1. Identify the checkout or installed command (`uv run mdrack` for a source
   checkout, `mdrack` for an installed command).
2. Ask the user to confirm `ROOT` and, if relevant, `CONFIG_FILE`.
3. Run a harmless guide or help command.
4. Before a write, state which command will create or change derived state.
5. Report the exact command result and distinguish local/offline checks from
   provider-backed behavior.

## Source checkout setup

From the repository root:

```text
uv sync --all-extras
uv run mdrack --help
uv run mdrack guide
```

The supported public source is the repository checkout. Do not claim that a
wheel, registry package, or public skill index is available unless it has been
independently verified.

## Read-only inspection

Replace `ROOT` with the directory the user selected. These commands do not
index documents:

```text
uv run mdrack --root ROOT guide quickstart
uv run mdrack --root ROOT status
uv run mdrack --root ROOT doctor
uv run mdrack --root ROOT search QUERY --mode text
```

Text mode is the provider-free search path. Quote `QUERY` in shells when it
contains spaces or shell metacharacters. Use `mdrack search --help` for the
current options rather than copying historical documentation.

PowerShell equivalents use the same CLI and quoting rules:

```powershell
uv run mdrack --root .\notes guide quickstart
uv run mdrack --root .\notes status
uv run mdrack --root .\notes doctor
uv run mdrack --root .\notes search 'architecture' --mode text
```

## Explicit local workflow

Only after the user asks to create or refresh an index, use a named root:

```text
uv run mdrack --root ROOT init
uv run mdrack --root ROOT scan --provider fake
uv run mdrack --root ROOT search QUERY --mode text
uv run mdrack --root ROOT status
```

The fake provider is an offline verification choice, not evidence of semantic
quality. A normal scan may use the configured local provider; say so before it
runs. Never modify the Markdown source as part of indexing.

For PowerShell:

```powershell
uv run mdrack --root .\notes init
uv run mdrack --root .\notes scan --provider fake
uv run mdrack --root .\notes search 'architecture' --mode text
uv run mdrack --root .\notes status
```

## Configuration and prepared media

Read the current built-in guidance first:

```text
uv run mdrack guide configuration
uv run mdrack guide search
uv run mdrack guide media
uv run mdrack model --help
```

MDRack accepts caller-supplied transcript and frame-caption text. It does not
turn raw audio, video, or image pixels into searchable text. Treat image and
prepared-media commands as explicit state-changing operations and use their
live `--help` output before proposing a recipe.

## Failure handling

- If the root is missing or ambiguous, stop and request a concrete path.
- If configuration fails, report only the stable CLI error and suggest
  `mdrack guide configuration`; do not print configuration contents.
- If a command is not registered, do not approximate it. Run the relevant
  `--help` command and revise the recipe to match the live CLI.
- If a provider is unavailable, offer text mode or the documented offline fake
  path; do not silently switch providers.
- After any requested mutation, run `status` and report what was verified.

## Completion checklist

- [ ] Explicit root and any config path were confirmed.
- [ ] The command path was checked against live `mdrack --help` output.
- [ ] Read-only was the default, or the requested mutation was named first.
- [ ] No secrets or user content entered the report.
- [ ] The result states what ran, what changed, and what was not tested.
