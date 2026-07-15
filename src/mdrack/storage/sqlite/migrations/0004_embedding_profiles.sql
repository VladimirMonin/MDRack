ALTER TABLE embedding_profiles ADD COLUMN fingerprint TEXT;
ALTER TABLE embedding_profiles ADD COLUMN provider TEXT;
ALTER TABLE embedding_profiles ADD COLUMN runtime TEXT;
ALTER TABLE embedding_profiles ADD COLUMN model_key TEXT;
ALTER TABLE embedding_profiles ADD COLUMN model_family TEXT;
ALTER TABLE embedding_profiles ADD COLUMN quantization TEXT;
ALTER TABLE embedding_profiles ADD COLUMN query_instruction_hash TEXT;
ALTER TABLE embedding_profiles ADD COLUMN normalization_mode TEXT;
ALTER TABLE embedding_profiles ADD COLUMN endpoint_family TEXT;

UPDATE embedding_profiles
SET fingerprint = 'legacy:' || name,
    provider = 'legacy',
    runtime = 'legacy',
    model_key = model,
    model_family = 'legacy',
    quantization = 'unknown',
    query_instruction_hash = 'legacy',
    normalization_mode = 'unknown',
    endpoint_family = 'legacy'
WHERE fingerprint IS NULL;

ALTER TABLE chunk_embeddings ADD COLUMN profile_fingerprint TEXT;
UPDATE chunk_embeddings
SET profile_fingerprint = (
    SELECT fingerprint
    FROM embedding_profiles
    WHERE embedding_profiles.name = chunk_embeddings.profile_name
)
WHERE profile_fingerprint IS NULL;

CREATE INDEX idx_embedding_profiles_fingerprint
    ON embedding_profiles(fingerprint)
    WHERE fingerprint IS NOT NULL;
CREATE INDEX idx_chunk_embeddings_fingerprint
    ON chunk_embeddings(profile_fingerprint);
