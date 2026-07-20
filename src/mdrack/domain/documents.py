"""Stable Markdown Document intermediate representation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from mdrack.domain.blocks import JSONValue, SourceBlock


@dataclass(frozen=True, order=True)
class MetadataDiagnostic:
    """Value-free aggregate diagnostic transported across indexing boundaries."""

    category: str
    count: int = 1

    def __post_init__(self) -> None:
        if not self.category or self.count < 1:
            raise ValueError("metadata diagnostic requires a category and positive count")


def canonical_metadata_diagnostics(
    diagnostics: Iterable[MetadataDiagnostic | str],
) -> tuple[MetadataDiagnostic, ...]:
    """Aggregate and sort structured diagnostics, accepting legacy categories safely."""

    counts: Counter[str] = Counter()
    for diagnostic in diagnostics:
        if isinstance(diagnostic, str):
            category, count = diagnostic, 1
        elif isinstance(diagnostic, MetadataDiagnostic):
            category, count = diagnostic.category, diagnostic.count
        else:
            raise TypeError("metadata diagnostics must be categories or MetadataDiagnostic values")
        if not category or count < 1:
            raise ValueError("metadata diagnostic requires a category and positive count")
        counts[category] += count
    return tuple(
        MetadataDiagnostic(category=category, count=count)
        for category, count in sorted(counts.items())
    )


@dataclass(frozen=True)
class Document:
    """Parser-independent representation of one Markdown source document."""

    document_id: str
    relative_path: str
    title: str
    frontmatter: Mapping[str, JSONValue]
    blocks: tuple[SourceBlock, ...]
    source_hash: str
    parser_name: str
    parser_version: str
    metadata_diagnostics: tuple[MetadataDiagnostic, ...] = ()
    metadata_fingerprint: str = ""
    metadata_policy_fingerprint: str = ""
    metadata_normalizer_version: str = ""

    def __post_init__(self) -> None:
        if not self.document_id or not self.relative_path:
            raise ValueError("document_id and relative_path are required")
        if not self.source_hash or not self.parser_name or not self.parser_version:
            raise ValueError("source hash and parser identity are required")
        object.__setattr__(self, "frontmatter", MappingProxyType(dict(self.frontmatter)))
        object.__setattr__(
            self,
            "metadata_diagnostics",
            canonical_metadata_diagnostics(self.metadata_diagnostics),
        )
