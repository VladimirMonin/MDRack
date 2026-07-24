CREATE TABLE mdrack_vector_codecs (
    codec_id TEXT PRIMARY KEY CHECK (trim(codec_id, ' ') <> ''),
    codec_version INTEGER NOT NULL CHECK (
        typeof(codec_version) = 'integer' AND codec_version >= 1
    ),
    component_type TEXT NOT NULL CHECK (component_type IN ('float32', 'float64')),
    byte_order TEXT NOT NULL CHECK (byte_order = 'little'),
    lossy INTEGER NOT NULL CHECK (lossy IN (0, 1))
);

INSERT INTO mdrack_vector_codecs(codec_id, codec_version, component_type, byte_order, lossy)
VALUES
    ('ieee754-f32-le-v1', 1, 'float32', 'little', 0),
    ('ieee754-f64-le-v1', 1, 'float64', 'little', 0);

CREATE TABLE mdrack_vector_backends (
    backend_id TEXT PRIMARY KEY CHECK (trim(backend_id, ' ') <> ''),
    backend_schema_version INTEGER NOT NULL CHECK (
        typeof(backend_schema_version) = 'integer' AND backend_schema_version >= 1
    ),
    extension_required INTEGER NOT NULL CHECK (extension_required IN (0, 1)),
    supports_atomic_replace INTEGER NOT NULL CHECK (supports_atomic_replace IN (0, 1)),
    supports_atomic_delete INTEGER NOT NULL CHECK (supports_atomic_delete IN (0, 1))
);

INSERT INTO mdrack_vector_backends(
    backend_id,
    backend_schema_version,
    extension_required,
    supports_atomic_replace,
    supports_atomic_delete
) VALUES ('builtin-exact-v1', 1, 0, 1, 1);

CREATE INDEX idx_mdrack_vector_codecs_component ON mdrack_vector_codecs(component_type, codec_id);
