# MDRack Retrieval Design

## Overview

MDRack supports three retrieval modes:

1. **Text Search** — FTS5-based lexical matching (BM25/Okapi)
2. **Semantic Search** — embedding-based cosine similarity via LM Studio
3. **Hybrid Search** — Reciprocal Rank Fusion (RRF) combining text + semantic results

All modes return enriched results with provenance: `chunk_id`, `score`, `file_relative_path`, `section_title`, `heading_path`.

Image references participate in retrieval only through explicit alt text and
adjacent document text. Raw Markdown/Obsidian/HTML references and resolved asset
paths are provenance fields, not searchable content. No vision, OCR, visual
embedding, URL fetch, or model call is part of the offline asset pipeline.

## Text Search (FTS5)

### Mechanism

1. User provides query string (e.g., `"configuration options"`)
2. `text_search(conn, query, limit, offset)` executes:
   ```sql
   SELECT chunk_id, rank, snippet(chunks_fts, 1, '<b>', '</b>', '...', 64) AS snippet
   FROM chunks_fts
   WHERE chunks_fts MATCH ?
   ORDER BY rank
   LIMIT ?
   ```
3. Enrich results by joining with `files` and `sections` to add `file_relative_path`, `section_title`
4. Paginate in Python (FTS5 lacks native `OFFSET`, so fetch `limit+offset` and slice)

### Ranking

- FTS5 `rank` = smaller is better (BM25-like)
- Snippet highlights matched terms with `<b>` tags
- The `heading_path` column in FTS allows searching section titles

### Use Cases

- Exact keyword matches
- Phrase queries: `"config file"`
- Boolean OR: `api OR reference`
- Prefix: `meet*` (matches `meeting`, `meets`)
- Limiting by content_type via post-query filter

### Limitations

- No semantic understanding; purely lexical
- No stemming or stopword removal (unicode61 tokenizer only)
- Language-specific analysis not available

## Semantic Search (Vector Similarity)

### Mechanism

1. **Embed the query** via `EmbeddingProvider.embed_query(query, profile)`
   - LM Studio provider: HTTP POST to `/v1/embeddings` (OpenAI compatible)
2. **Load all embeddings** for the profile from `chunk_embeddings`
   - All vectors loaded into Python memory (linear scan)
3. **Compute cosine similarity** between query vector and each stored vector
4. **Sort descending** by similarity score (higher = more relevant)
5. **Enrich** with provenance via joins on `files`/`sections`
6. Return `SemanticSearchResult` with `SearchResultItem` list

### Scoring

```python
def cosine_similarity(a, b):
    return dot(a,b) / (norm(a) * norm(b))  # range [-1,1], typically [0,1]
```

- Precision: full 32-bit float; JSON storage no quantization loss
- Normalization optimization possible if vectors pre-normalized

### EmbeddingProvider Protocol

```python
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str], profile: str = "default") -> list[list[float]]: ...
    async def embed_query(self, text: str, profile: str = "default") -> list[float]: ...
    async def health(self) -> EmbeddingHealth: ...
    @property
    def dimensions(self) -> int: ...
```

Implementations:
- `LMStudioProvider` — calls LM Studio HTTP API
- `FakeProvider` — deterministic hash vectors for tests

Embedding, reranking, model catalog discovery, and model lifecycle are four
separate ports. An `EmbeddingProfile` is not just a display name: its stable
fingerprint covers provider, runtime, model key and family, quantization,
output dimensions, query instruction, normalization mode, and endpoint family.
SQLite binds an active profile name to one fingerprint and stores that
fingerprint with every vector. A mismatched fingerprint or dimension is rejected.

Reduced MRL dimensions use an explicit capability result. An unprobed runtime
remains `runtime_capability_unknown`; offline configuration is not evidence of
live runtime support.

### Error Handling

- Provider failure (network, LM Studio down) → `result.error` set, `results=[]`
- Empty query → immediate empty result (no provider call)
- Chunks without embeddings automatically filtered out

### Performance

- Memory: ~150MB for 10k × 768-dim vectors
- Time: O(n) linear scan; ~50k vectors feasible (<1s)
- Future: pre-normalize, cache norms, or ANN index (HNSW) for larger sets

### Use Cases

- Conceptual search (different terminology)
- Cross-language synonyms (multilingual models)
- "Similar chunks" given problem statement

## Hybrid Search (Reciprocal Rank Fusion)

### Goal

Combine complementary signals from text and semantic modes to improve both precision and recall.

### Rank Fusion Formula

Given ranked lists L1 (text) and L2 (semantic), for each document `d`:

```
RRF_score(d) = Σ_{list} 1 / (k + rank(d, list))
```

where `k` = damping parameter (default 60). If `d` not in a list, that term = 0.

**Example:**
```
text rank:      docA(1), docB(2), docD(5)
semantic rank:  docB(1), docC(2), docD(3)

k=60:
docA: 1/(60+1) = 0.01639
docB: 1/(60+1) + 1/(60+1) = 0.03278
docC: 1/(60+2) = 0.01639
docD: 1/(60+5) + 1/(60+3) = 0.03257

RRF rank: docB > docD > docA ≈ docC
```

### Weighted Blend (Optional)

Final score may apply configurable weights:

```
final_score(d) = w_text * text_norm(d) + w_sem * sem_norm(d) + w_rrf * rrf_score(d)
```

Defaults:
- `text_weight = 0.4`
- `semantic_weight = 0.6`
- `rrf_fusion_weight = 0.0` (enable later)

### Workflow

