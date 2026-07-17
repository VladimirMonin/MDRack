-- Provider- and source-neutral resource catalog. This migration is create-only:
-- legacy objects and rows remain untouched for candidate-generation compatibility.
CREATE TABLE core_resources (
    resource_id TEXT NOT NULL PRIMARY KEY CHECK (trim(resource_id, ' ') <> ''),
    resource_kind TEXT NOT NULL CHECK (trim(resource_kind, ' ') <> ''),
    media_type TEXT NOT NULL CHECK (trim(media_type, ' ') <> ''),
    source_namespace TEXT NOT NULL CHECK (trim(source_namespace, ' ') <> ''),
    locator_kind TEXT NOT NULL CHECK (trim(locator_kind, ' ') <> ''),
    locator_json TEXT NOT NULL,
    locator_fingerprint TEXT NOT NULL CHECK (
        length(locator_fingerprint) = 71
        AND substr(locator_fingerprint, 1, 7) = 'sha256:'
        AND substr(locator_fingerprint, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    content_hash TEXT CHECK (content_hash IS NULL OR trim(content_hash, ' ') <> ''),
    title TEXT,
    metadata_json TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    UNIQUE (source_namespace, locator_kind, locator_fingerprint)
);

CREATE TABLE core_representations (
    representation_id TEXT NOT NULL PRIMARY KEY CHECK (trim(representation_id, ' ') <> ''),
    resource_id TEXT NOT NULL REFERENCES core_resources(resource_id) ON DELETE CASCADE,
    representation_kind TEXT NOT NULL CHECK (trim(representation_kind, ' ') <> ''),
    modality TEXT NOT NULL CHECK (trim(modality, ' ') <> ''),
    text_content TEXT,
    language TEXT CHECK (language IS NULL OR trim(language, ' ') <> ''),
    producer_fingerprint TEXT CHECK (
        producer_fingerprint IS NULL OR trim(producer_fingerprint, ' ') <> ''
    ),
    token_count INTEGER CHECK (
        token_count IS NULL OR (typeof(token_count) = 'integer' AND token_count >= 0)
    ),
    token_count_kind TEXT CHECK (token_count_kind IS NULL OR token_count_kind IN ('exact', 'estimated')),
    metadata_json TEXT NOT NULL,
    UNIQUE (resource_id, representation_id),
    CHECK ((token_count IS NULL) = (token_count_kind IS NULL))
);

CREATE TABLE core_search_units (
    unit_id TEXT NOT NULL PRIMARY KEY CHECK (trim(unit_id, ' ') <> ''),
    resource_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    unit_kind TEXT NOT NULL CHECK (trim(unit_kind, ' ') <> ''),
    modality TEXT NOT NULL CHECK (trim(modality, ' ') <> ''),
    text_content TEXT,
    evidence_locator_kind TEXT NOT NULL CHECK (
        trim(evidence_locator_kind, ' ') <> ''
    ),
    evidence_locator_json TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (typeof(ordinal) = 'integer' AND ordinal >= 0),
    token_count INTEGER CHECK (
        token_count IS NULL OR (typeof(token_count) = 'integer' AND token_count >= 0)
    ),
    token_count_kind TEXT CHECK (token_count_kind IS NULL OR token_count_kind IN ('exact', 'estimated')),
    metadata_json TEXT NOT NULL,
    UNIQUE (resource_id, unit_id),
    UNIQUE (representation_id, ordinal),
    FOREIGN KEY (resource_id, representation_id)
        REFERENCES core_representations(resource_id, representation_id) ON DELETE CASCADE,
    CHECK ((token_count IS NULL) = (token_count_kind IS NULL))
);

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
