"""FTS query helpers shared by the standalone SQLite catalog."""

from __future__ import annotations

import re

_FTS_OPERATOR_PATTERN = re.compile(
    r'"|\(|\)|\*|\b(?:AND|OR|NOT|NEAR)\b|\w+:',
    re.IGNORECASE,
)


def plain_query_fallback(query: str) -> str | None:
    """Return a quoted-phrase retry only for plain, non-operator input."""
    if _FTS_OPERATOR_PATTERN.search(query):
        return None
    return f'"{query.replace(chr(34), chr(34) * 2)}"'
