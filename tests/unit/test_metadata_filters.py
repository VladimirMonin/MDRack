"""M2 typed metadata filter compiler contracts."""

from __future__ import annotations

import pytest

from mdrack.application.metadata_filters import (
    MetadataFilter,
    MetadataFilters,
    compile_metadata_filters,
)
from mdrack.application.metadata_projection import FACET_SCALAR_CODEC
from mdrack_core.domain import Facet, SearchScope


def test_compiler_preserves_categorical_scope_and_encodes_any_all_none() -> None:
    base = SearchScope(
        resource_kinds=("document",),
        media_types=("text/markdown",),
        facets_all=(Facet("existing", "s:value"),),
    )
    compiled = compile_metadata_filters(
        MetadataFilters(
            any=(MetadataFilter("status", "reviewed"), MetadataFilter("priority", 3)),
            all=(MetadataFilter("draft", False), MetadataFilter("ratio", 3.0)),
            none=(MetadataFilter("archived", True), MetadataFilter("deleted", None)),
        ),
        base_scope=base,
    )

    assert compiled.resource_kinds == ("document",)
    assert compiled.media_types == ("text/markdown",)
    assert compiled.facets_any == (
        Facet("status", FACET_SCALAR_CODEC.encode("reviewed")),
        Facet("priority", FACET_SCALAR_CODEC.encode(3)),
    )
    assert compiled.facets_all == (
        Facet("existing", "s:value"),
        Facet("draft", FACET_SCALAR_CODEC.encode(False)),
        Facet("ratio", FACET_SCALAR_CODEC.encode(3.0)),
    )
    assert compiled.facets_none == (
        Facet("archived", FACET_SCALAR_CODEC.encode(True)),
        Facet("deleted", FACET_SCALAR_CODEC.encode(None)),
    )


def test_compiler_deduplicates_exact_facets_without_collapsing_types() -> None:
    compiled = compile_metadata_filters(
        MetadataFilters(
            all=(
                MetadataFilter("value", "3"),
                MetadataFilter("value", 3),
                MetadataFilter("value", "3"),
            )
        )
    )

    assert compiled.facets_all == (
        Facet("value", FACET_SCALAR_CODEC.encode("3")),
        Facet("value", FACET_SCALAR_CODEC.encode(3)),
    )


def test_filter_contract_rejects_non_scalars_and_invalid_collections() -> None:
    with pytest.raises((TypeError, ValueError)):
        MetadataFilter("value", [1])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must contain MetadataFilter"):
        MetadataFilters(any=("not-a-filter",))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="filters must be"):
        compile_metadata_filters("not-filters")  # type: ignore[arg-type]
