CREATE TABLE files (
    id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    title TEXT,
    source_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE sections (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    title TEXT,
    heading_path TEXT,
    level INTEGER NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    parent_id TEXT REFERENCES sections(id)
);

CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    section_id TEXT REFERENCES sections(id),
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text',
    chunk_index INTEGER NOT NULL,
    heading_path TEXT,
    previous_chunk_id TEXT,
    next_chunk_id TEXT,
    embedding_text TEXT,
    embedding_text_hash TEXT
);

CREATE TABLE embedding_profiles (
    name TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    endpoint TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE chunk_embeddings (
    chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    profile_name TEXT NOT NULL REFERENCES embedding_profiles(name),
    embedding BLOB,
    embedded_at TEXT,
    PRIMARY KEY (chunk_id, profile_name)
);

CREATE TABLE index_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    files_seen INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    files_deleted INTEGER DEFAULT 0,
    chunks_created INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE TABLE diagnostics (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES index_runs(id),
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_sections_file_id ON sections(file_id);
CREATE INDEX idx_chunks_file_id ON chunks(file_id);
CREATE INDEX idx_chunks_section_id ON chunks(section_id);
CREATE INDEX idx_chunk_embeddings_profile ON chunk_embeddings(profile_name);
CREATE INDEX idx_diagnostics_run_id ON diagnostics(run_id);