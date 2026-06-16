CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    content,
    content_type UNINDEXED,
    heading_path
);
