# Getting started with MDRack 1.3

MDRack is a local Python 3.11+ CLI and embedded library. It indexes supplied
Markdown into SQLite and can use LM Studio over HTTP for embeddings. The
committed base-candidate checkpoint is
`a796a1ab55bbf18ae8c62618502b2b0dda929431`
(`fix(release): close v1.3 source identity`); its release packet records the
SHA-256 manifest
`c257c816df7bf3f65fefb709f987fcb5472915bda6f04aa436d9762e62e72868` over
626 tracked non-packet paths. That checkpoint is not itself a Git tag or
package-index publication. This guide therefore makes no installed-index claim
and uses a repository checkout.

## Install from the checkout

Install `uv`, clone the repository, and run:

```bash
uv sync --all-extras
uv run mdrack --version
uv run mdrack --help
uv run mdrack guide
```

`uv` resolves the application together with its pinned workspace distributions,
declared as `mdrack-core==1.0.0rc1`, `mdrack-media==1.0.0rc1`, and
`mdrack-sqlite==1.0.0rc2`. This is checkout evidence, not an installed-package
or package-index smoke. Do not use system `pip` for this checkout.

## Agent skill from this checkout

The repository ships one self-contained agent skill for the MDRack CLI. From a
source checkout, install it into a local skill directory with:

```bash
mkdir -p "$HOME/.hermes/skills/mdrack"
cp skills/mdrack/SKILL.md "$HOME/.hermes/skills/mdrack/SKILL.md"
```

When working from a fresh home without a checkout copy, the same single file
can be fetched from the repository's raw source URL:

```bash
mkdir -p "$HOME/.hermes/skills/mdrack"
curl --fail --location \
  https://raw.githubusercontent.com/VladimirMonin/MDRack/master/skills/mdrack/SKILL.md \
  --output "$HOME/.hermes/skills/mdrack/SKILL.md"
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.hermes\skills\mdrack" | Out-Null
Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/VladimirMonin/MDRack/master/skills/mdrack/SKILL.md" `
  -OutFile "$HOME\.hermes\skills\mdrack\SKILL.md"
