"""Build embedding text strings from FinalChunk instances.

Embedding text is the formatted string that gets sent to an embedding model.
It is separate from display content and includes provenance metadata
(title, relative path, heading path) to make embeddings context-aware.
"""

from __future__ import annotations

from mdrack.markdown.ir import FinalChunk


def build_embedding_text(
    chunk: FinalChunk,
    document_title: str,
    relative_path: str,
    heading_path: str,
) -> str:
    """Build the embedding text for a chunk.

    Format:
        "[{document_title}] {relative_path} > {heading_path}\\n\\n{content}"

    Parts are omitted when unavailable:
    - *document_title* empty  -> bracket prefix is omitted
    - *heading_path* empty    -> the `` > heading`` part is omitted
    - *relative_path* is always included for provenance

    Parameters
    ----------
    chunk:
        The final chunk whose ``content`` forms the body.
    document_title:
        Title of the source document (may be empty).
    relative_path:
        Relative file path for provenance (always included).
    heading_path:
        Joined heading path string, e.g. ``"Doc > Section > Sub"`` (may be empty).

    Returns
    -------
    str
        Formatted embedding text ready for vectorisation.
    """
    bracket = f"[{document_title}]" if document_title else ""

    suffix_parts: list[str] = []
    if relative_path:
        suffix_parts.append(relative_path)
    if heading_path:
        suffix_parts.append(heading_path)

    suffix = " > ".join(suffix_parts) if suffix_parts else ""

    if bracket and suffix:
        header = f"{bracket} {suffix}"
    else:
        header = bracket or suffix

    if header:
        return f"{header}\n\n{chunk.content}"

    return chunk.content
