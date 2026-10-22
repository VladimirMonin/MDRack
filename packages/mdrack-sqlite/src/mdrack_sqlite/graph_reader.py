"""One eager graph reconstruction owner. Caller owns the read transaction."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Facet,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)

from .resource_store import _decode_mapping
from .vector_codecs import decode_vector_payload

if TYPE_CHECKING:
    from .resource_store import SQLiteResourceStore


def read_graph(
    store: SQLiteResourceStore, resource_id: str, *, resource: ResourceRecord | None = None,
) -> PreparedResourceBatch | None:
    """Materialize a graph; verification may supply its already-read resource.

    Supplying the private row projection avoids invoking public hydration hooks
    during catalog preflight, while retaining one graph reconstruction owner.
    """
    connection = store.connection
    if resource is None:
        resource = store.read_resource(resource_id)
    if resource is None:
        return None
    representations = tuple(
        RepresentationRecord(
            representation_id=r["representation_id"],
            resource_id=r["resource_id"],
            representation_kind=r["representation_kind"],
            modality=r["modality"],
            text=r["text_content"],
            language=r["language"],
            producer_fingerprint=r["producer_fingerprint"],
            token_count=r["token_count"],
            token_count_kind=r["token_count_kind"],
            metadata=_decode_mapping(r["metadata_json"], "representation.metadata"),
        )
        for r in connection.execute(
            "SELECT * FROM core_representations WHERE resource_id=? ORDER BY representation_id", (resource_id,)
        )
    )
    units = tuple(
        SearchUnitRecord(
            unit_id=r["unit_id"],
            resource_id=r["resource_id"],
            representation_id=r["representation_id"],
            unit_kind=r["unit_kind"],
            modality=r["modality"],
            text=r["text_content"],
            evidence_locator=Locator(
                r["evidence_locator_kind"], _decode_mapping(r["evidence_locator_json"], "locator")
            ),
            ordinal=r["ordinal"],
            token_count=r["token_count"],
            token_count_kind=r["token_count_kind"],
            metadata=_decode_mapping(r["metadata_json"], "unit.metadata"),
        )
        for r in connection.execute(
            "SELECT * FROM core_search_units WHERE resource_id=? ORDER BY representation_id,ordinal,unit_id",
            (resource_id,),
        )
    )
    spaces = tuple(
        EmbeddingSpaceRecord(
            r["space_id"],
            r["dimensions"],
            r["metric"],
            r["fingerprint"],
            _decode_mapping(r["metadata_json"], "space.metadata"),
        )
        for r in connection.execute(
            "SELECT DISTINCT s.* FROM core_embedding_spaces s JOIN core_unit_embeddings e USING(space_id) "
            "JOIN core_search_units u USING(unit_id) WHERE u.resource_id=? ORDER BY s.space_id",
            (resource_id,),
        )
    )
    vectors = tuple(
        VectorRecord(
            r["unit_id"],
            r["space_id"],
            decode_vector_payload(
                r["embedding"],
                dimensions=r["dimensions"],
                metadata=_decode_mapping(r["metadata_json"], "space.metadata"),
                registry=store._codec_registry,
            ),
        )
        for r in connection.execute(
            "SELECT e.*,s.dimensions,s.metadata_json FROM core_unit_embeddings e "
            "JOIN core_embedding_spaces s USING(space_id) JOIN core_search_units u USING(unit_id) "
            "WHERE u.resource_id=? ORDER BY e.unit_id,e.space_id",
            (resource_id,),
        )
    )
    facets = tuple(
        ResourceFacet(
            resource_id,
            Facet(r["namespace"], r["value"]),
            r["origin"],
            None if r["producer_is_null"] == 1 else r["producer_value"],
            None if r["confidence_json"] is None else float(json.loads(r["confidence_json"].decode("utf-8"))),
        )
        for r in connection.execute(
            "SELECT f.*,rf.origin,rf.producer_is_null,rf.producer_value,rf.confidence_json "
            "FROM core_resource_facets rf JOIN core_facets f USING(facet_id) WHERE rf.resource_id=? "
            "ORDER BY f.namespace,f.value,rf.origin,rf.producer_is_null,rf.producer_value",
            (resource_id,),
        )
    )
    return PreparedResourceBatch(resource, representations, units, spaces, vectors, facets)
