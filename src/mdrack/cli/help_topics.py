"""Static, pre-configuration help for the MDRack command-line interface."""

from __future__ import annotations

import shlex
from collections.abc import Mapping

import click

TOPICS: tuple[str, ...] = ("quickstart", "configuration", "search", "media")
_TOPIC_TEXT: Mapping[str, str] = {
    "quickstart": """Quickstart (offline text search)

This recipe uses deterministic fake embeddings and text mode. It needs no
embedding provider or network connection for the scan and search steps.

  mkdir -p ./notes
  printf '# Architecture\\n\\nLocal notes.\\n' > ./notes/example.md
  mdrack --root ./notes init
  mdrack --root ./notes scan --provider fake
  mdrack --root ./notes search architecture --mode text

The commands create derived state under ./notes/.mdrack/; source Markdown is
not modified. Fake embeddings are an offline smoke choice, not semantic-quality
evidence.
""",
    "configuration": """Configuration

MDRack reads <root>/.mdrack/config.toml when --config-file is not supplied.
Relative paths resolve from --root. The production embedding boundary is the
LM Studio HTTP endpoint; no model weights are loaded by Python.

Inspect the current lifecycle options with:

  mdrack model --help
  mdrack rebuild embeddings --help

For a provider-free installation check, use --provider fake and --mode text as
shown by `mdrack guide quickstart`. A configuration file is not needed to read
this guide.
""",
    "search": """Search scopes

Search accepts text, semantic, and hybrid modes. Text mode is provider-free.
Use --scope to restrict a unified search to all, notes, audio, video, frames,
or images; combine it with the normal query and mode options.

  mdrack --root ./notes search architecture --mode text --scope notes
  mdrack --root ./notes search meeting --mode text --scope audio
  mdrack --root ./notes search demo --mode text --scope video

Use `mdrack search --help` for the complete current option list. The search
contract uses one configured catalog; this guide intentionally does not expose
legacy catalog selectors.
""",
    "media": """Media boundaries

MDRack indexes caller-supplied transcript and frame-caption text.
It does not provide built-in transcription, decoding, pixel/visual, or acoustic
search. This guide only prints help: it does not select private data or create
derived state. Before a real mutation, an agent needs an explicit root, one
explicitly selected source, and authorization for the named operation. Text or
Markdown, direct images with caller-supplied text, WAVE, and ISO-BMFF video are
separate input boundaries.

The following are explicit, caller-authorized local adapters: WAVE audio is passed
to a shell-free stdin transcription command, and ISO-BMFF video is passed to a
shell-free stdin extractor. They do not establish provider quality, Windows, or
real-source evidence. Keep selected source values outside reports; source files are
not modified.

  mdrack --root ./notes ingest transcript TRANSCRIPT_PATH --resource-id ID \\
    --kind audio --media-type audio/transcript --namespace local --no-embeddings
  mdrack --root ./notes ingest audio SOURCE_PATH --source-ref REF \\
    --allow-external-stt --stt-command COMMAND
  mdrack --root ./notes ingest raw-video SOURCE_PATH --source-ref REF \\
    --allow-external-video-extractor --video-extractor-command COMMAND
  mdrack --root ./notes image ingest IMAGE_PATH --resource-id ID \\
    --source-namespace local --source-ref images/example.png --caption "A caption"

Review `mdrack ingest --help` and `mdrack image --help` before using a recipe;
these operations are separate from Markdown scanning.
""",
}


def render_topic(topic: str, registered_commands: Mapping[str, object]) -> str:
    """Return one static topic after checking every documented Click path."""
    recipe_paths = {
        _recipe_command_path(line, registered_commands)
        for line in _TOPIC_TEXT[topic].splitlines()
        if line.strip().startswith("mdrack ")
    }
    missing = sorted(" ".join(path) for path in recipe_paths if not path)
    if missing:
        raise RuntimeError(f"Guide recipe command path is not registered: {', '.join(missing)}")
    return _TOPIC_TEXT[topic]


def _recipe_command_path(
    line: str,
    registered_commands: Mapping[str, object],
) -> tuple[str, ...]:
    """Resolve the complete registered Click path from one recipe command."""
    tokens = shlex.split(line.removesuffix("\\"))
    try:
        index = tokens.index("mdrack") + 1
    except ValueError:
        return ()
    commands = registered_commands
    path: list[str] = []
    while index < len(tokens):
        token = tokens[index]
        if not path and token in {"--root", "--config-file"}:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        command = commands.get(token)
        if command is None:
            return ()
        path.append(token)
        commands = getattr(command, "commands", {})
        if not commands:
            return tuple(path)
        index += 1
    return tuple(path)


@click.command(name="guide")
@click.argument("topic", required=False, type=click.Choice(TOPICS))
@click.pass_context
def guide(ctx: click.Context, topic: str | None) -> None:
    """Show static help for quickstart, configuration, search, and media."""
    root_command = ctx.find_root().command
    commands = getattr(root_command, "commands", {})
    if topic is None:
        click.echo("Available guide topics:")
        for name in TOPICS:
            click.echo(f"  {name:<15} {name.title()} guidance")
        click.echo("\nRun `mdrack guide TOPIC` for human-readable help.")
        return
    click.echo(render_topic(topic, commands).rstrip())
