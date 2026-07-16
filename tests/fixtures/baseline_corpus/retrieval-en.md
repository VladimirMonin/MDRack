# Agent Retrieval Guide

## Neighbor context

An agent can inspect the previous and next chunks around a selected result. Stable source facts make that navigation reproducible.

## Example

```python
def neighboring_chunks(chunk_id: str) -> tuple[str | None, str | None]:
    """Return stable references to adjacent chunks."""
    return None, None
```

| Field | Meaning |
| --- | --- |
| previous | Earlier chunk in the same document |
| next | Later chunk in the same document |

See [the indexing note](indexing-ru.md) for deterministic indexing details.
