-- Local publication authority survives graph withdrawal and deletion.
CREATE TABLE core_publication_states (
    resource_id TEXT PRIMARY KEY NOT NULL CHECK(length(resource_id) > 0),
    status TEXT NOT NULL CHECK(status IN ('published','live_without_projections','deleted')),
    lifecycle_id TEXT NOT NULL CHECK(length(lifecycle_id) > 0),
    state_token TEXT NOT NULL CHECK(length(state_token) > 0),
    source_namespace TEXT NOT NULL CHECK(length(source_namespace) > 0),
    source_locator_kind TEXT NOT NULL CHECK(length(source_locator_kind) > 0),
    source_locator_json TEXT NOT NULL CHECK(json_valid(source_locator_json)),
    source_metadata_json TEXT NOT NULL CHECK(json_valid(source_metadata_json))
);
CREATE UNIQUE INDEX idx_core_publication_live_source
ON core_publication_states(source_namespace,source_locator_kind,source_locator_json)
WHERE status <> 'deleted';
INSERT INTO core_publication_states
SELECT resource_id,'published',lower(hex(randomblob(16))),lower(hex(randomblob(16))),
       source_namespace,locator_kind,locator_json,metadata_json
FROM core_resources;