```

The skill is self-contained and is invoked by an agent when the request is
about MDRack installation, configuration, status, diagnostics, or search. This
source checkout has not been externally smoke-tested from a fresh home here;
the URL is an installation path, not a claim of public registry publication.

## Static CLI guide

The pre-configuration guide is safe to run before a config file or store exists:

```bash
uv run mdrack guide quickstart
uv run mdrack guide configuration
uv run mdrack guide search
uv run mdrack guide media
```

It only prints help and does not load configuration, open SQLite, contact a
provider, or create `.mdrack` state.

## Offline quick start

The following smoke uses deterministic fake embeddings and does not contact LM
Studio. Use it to verify installation and the text-search workflow, not to claim
semantic quality.

```bash
mkdir -p ./notes
printf '# Architecture\n\nMDRack keeps local notes searchable.\n' > ./notes/example.md
uv run mdrack --root ./notes init
uv run mdrack --root ./notes scan --provider fake
uv run mdrack --root ./notes search architecture --mode text
uv run mdrack --root ./notes status
```

MDRack writes derived state under `./notes/.mdrack/`; it does not modify the
Markdown source. CLI success and error responses use one JSON envelope. See the
[CLI contracts](cli-contracts.md) for complete flags and response shapes.

## Agent workflow for authorized human data

This is the normal operational sequence when an agent helps a human search local
material. It is intentionally different from the synthetic quick start above.
The human selects a private data root and any individual source files; keep those
sources outside the repository checkout and do not put their contents, queries,
paths, configuration values, or generated state into a report or commit.

1. Identify the command boundary. In a source checkout use `uv run mdrack`; for a
   separately installed command use `mdrack`. Do not describe a checkout smoke as
   installed-package evidence.
2. Confirm `ROOT` and the requested operation. Begin with read-only discovery:

   ```bash
   uv run mdrack --root ROOT guide
   uv run mdrack --root ROOT --help
   uv run mdrack --root ROOT status
   ```

   The `guide` command prints static help before configuration exists. `status`
   reads existing derived state and does not create a store.
3. Before a mutation, state exactly what will change: MDRack may create or replace
   derived records under `ROOT/.mdrack/`, but it must not modify the selected
   source. Markdown scanning reads only the selected root. Raw text/Markdown,
   images, WAVE audio, and ISO-BMFF video are separate explicit input boundaries:

   ```bash
   uv run mdrack --root ROOT ingest text SOURCE_PATH --source-ref PORTABLE_REF --media-type text/markdown
   uv run mdrack --root ROOT image ingest IMAGE_PATH --resource-id RESOURCE_ID --source-namespace NAMESPACE --source-ref PORTABLE_REF --caption "caller-supplied text"
   uv run mdrack --root ROOT ingest audio WAVE_PATH --source-ref PORTABLE_REF --allow-external-stt --stt-command EXECUTABLE
   uv run mdrack --root ROOT ingest raw-video VIDEO_PATH --source-ref PORTABLE_REF --allow-external-video-extractor --video-extractor-command EXECUTABLE
   ```

   `PORTABLE_REF` is a public relative reference, not the source filesystem path.
   Raw text is captured from outside `ROOT`; the image command requires
   caller-supplied caption text. The WAVE and ISO-BMFF commands run only the
   caller-named local executable after their explicit authorization flags are
   present. They use shell-free stdin adapters and do not claim built-in
   transcription, video decoding, provider quality, or visual/acoustic search.
   Read the relevant live `--help` output before proposing a non-routine command.
4. Search and read the returned public evidence without copying private values into
   the agent's report. Text mode avoids embedding-provider calls; `--scope` limits
   unified retrieval to `all`, `notes`, `audio`, `video`, `frames`, or `images`.

   ```bash
   uv run mdrack --root ROOT search QUERY --mode text --scope all
   uv run mdrack --root ROOT read chunk LOGICAL_ID --context neighbors
   uv run mdrack --root ROOT read outline FILE_LOGICAL_ID
   ```

   Preserve logical IDs and portable source locators. Use the public record's
   available line or time bounds and timestamps as evidence rather than recording
   a private path or raw source text.
5. End the first command, then start a fresh CLI process with the same command
   boundary and root:

   ```bash
   uv run mdrack --root ROOT status
   ```

   This reopens the same fixed derived store and returns privacy-safe aggregate
   status. It is a useful continuity check, not a replacement for checking the
   original source remains unchanged.

An actual agent acceptance run requires the installed public CLI or engine to use
separately authorized human-like private data, with source immutability and report
privacy checked at the end. It is not performed by this guide. Deterministic fake
providers, synthetic fixtures, and local offline checks are supporting evidence;
they do not prove that real-use boundary.

### Authorized external local STT for WAVE

MDRack has no bundled speech runtime, model weights, model cache, or cloud
fallback. For an explicitly authorized WAVE source, an agent can select the
separately installed Voiceover Pipeline (VOP) as the local external processor.
First inspect VOP's current registry and the chosen local boundary without
running inference:

```bash
voiceover list asr-providers --json
voiceover doctor --with-asr --asr-provider nemotron-local --asr-device cpu --asr-compute auto --json
```

Use the already-downloaded `nemotron-local` model
`nvidia/nemotron-3.5-asr-streaming-0.6b` from VOP's configured local/offline
cache. A missing runtime, model, or cache is a local prerequisite: do not install,
download, use a cloud provider, or silently switch models.

The caller-named `--stt-command` is a temporary shell-free stdin wrapper, not the
VOP command itself. It receives one RIFF/WAVE byte stream from MDRack, writes a
restricted temporary WAVE file outside the checkout and selected root, runs VOP
with an argv list, parses its single JSON result, emits only the strict
`mdrack.timed-transcript.v1` response on stdout, and deletes temporary files in a
`finally` path. The primary VOP argv is:

```bash
voiceover transcribe --audio TEMP_WAVE --provider nemotron-local --model nvidia/nemotron-3.5-asr-streaming-0.6b --language ru --device cpu --compute auto --word-timestamps --runtime auto --json
```

The wrapper must require usable segments or words and convert them to integer
`start_ms`/`end_ms` atoms. It rejects VOP text-only/invalid timing output rather
than making up timestamps or taking another provider path. It never copies VOP
stderr, raw audio, source paths, temporary names, or private text into MDRack
stdout or the agent report.

Nemotron accepts its model-owned language/task conditioning through `--language`,
not arbitrary context, phrase hints, prompt, or glossary text. If the explicitly
authorized job genuinely needs contextual text, the agent deliberately selects
local `qwen-local`, preflights that provider, and uses its supported `--context` or
`--context-file` with `Qwen/Qwen3-ASR-0.6B`; this is not an automatic fallback.

## Verify a checkout before maintainer work

The quick start above verifies one local Markdown flow. Maintainers can run the
separate [one-store acceptance evidence](one-store-acceptance.md) runner to
exercise the canonical synthetic fixture, a temporary installed-wheel target,
and privacy-safe evidence. It does not use a private vault or prove live LM
Studio behavior.

## Configure LM Studio

Without `--config-file`, MDRack reads `<root>/.mdrack/config.toml` when it exists.
Configuration precedence is defaults, TOML, `MDRACK_<SECTION>_<FIELD>` environment
variables, then command-line overrides. Relative store/config paths resolve from
`--root`.

A minimal local embedding configuration is:

```toml
[embedding]
provider = "lmstudio"
model = "qwen3-embedding-0.6b"
endpoint = "http://localhost:1234/v1"
timeout_secs = 120
dimensions = 1024

