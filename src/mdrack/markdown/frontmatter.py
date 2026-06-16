"""YAML frontmatter extraction from Markdown files.

Uses simple line-by-line parsing — no external YAML library required.
"""

from __future__ import annotations


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Extract YAML frontmatter delimited by --- lines.

    Parameters
    ----------
    content:
        Raw Markdown text that may begin with a frontmatter block.

    Returns
    -------
    tuple[dict[str, str], str]
        (metadata_dict, remaining_content) where metadata values are
        simple strings (no nested structures).  If no frontmatter is
        found the dict is empty and the full content is returned.
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, content

    closing: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing = idx
            break

    if closing is None:
        return {}, content

    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        colon = stripped.find(":")
        if colon == -1:
            continue
        key = stripped[:colon].strip()
        value = stripped[colon + 1 :].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if key:
            metadata[key] = value

    remaining = "\n".join(lines[closing + 1 :])
    if remaining.startswith("\n"):
        remaining = remaining[1:]

    return metadata, remaining
