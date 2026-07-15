ALTER TABLE files ADD COLUMN logical_id TEXT;
ALTER TABLE files ADD COLUMN root_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE files ADD COLUMN parser_name TEXT;
ALTER TABLE files ADD COLUMN parser_version TEXT;
ALTER TABLE files ADD COLUMN chunk_strategy_name TEXT;
ALTER TABLE files ADD COLUMN chunk_strategy_version TEXT;
ALTER TABLE files ADD COLUMN index_run_id TEXT REFERENCES index_runs(id);

ALTER TABLE sections ADD COLUMN logical_id TEXT;

ALTER TABLE chunks ADD COLUMN logical_id TEXT;
ALTER TABLE chunks ADD COLUMN start_line INTEGER;
ALTER TABLE chunks ADD COLUMN end_line INTEGER;
ALTER TABLE chunks ADD COLUMN block_logical_id TEXT;

ALTER TABLE index_runs ADD COLUMN files_indexed INTEGER DEFAULT 0;
ALTER TABLE index_runs ADD COLUMN files_failed INTEGER DEFAULT 0;
ALTER TABLE index_runs ADD COLUMN errors_count INTEGER DEFAULT 0;
ALTER TABLE index_runs ADD COLUMN parser_name TEXT;
ALTER TABLE index_runs ADD COLUMN parser_version TEXT;
ALTER TABLE index_runs ADD COLUMN chunk_strategy_name TEXT;
ALTER TABLE index_runs ADD COLUMN chunk_strategy_version TEXT;

CREATE UNIQUE INDEX idx_files_logical_id ON files(logical_id) WHERE logical_id IS NOT NULL;
CREATE UNIQUE INDEX idx_sections_logical_id ON sections(logical_id) WHERE logical_id IS NOT NULL;
CREATE UNIQUE INDEX idx_chunks_logical_id ON chunks(logical_id) WHERE logical_id IS NOT NULL;
