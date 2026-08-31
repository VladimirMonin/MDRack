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

MDRack is an agent-first local retrieval tool. It indexes selected Markdown and
prepared transcript or frame-caption text into one local store and returns
structured retrieval results. It can use a local LM Studio HTTP endpoint for
embeddings, but it never loads model weights itself.

This skill does not add commands, change output formats, publish packages, or
claim transcription, pixel analysis, or acoustic search. It does not inspect
source files other than the material the user explicitly selected for indexing.

## License boundary

MDRack is standard MIT software; its exact commercial-use and notice-retention
policy is in [`docs/licensing.md`](../../docs/licensing.md). The locked Python
runtime inventory is [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).
Resolver-installed dependencies are not relicensed by MDRack and are not bundled
by the four base Python wheel/sdist distributions. A self-contained Windows EXE
needs a separate exact bundle manifest and upstream notices before it is called
release-ready.

## Safety contract

- Ask for, or require, an explicit project root. Do not infer a private vault,
  home directory, or current working tree as the user's data root.
- Keep human data outside the MDRack checkout. Require an explicit selected source
  for each non-Markdown input; do not scan a repository or home directory by
  implication.
- Ask for an explicit configuration path when one is needed. Otherwise use the
  CLI's documented root-relative configuration discovery.
- Begin with read-only `guide`, `--help`, `status`, `doctor`, or text `search`.
- Initialization, scanning, rebuilding, importing, deletion, image operations,
  and model/provider lifecycle actions are state-changing. Run them only when
  the user explicitly requests that operation and names the intended root.
- Keep secrets, endpoint credentials, query text, document contents, and
  private paths out of reports and logs.
- Treat source files as read-only. MDRack may create or replace derived local state,
  but the agent must not use it to alter selected source bytes.
- Use only the registered `mdrack` CLI. Do not invent flags, alternate stores,
  database inspection commands, provider calls, or hidden model operations.

## First response

1. Identify the checkout or installed command (`uv run mdrack` for a source
   checkout, `mdrack` for an installed command). Do not call checkout evidence an
   installed-package run.
2. Ask the user to confirm `ROOT` and, if relevant, `CONFIG_FILE`.
3. Run a harmless guide or help command.
4. Before a write, state which command will create or change derived state.
5. Report the exact command result and distinguish local/offline checks from
   provider-backed behavior.

## Source checkout setup

The committed lockfile and hosted release matrix are validated with
`uv 0.11.15`. From the repository root, verify the executable before changing
the environment. If another uv version is active, do not refresh `uv.lock` or
claim release parity; use the project-pinned version first.

