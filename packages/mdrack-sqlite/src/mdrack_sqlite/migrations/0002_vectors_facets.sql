CREATE TABLE core_embedding_spaces (
    space_id TEXT NOT NULL PRIMARY KEY CHECK (trim(space_id, ' ') <> ''),
    dimensions INTEGER NOT NULL CHECK (typeof(dimensions) = 'integer' AND dimensions >= 1),
    metric TEXT NOT NULL CHECK (metric IN ('cosine', 'dot', 'l2')),
    fingerprint TEXT NOT NULL CHECK (trim(fingerprint, ' ') <> ''),
    metadata_json TEXT NOT NULL
);

CREATE TABLE core_unit_embeddings (
    unit_id TEXT NOT NULL REFERENCES core_search_units(unit_id) ON DELETE CASCADE,
    space_id TEXT NOT NULL REFERENCES core_embedding_spaces(space_id) ON DELETE RESTRICT,
    embedding BLOB NOT NULL CHECK (typeof(embedding) = 'blob' AND length(embedding) > 0),
    embedded_at TEXT NOT NULL,
    PRIMARY KEY (unit_id, space_id)
);

CREATE TABLE core_facets (
    facet_id INTEGER PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (trim(namespace, ' ') <> ''),
    value TEXT NOT NULL CHECK (trim(value, ' ') <> ''),
    UNIQUE (namespace, value)
);

CREATE TABLE core_resource_facets (
    resource_id TEXT NOT NULL REFERENCES core_resources(resource_id) ON DELETE CASCADE,
    facet_id INTEGER NOT NULL REFERENCES core_facets(facet_id) ON DELETE RESTRICT,
    origin TEXT NOT NULL CHECK (trim(origin, ' ') <> ''),
    producer_is_null INTEGER NOT NULL CHECK (producer_is_null IN (0, 1)),
    producer_value TEXT NOT NULL,
    confidence_json BLOB CHECK (confidence_json IS NULL OR typeof(confidence_json) = 'blob'),
    PRIMARY KEY (resource_id, facet_id, origin, producer_is_null, producer_value),
    CHECK (
        (producer_is_null = 1 AND producer_value = '')
        OR (producer_is_null = 0 AND trim(producer_value, ' ') <> '')
    )
);
