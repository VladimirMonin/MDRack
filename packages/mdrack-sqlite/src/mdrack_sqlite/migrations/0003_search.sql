CREATE VIRTUAL TABLE core_search_units_fts USING fts5(
    unit_id UNINDEXED,
    content,
    tokenize = 'unicode61'
);

CREATE INDEX idx_core_resources_kind ON core_resources(resource_kind, resource_id);
CREATE INDEX idx_core_resources_media ON core_resources(media_type, resource_id);
CREATE INDEX idx_core_resources_namespace ON core_resources(source_namespace, resource_id);
CREATE INDEX idx_core_resources_hash ON core_resources(content_hash, resource_id);
CREATE INDEX idx_core_representations_resource ON core_representations(resource_id, representation_id);
CREATE INDEX idx_core_representations_kind ON core_representations(representation_kind, resource_id, representation_id);
CREATE INDEX idx_core_representations_modality ON core_representations(modality, resource_id, representation_id);
CREATE INDEX idx_core_units_resource ON core_search_units(resource_id, unit_id);
CREATE INDEX idx_core_units_kind ON core_search_units(unit_kind, resource_id, unit_id);
CREATE INDEX idx_core_units_modality ON core_search_units(modality, resource_id, unit_id);
CREATE INDEX idx_core_embeddings_space ON core_unit_embeddings(space_id, unit_id);
CREATE INDEX idx_core_spaces_metric ON core_embedding_spaces(metric, space_id);
CREATE INDEX idx_core_spaces_fingerprint ON core_embedding_spaces(fingerprint, space_id);
CREATE INDEX idx_core_facets_lookup ON core_facets(namespace, value, facet_id);
CREATE INDEX idx_core_resource_facets_facet ON core_resource_facets(facet_id, resource_id);
CREATE INDEX idx_core_resource_facets_resource ON core_resource_facets(resource_id, facet_id);
