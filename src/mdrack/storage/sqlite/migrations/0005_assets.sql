CREATE TABLE assets (
    asset_id TEXT PRIMARY KEY,
    root_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_hash TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    width INTEGER,
    height INTEGER,
    exists_on_disk INTEGER NOT NULL CHECK (exists_on_disk IN (0, 1)),
    UNIQUE (root_id, relative_path)
);

CREATE TABLE asset_references (
    reference_id TEXT PRIMARY KEY,
    asset_id TEXT REFERENCES assets(asset_id) ON DELETE SET NULL,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    document_logical_id TEXT NOT NULL,
    document_relative_path TEXT NOT NULL,
    block_logical_id TEXT NOT NULL,
    chunk_logical_id TEXT,
    raw_reference TEXT NOT NULL,
    syntax TEXT NOT NULL CHECK (syntax IN ('markdown', 'obsidian', 'html')),
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_offset INTEGER,
    end_offset INTEGER,
    alt_text TEXT,
    surrounding_text TEXT,
    resolution_status TEXT NOT NULL CHECK (
        resolution_status IN ('resolved', 'missing', 'unsafe_reference', 'external_reference')
    )
);

CREATE TABLE asset_descriptions (
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    description_kind TEXT NOT NULL,
    description_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (asset_id, description_kind)
);

CREATE INDEX idx_asset_references_file_id ON asset_references(file_id);
CREATE INDEX idx_asset_references_asset_id ON asset_references(asset_id);
