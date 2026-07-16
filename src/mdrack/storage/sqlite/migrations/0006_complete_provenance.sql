ALTER TABLE chunks ADD COLUMN start_offset INTEGER;
ALTER TABLE chunks ADD COLUMN end_offset INTEGER;
ALTER TABLE chunks ADD COLUMN block_kind TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE chunks ADD COLUMN chunk_kind TEXT NOT NULL DEFAULT 'unknown';

-- Databases upgraded from 0005 predate explicit structural kinds. The legacy
-- content_type is the authoritative retrieval kind. For source-block provenance,
-- legacy text chunks are defensibly paragraphs; all other recognized content
-- types already use the same stable name in the source-block and chunk enums.
UPDATE chunks
SET chunk_kind = CASE
        WHEN content_type IN (
            'text', 'list', 'blockquote', 'callout', 'code', 'table',
            'mermaid', 'image_reference'
        ) THEN content_type
        ELSE 'text'
    END,
    block_kind = CASE
        WHEN content_type = 'text' THEN 'paragraph'
        WHEN content_type IN (
            'list', 'blockquote', 'callout', 'code', 'table', 'mermaid',
            'image_reference'
        ) THEN content_type
        ELSE 'paragraph'
    END;