[search]
default_mode = "hybrid"
text_weight = 0.4
semantic_weight = 0.6
top_k = 20
```

The model name, output dimensions, and stored profile fingerprint must match the
LM Studio model actually serving the endpoint. A model or dimension change
requires rebuilding derived vectors; MDRack fails closed rather than mixing
incompatible spaces. Inspect the available lifecycle commands with:

```bash
uv run mdrack model --help
uv run mdrack rebuild embeddings --help
```

LM Studio HTTP is the only production embedding boundary. MDRack does not load
model weights through Python, and fake embeddings are an explicit offline/test
choice. Model lifecycle and any live provider call require a reachable local LM
Studio instance; text mode does not call an embedding provider.

## Everyday workflow

```bash
uv run mdrack --root ./notes scan
uv run mdrack --root ./notes search "architecture" --mode text
uv run mdrack --root ./notes search "design boundaries" --mode semantic
uv run mdrack --root ./notes search "storage" --mode hybrid
uv run mdrack --root ./notes status
uv run mdrack --root ./notes doctor
```

`scan` defaults to the configured LM Studio provider. Use `--provider fake` only
for deterministic offline verification. `scan --changed` is accepted for
compatibility but ordinary scan already performs change detection.

For the full command inventory and CLI/engine differences, see
[public interfaces](current-architecture/public-interfaces.md).

## Embedded text search

After a store has been initialized and scanned, host Python code can use the
Click-free engine for text retrieval:

```python
from pathlib import Path

from mdrack.config.loader import load_config
from mdrack.public_api import MDRackEngine

root = Path("notes")
config = load_config(root=root)
with MDRackEngine(root=root, config=config) as engine:
    result = engine.search_text("architecture", limit=10)
    print(result.to_dict())
```

Semantic/hybrid engine calls are asynchronous and require an injected compatible
embedding provider. The engine does not expose every diagnostic or model command;
use the [interface matrix](current-architecture/public-interfaces.md#embedded-engine)
for the exact boundary.

## Next reading

- [Operations and troubleshooting](operations.md)
- [Current architecture](current-architecture/README.md)
- [Current SQLite persistence](current-architecture/sqlite-persistence.md)
- [Development guide](development.md)
