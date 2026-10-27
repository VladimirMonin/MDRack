# Indexing and structural chunking

The default indexing path is structural and source-preserving. Markdown remains
external and read-only; the application projects prepared document resources,
units and ready vectors into the provider-free core contract.

## Indexing sequence

```mermaid
sequenceDiagram
    autonumber
    actor Caller as "CLI or MDRackEngine"
    participant Service as "IndexingService"
    participant Scanner as "Markdown scanner"
    participant Parser as "MarkdownItParser"
    participant Chunker as "StructuralChunker"
    participant Projection as "Resource projection"
    participant Embedder as "EmbeddingProvider"
    participant Storage as "IndexStorage / app catalog projection"

    Caller->>Service: "scan"
    Service->>Storage: "read_text_embedding_spaces"
    Note over Service,Storage: "Full-profile admission; refuse incompatible or unknown binding"
    Service->>Storage: "start_run"
    Service->>Scanner: "scan relative Markdown paths"
    Scanner-->>Service: "current paths"
    Service->>Storage: "plan_changes by source hash"
    Storage-->>Service: "new, changed, unchanged, deleted"

    loop Each new or changed file
        Service->>Parser: "parse UTF-8 source"
        Parser-->>Service: "Document and SourceBlock records"
        Service->>Chunker: "build bounded RetrievalChunk records"
        Chunker-->>Service: "display text, embedding text, exact spans"
        opt Embedding provider configured
            Service->>Embedder: "embed chunk embedding_text values"
            Embedder-->>Service: "vectors"
        end
        Service->>Projection: "project prepared document and ready vectors"
        Projection-->>Service: "typed resource graph"
        Service->>Storage: "replace resource graph atomically"
    end

    loop Each deleted file
        Service->>Storage: "delete_file"
    end

    Service->>Storage: "finish_run"
    Service-->>Caller: "IndexingResult"
```

## Scan and change planning

`IndexingService.scan` first checks TEXT-profile compatibility, then starts a run,
scans root-relative Markdown paths, and delegates hash comparison to storage.
Normal app composition uses `CoreCompatibilityStorage` over the fixed clean-v2
catalog; its run ID does not create a legacy `index_runs` row. Only the retained
direct `SQLiteIndexStorage` adapter records that legacy row. The scanner always prunes
`.git`, `.venv`, `node_modules`, `.mdrack`, and `__pycache__`; configuration adds
patterns such as the default `tests/**` exclusion.

Unless a force reindex is requested, only new and changed files are processed.
Deleted paths are removed. A new path may reuse the document and record identity
of exactly one deleted file with the same source hash; ambiguous matches do not
trigger rename reuse. Files are independent: failures can produce
`partial_success`, while each successfully replaced file remains atomic.

The CLI accepts `scan --changed`, but that flag is currently ignored because
normal scans already perform change detection.

## Selected mixed-folder indexing

`FolderImportService` is an additive application orchestrator, reached by registered
`folder add` and `MDRackEngine.import_folders`. It requires explicit namespace/root
pairs and a selected prepared provider; it never guesses a source root or loops
`IndexingService.scan` over several roots. A private `<store>/folders.json` registry
persists the trusted namespace/root mapping. A namespace cannot silently move to a
different root, and the same root cannot acquire another namespace. Deterministic
resource IDs use namespace plus relative source reference, so two roots containing
`notes.md` stay distinct. Processing B neither deletes nor marks A missing. Missing,
rename and interruption reconciliation remain separate work.

Admission resolves selected roots and checks **physical** store/cache containment
in both directions before catalog creation or encode dispatch. Existing symlink/
junction ancestors are resolved even when the final derived leaf does not exist;
unresolvable owner topology fails closed with the existing store/cache overlap
reason. CLI admission also precedes prepared-provider construction. Sources remain
read-only; the app registry, catalog and configured cache stay outside them.

The folder enumerator retains configured `scan.exclude` patterns, adds explicit
exclusions, and prunes the standard ignored directories. It refuses linked selected
roots and does not follow symlink/junction/reparse entries. Relative file references
remain portable; local registry root paths are not returned or logged. Unsupported
entries and unavailable subtrees have explicit outcomes rather than silently
claiming a complete traversal.

Routing reuses existing owners:

| Folder input | Existing preparation/publication path |
|---|---|
| `.md`, `.markdown` | `ingest_native_text` uses `prepare_selected_document`, the Markdown parser/chunker and conditional document publication. Image syntax remains prose-only; referenced files are never resolved. |
| UTF-8 `.txt` | The same document path selects `prepare_selected_plain_text`: one literal paragraph IR, empty heading path, no Markdown/frontmatter interpretation, canonical structural chunks and exact source spans. The one resource is `document`/`text/plain`, not an extra `raw_text` copy. |
| `.jpg`, `.jpeg`, `.png`, `.webp` | `ingest_native_image` uses the accepted image preparation and conditional image owner, retaining original source locators instead of prepared transport paths. |

