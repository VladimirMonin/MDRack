"""M1 parser metadata behavior and privacy contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.metadata_normalization import MetadataNormalizationError
from mdrack.domain.blocks import BlockType


def _parse(content: str, *, strict: bool = False):
    parser = MarkdownItParser(
        metadata_invalid_policy="fail_resource" if strict else "warn_and_continue"
    )
    return parser.parse(
        Path("/tmp/metadata.md"),
        content=content,
        document_id="doc-metadata",
        relative_path="metadata.md",
    )


def test_yaml_types_duplicate_keys_and_fingerprints_are_deterministic() -> None:
    content = """---
string: "3"
integer: 3
float: 3.0
flag: true
empty: null
when: 2026-07-20
items: !!set
  b: null
  a: null
nested:
  key: value
---
# Body

Searchable body.
"""
    first = _parse(content)
    second = _parse(content)

    assert first.frontmatter == {
        "empty": None,
        "flag": True,
        "float": 3.0,
        "integer": 3,
        "items": ["a", "b"],
        "nested": {"key": "value"},
        "string": "3",
        "when": "2026-07-20",
    }
    assert first.metadata_diagnostics == ()
    assert first.metadata_fingerprint == second.metadata_fingerprint
    assert first.metadata_policy_fingerprint == second.metadata_policy_fingerprint
    assert first.metadata_normalizer_version == "metadata-json-v1"


def test_malformed_and_duplicate_metadata_do_not_block_body_by_default() -> None:
    for invalid in ("nested: [broken", "key: one\nkey: two"):
        document = _parse(f"---\n{invalid}\n---\n# Public body\n\nSearchable text.")
        assert document.frontmatter == {}
        assert [
            (diagnostic.category, diagnostic.count)
            for diagnostic in document.metadata_diagnostics
        ] == [("METADATA_PARSE_FAILED", 1)]
        assert [
            block.plain_text
            for block in document.blocks
            if block.block_type != BlockType.FRONTMATTER
        ] == ["Public body", "Searchable text."]


def test_strict_metadata_policy_rejects_malformed_frontmatter_without_values() -> None:
    sentinel = "PRIVATE_METADATA_SENTINEL"
    with pytest.raises(MetadataNormalizationError) as caught:
        _parse(f"---\nsecret: [{sentinel}\n---\n# Body", strict=True)

    assert sentinel not in str(caught.value)
    assert "METADATA_PARSE_FAILED" in str(caught.value)


def test_frontmatter_values_never_enter_retrieval_blocks() -> None:
    sentinel = "PRIVATE_METADATA_SENTINEL"
    document = _parse(
        f"---\nsecret: {sentinel}\naliases: [hidden]\n---\n# Public\n\nVisible body."
    )
    retrieval_text = "\n".join(
        block.plain_text or ""
        for block in document.blocks
        if block.block_type != BlockType.FRONTMATTER
    )

    assert sentinel not in retrieval_text
    assert "hidden" not in retrieval_text
    assert retrieval_text == "Public\nVisible body."