1. Obtain `text_hits = text_search(query, limit=K)` (BM25 rank 1..K)
2. Obtain `sem_hits = semantic_search(query, provider, limit=K)` → normalized to rank 1..K
3. Build union of all chunk_ids
4. Compute RRF score for each chunk_id
5. Apply weighted sum if RRF weight enabled
6. Sort by final score descending
7. Enrich with provenance (may need extra joins for union members)

Duplicate IDs contribute only their first rank in each branch. Ties are resolved
by first appearance and then stable candidate ID. Results preserve `text_rank`,
`semantic_rank`, `rrf_rank`, and `rrf_score`.

### Reranking contract (test-only in v0.2)

`HybridRetrievalService` retains an injectable `RerankerProvider` seam and
nullable `rerank_rank` / `rerank_score` fields. `DeterministicReranker` exercises
that contract in offline tests, including malformed-response and fail-open
behavior. It is not a production adapter.

The production v0.2 retrieval contract passes no reranker. The current CLI
returns the RRF order; the planned unified embedded hybrid path must return the
same order. Both reranker fields remain `null`. LM Studio has no documented
reranking endpoint, no model is invoked, and chat completion is never used as a
substitute. This is a complete supported result rather than a degraded result.
See [ADR-0001](decisions/0001-reranking-deferred.md).

### Use Cases

- General search with mixed keywords + natural language
- Default mode (`search.default_mode = "hybrid"`) for balanced performance

## Ranking & Provenance

### Standard Result Fields

| Field | Mode(s) | Description |
|-------|---------|-------------|
| `chunk_id` | all | Stable ID for `read chunk` command |
| `score` | all | Text: BM25 rank (lower better); Semantic: cosine (higher better); Hybrid: fused score (higher better) |
| `snippet` | text only | Highlighted snippet with `<b>` tags |
| `content_preview` | sem/hybrid | First 200 chars of `chunks.content` |
| `file_relative_path` | all | Document path in project |
| `section_title` | all | Immediate section heading |
| `heading_path` | all | JSON array of full section ancestry (e.g., `["API", "Authentication", "JWT"]`)

### Provenance Denormalization

`chunks.heading_path` is copied from the parent section at indexing time, avoiding recursive joins at query time.

### Context for Neighbors

Search results do not include neighboring chunks by default. To retrieve surrounding context:
- Use `read chunk <id> --context neighbors` to fetch prev/next chunks
This separates fast ranked retrieval from on-demand expansion.

## Search API Reference

### Text Search

```python
from mdrack.search.text import text_search

result = text_search(conn, query="config", limit=20, offset=0)
# result.query, result.results (list[TextSearchItem]), result.total_count
```

Raises `FTSQueryError` for invalid queries.

### Semantic Search

```python
from mdrack.search.semantic import semantic_search

# async
result = await semantic_search(conn, query="config", provider=provider, profile="default", limit=20)
# result.query, result.results (list[SearchResultItem]), result.total_count, result.error
```

### Hybrid Search

```python
from mdrack.search import hybrid_search

result = await hybrid_search(conn, query="config", provider=provider, profile="default", limit=20)
```

Configuration:
```toml
[parsing]
backend = "markdown_it"  # use "legacy" only for A/B baseline runs

[chunking]
target_chunk_chars = 3200
hard_limit_chars = 8000
max_chunk_tokens = 2000
overlap_chars = 300
code_window_lines = 80
table_rows_per_chunk = 40
mermaid_window_lines = 80

[search]
default_mode = "hybrid"  # or "text", "semantic"
text_weight = 0.4
semantic_weight = 0.6
rrf_k = 60
rrf_fusion_weight = 0.0  # set >0 to enable RRF blending
```

The default indexing path parses Markdown into lossless `SourceBlock` records and
then derives separate `RetrievalChunk` records. Prose may split at sentence or
word boundaries. Code, tables, and Mermaid retain one source block while their
retrieval representations split only on complete lines or rows. Every chunk
stores a parent block ID, source span, heading path, display content, and a
separate embedding text.

`target_chunk_chars` controls normal prose boundaries; `hard_limit_chars` and
`max_chunk_tokens` are absolute limits on every emitted retrieval chunk. A
single table row or Mermaid line that cannot fit is represented by a compact,
hash-addressed omission marker with its original parent block and exact source
span. Mermaid source lines are never fragmented. Adjacent standalone Markdown
image references and Obsidian embeds become individual `IMAGE_REFERENCE`
source blocks, even when CommonMark groups their lines into one paragraph.

## Diagnostics

`mdrack status` reports:

- `files_count` — total indexed documents (status='active')
- `chunks_count` — total retrieval units
- `embeddings_count` — chunks with vectors for active profile
- `active_profile` — embedding profile used in last semantic search
- `schema_version` — DB schema version

If `embeddings_count < chunks_count`, either:
- Indexing not run after adding chunks
- Embedding step failed (LM Studio down, wrong config)
- Profile not registered
- `rebuild embeddings` needed

## Future Improvements

- **Approximate Nearest Neighbor (ANN)** — HNSW index for sub-linear semantic search
- **BM25 tuning** — expose `k1` and `b` parameters
- **Per-chunk embedding variants** — store separate `embedding_text` for different contexts
- **Query expansion** — use nearest neighbors of query to expand terms
- **Result caching** — cache `(query_hash, profile)` → result IDs
- **Multi-profile boosting** — combine embeddings from multiple models
- **Learning-to-Rank (LTR)** — train on click logs to re-weight components
- **Faceted search** — filter by content_type, file path patterns, date ranges
- **Semantic snippet highlighting** — embed query terms into content preview