Text source capture is bounded to 32 MiB. Structural limits and the selected
provider's actual input contract apply separately; oversized inputs are chunked or
refused, never silently clipped. Complete embedding-space identity is retained;
folder import does not migrate/relabel old vectors or create another persistence
implementation. The document-space preflight precedes document encoding; an existing
image with an incompatible visual space is refused without replacing its graph.

One file's failure does not discard already published resources or stop the remaining
candidates. Progress uses opaque file references and status/counts. Summary/status
and per-file reasons distinguish added, updated, unchanged, skipped, failed and
unprocessed input. An unchanged repeat, including fresh-process reopen, makes zero
prepared-provider encode calls and no duplicate resources/units. Ready-image CAS
bookkeeping need not be write-free. See the
[CLI result/exit contract](../cli-contracts.md#selected-mixed-folders),
[engine surface](public-interfaces.md#selected-mixed-folder-api) and
[remaining limits](limitations.md#selected-mixed-folders).

## TEXT profile admission

`application/embedding_preflight.py::preflight_text_embedding_profile` builds the
existing complete profile and delegates comparison to the application compatibility
owner. `IndexingService.scan` invokes it before run creation, source traversal,
planning/deletion or replacement. `MDRackEngine.scan` and `run_indexer` delegate to
that service. CLI scan/rebuild additionally check before external provider
construction; model switch checks before any control construction/dispatch.
Force, confirmations and rebuild-mode selection do not bypass the check.

The authoritative read selects active app TEXT bindings: document retrieval/whole
text projections that scan can replace, the relevant named slot, and unknown
app-canonical identities including caption/OCR/image-text aggregates. The existing
`embedding-space_` logical-ID kind retains unknown bindings conservatively; it is
not positive compatibility proof. A valid other named slot and unrelated native
TEXT/visual spaces remain independent. Unused space records without active vectors
do not bind an empty collection.

Admission requires the exact profile name, canonical space ID, full fingerprint,
dimensions, metric and existing value-policy/codec compatibility. Neither a model
alias nor matching dimensions alone proves it. An explicit `TextEmbeddingRecipe`
adds its complete versioned policy to that fingerprint; legacy identities remain
unchanged. There is no schema change, old-vector relabeling or hot migration.
Missing/conflicting immutable metadata
remains unknown even for the original model: unsafe writes refuse without adopting
it. Existing queries with a known original identity can remain useful; try new
writes/profiles in a separate collection. Intact compatible bindings still permit
normal same-store scans and rebuilds. The declared injected-storage read and retained
legacy-row transport are described in [public interfaces](public-interfaces.md#injected-indexing-storage).

Profile compatibility and runtime input qualification are separate checks. After
identity admission, CLI scan/rebuild/model switch refuse every explicit LM Studio
HTTP recipe with `embedding_input_contract_unqualified`, before external provider
or lifecycle dispatch, including an empty/new collection and `--rebuild none`.
A compatible fingerprint alone cannot qualify native inputs. The canonical factory
and raw sender own this refusal; see [text recipe admission](system-overview.md#lm-studio-text-recipe-and-input-admission).
Legacy-without-recipe indexing remains available. A directly injected unqualified
HTTP provider reaches the same sender refusal, but existing per-file indexing
bookkeeping still reports `failed` / `FILE_INDEX_ERROR`; it is not an early-run
exception or a successful vector publication. Identity mismatches still refuse
before traversal/run work, regardless of injection.

`IndexingResult.status` distinguishes `success`, `partial_success` and `failed`.
Per-file publication remains atomic; partial execution retains useful completed
resources. Model-switch activation consumes the full status, not a truthy result or
absence of an exception. Exact CLI envelopes and activation/unload ordering are in
[CLI contracts](../cli-contracts.md#mdrack-model-switch-model-and-mdrack-rebuild-embeddings).

## Parser output

`MarkdownItParser` reads UTF-8, computes a SHA-256 source hash, parses YAML
frontmatter, and emits parser-independent `Document` and `SourceBlock` values.
Frontmatter passes through the bounded `metadata-json-v1` normalizer. Supported
JSON values retain their types; YAML dates become ISO strings and sets use
canonical ordering. Unsupported or oversized fields produce value-free
diagnostic categories with deterministic aggregate counts and are omitted by
default. Repeated failures in one category retain their count through
`Document`, both `PreparedFile` paths, the resource envelope, and SQLite.
Malformed or duplicate-key
frontmatter likewise leaves metadata empty while the body still indexes;
explicit strict parsing rejects the resource. Normalized metadata and stable
content/policy fingerprints travel through both `PreparedFile` paths into the
resource-level `source/ingestion/derived` metadata envelope. They are not copied
to chunks, representations, embedding inputs, snippets, or logs. The detailed
contract is [`v1.1-metadata.md`](../contracts/v1.1-metadata.md).
The parser recognizes H1–H6 headings, paragraphs, lists, blockquotes and
callouts, fenced and indented code, Mermaid, tables, thematic breaks, Markdown
images, Obsidian embeds, and HTML `img`. Image syntax is projected to ordinary
prose only when it has a non-empty textual alt/alias; paths, targets, titles,
dimensions, bare/numeric aliases, and referenced files are ignored.

Every block has a one-based line span. The structural path also carries half-open
character offsets `[start_offset, end_offset)`. Heading paths are ordered tuples
and become JSON arrays at public boundaries.

## Stable identities

- A document logical ID is based on the root identity and relative path. The legacy
  scan may reuse the prior identity for an unambiguous exact-content move; the
  selected-folder path uses its durable namespace and relative source reference
  and makes no rename-reconciliation promise.
- A block logical ID combines document identity, block kind, normalized heading
  path, content fingerprint, duplicate ordinal, and parser version.
- A chunk logical ID combines document identity, parent block IDs, chunk kind,
  display-content fingerprint, duplicate ordinal, and chunker version.
- SQLite record UUIDs remain replaceable storage details.

## Structural chunker v2

Headings, frontmatter, and thematic breaks shape context but do not become
retrieval chunks. Other blocks use type-specific policies:

| Block | Current policy |
|---|---|
| Paragraph | Split by paragraph, sentence, word, then character while retaining every separator in an owned source slice. |
| List / blockquote | Use exact source slices; small compatible drafts may merge under the same heading and kind. |
| Callout / unknown | Split as bounded source slices but do not merge. |
| Python code | Prefer top-level class, function, and async-function AST boundaries; invalid Python falls back to line windows. |
| Other code / Mermaid | Use bounded line windows; an oversized individual line is fragmented into exact bounded slices. |
| Table | Repeat header rows for retrieval; a single unrepresentable row/header uses a bounded hash marker. |
| Image syntax | Eligible alt/textual alias participates once as ordinary prose; no image-reference block, asset graph, file access, or image resource is created. |

Normal prose uses `target_chunk_chars`; every output also obeys
`hard_limit_chars` and the estimated-token limit. `min_chunk_chars` only enables
safe merging when one compatible side is short. The configured
`overlap_chars` value is validated and passed into structural configuration but
is not consumed by `StructuralChunker`; current structural chunks do not overlap.

`display_content` is the persisted retrieval text. `embedding_text` adds a
heading and content-kind prefix when applicable; it is not assembled from
neighboring chunks.

## Persistence handoff

The application still prepares the compatibility `PreparedFile` metadata, sections,
chunks and optional vectors. Normal composition projects it into one complete core
resource graph; it does not duplicate that graph into legacy SQL tables. The core
validates ownership and UTF-8/JSON/vector invariants before the resource adapter
opens its serialized transaction. Any write/FTS/vector/facet failure leaves
the prior complete graph visible.

## Primary source anchors

- Scan orchestration and identities: `src/mdrack/application/indexing.py`
- Mixed-folder selection/registry/routing: `src/mdrack/application/folder_import.py`
- Selected document publication: `src/mdrack/application/native_text.py`
- Parser: `src/mdrack/adapters/markdown_it/parser.py`
- Structural policies and exact spans: `src/mdrack/application/chunking.py`
- Scanner: `src/mdrack/indexing/scanner.py`
- Admission and core projection: `src/mdrack/application/embedding_preflight.py`,
  `src/mdrack/application/compatibility.py`
- Authoritative metadata port: `src/mdrack/ports/storage.py`
- Retained direct legacy adapter: `src/mdrack/adapters/sqlite/index_storage.py`
- Metadata normalization: `src/mdrack/application/metadata_normalization.py`
- Provider-free validation:
  `packages/mdrack-core/src/mdrack_core/application/indexing.py`
- Domain records: `src/mdrack/domain/blocks.py`,
  `src/mdrack/domain/documents.py`, `src/mdrack/domain/chunks.py`

Explicit raw text ingestion is a separate CLI-owned producer in
`src/mdrack/ingestion/raw_text.py`. It reuses the Markdown parser/chunker for
Markdown, treats plain text as literal prose, and guards the single core write
with an R0 after-read source check. That `raw_text` producer remains separate from
both single-root Markdown scan and the selected mixed-folder `document` path.
