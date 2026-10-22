"""Type-level compatibility at the search/read and image metadata boundaries."""
from __future__ import annotations

from typing import get_type_hints

from mdrack.application.resource_catalog import UnitReadProjection, project_unit
from mdrack.ingestion.raw_source_provenance import RawSourceProvenance, resource_metadata
from mdrack_core import JSONValue, Locator, SearchUnitRecord


def test_unit_projection_preserves_existing_nullable_text_and_annotates_it() -> None:
    unit = SearchUnitRecord('unit', 'resource', 'representation', 'whole_resource', 'image', None,
                            Locator('whole_image', {}), 0)
    projected = project_unit(unit)
    assert projected.text is None
    assert projected.to_dict()['text'] is None
    assert get_type_hints(UnitReadProjection)['text'] == str | None


def _json_mapping() -> dict[str, JSONValue]:
    return {}


def test_raw_metadata_producer_declares_the_json_values_it_emits() -> None:
    # Resolve recursive JSONValue aliases on both sides with identical semantics.
    expected = get_type_hints(_json_mapping)['return']
    assert get_type_hints(RawSourceProvenance.to_dict)['return'] == expected
    assert get_type_hints(resource_metadata)['return'] == expected
