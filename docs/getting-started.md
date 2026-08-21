# Getting started with MDRack 1.3

MDRack is a local Python 3.11+ CLI and embedded library. It indexes supplied
Markdown into SQLite and can use LM Studio over HTTP for embeddings. The project
has not been tagged or published to PyPI, so the supported setup described here
uses a repository checkout.

## Install from the checkout

Install `uv`, clone the repository, and run:

```bash
uv sync --all-extras
uv run mdrack --version
uv run mdrack --help
uv run mdrack guide
```

`uv` resolves the workspace distributions `mdrack-core`, `mdrack-media`, and
`mdrack-sqlite` together with the application. Do not use system `pip` for this
checkout.

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