```text
uv --version
uv lock --check
uv sync --all-extras --frozen
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

## Authorized human-data workflow

Use this only after the user names `ROOT`, each selected source, and the requested
mutation. Keep private sources outside the checkout and substitute no real values
into commands, reports, or Git material.

1. Markdown: `scan` reads the user-selected root. Do not use it to inspect an
   unrelated repository or private home directory.
2. Text or Markdown: explicitly ingest one UTF-8 source with a portable reference:

   ```text
   uv run mdrack --root ROOT ingest text SOURCE_PATH --source-ref PORTABLE_REF --media-type text/markdown
   ```

3. Direct image: supply a caller-owned identity, portable reference, and text
   representation. Do not treat this command as built-in pixel analysis:

   ```text
   uv run mdrack --root ROOT image ingest IMAGE_PATH --resource-id RESOURCE_ID --source-namespace NAMESPACE --source-ref PORTABLE_REF --caption "caller-supplied text"
   ```

4. WAVE and ISO-BMFF: these run only a caller-selected local adapter after the
   matching permission flag is present. State the executable and expected derived
   change before execution; do not silently select an adapter or provider:

   ```text
   uv run mdrack --root ROOT ingest audio WAVE_PATH --source-ref PORTABLE_REF --allow-external-stt --stt-command EXECUTABLE
   uv run mdrack --root ROOT ingest raw-video VIDEO_PATH --source-ref PORTABLE_REF --allow-external-video-extractor --video-extractor-command EXECUTABLE
   ```

   These shell-free stdin paths do not prove transcription, decoding, visual or
   acoustic quality, Windows behavior, or live-provider behavior.

5. Search and read the public result without quoting its private values in a
   report. Prefer text mode for provider-free retrieval and scope the search when
   useful:

   ```text
   uv run mdrack --root ROOT search QUERY --mode text --scope all
   uv run mdrack --root ROOT read chunk LOGICAL_ID --context neighbors
   uv run mdrack --root ROOT read outline FILE_LOGICAL_ID
   ```

   Keep logical IDs, portable locators, and available line/time evidence only.
6. After any requested mutation, start a fresh process with the same command
   boundary and root, then run:

   ```text
   uv run mdrack --root ROOT status
   ```

   This checks that the same derived store can be reopened and reports aggregate
   state. Separately confirm that source bytes remain unchanged.

A real acceptance claim requires this workflow on separately authorized human-like
private data through the installed public CLI or engine. Fake providers, synthetic
fixtures, and offline checks are supporting evidence only and do not establish that
claim.

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
provide built-in transcription, decoding, pixel/visual, or acoustic search.
For an explicitly authorized local adapter, use the registered WAVE and ISO-BMFF
paths (the commands invoke caller-selected shell-free stdin tools):

```text
uv run mdrack ingest audio SOURCE_PATH --source-ref REF --allow-external-stt --stt-command COMMAND
uv run mdrack ingest raw-video SOURCE_PATH --source-ref REF --allow-external-video-extractor --video-extractor-command COMMAND
```

These commands are state-changing and do not claim provider quality, Windows,
or real-source coverage. Use their live `--help` output before proposing a
recipe; image and prepared-media commands remain separate boundaries.

## External local STT: Voiceover Pipeline

For an explicitly authorized WAVE ingest, an agent may use the independently
installed Voiceover Pipeline as the caller-selected external STT executable.
MDRack does not bundle its models, weights, runtime, cache, or provider. First
check the current local registry and the selected offline boundary without model
inference:

```text
voiceover list asr-providers --json
voiceover doctor --with-asr --asr-provider nemotron-local --asr-device cpu --asr-compute auto --json
```

The inventory must include `nemotron-local` with
`nvidia/nemotron-3.5-asr-streaming-0.6b`; use only its already-downloaded local
model/cache. Never install, download, use cloud, or silently select another
provider. The doctor result must make the selected local workflow available; a
missing runtime/model/cache is a local prerequisite, not permission to fetch it.

The agent creates a temporary shell-free stdin wrapper outside the checkout and
selected root. It accepts exactly one WAVE stream on stdin, writes a restricted
temporary WAVE file, invokes Voiceover Pipeline with an argv list (not a shell),
then removes every temporary file in `finally`. Its primary invocation is:

```text
voiceover transcribe --audio TEMP_WAVE --provider nemotron-local --model nvidia/nemotron-3.5-asr-streaming-0.6b --language ru --device cpu --compute auto --word-timestamps --runtime auto --json
```

The wrapper accepts exactly one successful VOP JSON object, requires usable
timed segments or words, and emits only strict `mdrack.timed-transcript.v1` JSON
to stdout (`atoms` with integer `start_ms`, `end_ms`, and text). It rejects
text-only or invalid timing output rather than inventing timings or falling back.
It must not print VOP stderr, source paths, source bytes, prompts, or temporary
names to MDRack's stdout or report.

Nemotron may receive model-owned language/task conditioning through `--language`;
do not pass `--context`, phrase hints, a prompt, or a glossary to that route.
When caller-authorized contextual text is truly required, choose local
`qwen-local` deliberately, preflight it first, and use its supported `--context`
or `--context-file` with `Qwen/Qwen3-ASR-0.6B`. Qwen is not an automatic
fallback, and it must still return valid timings for the wrapper to emit strict
MDRack JSON.

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
- [ ] Each selected source and every external adapter were explicitly authorized.
- [ ] A fresh `status` invocation checked the same derived store after a mutation.
- [ ] Source bytes remained unchanged.
- [ ] No secrets or user content entered the report.
- [ ] The result states what ran, what changed, and what was not tested.
