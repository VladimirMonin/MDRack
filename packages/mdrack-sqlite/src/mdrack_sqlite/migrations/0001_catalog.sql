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
