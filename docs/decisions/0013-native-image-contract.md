# ADR-0013: prepared embedding and native image source contract

Status: implemented source candidate, independent review pending.

The app advances to 1.4.0rc1 for the new versioned media API and closed exports.
`mdrack-core==1.0.0rc1`, `mdrack-media==1.0.0rc1` and
`mdrack-sqlite==1.0.0rc2` pins and their contracts are unchanged. The change is
app-owned: provider payloads, resolver authority, planning, public projection and
CLI. No generic core/SQL/media API changes, migrations or CAS implementation.
Declared Python support remains >=3.11; current execution evidence is Windows
CPython 3.14.2 only. Lock maintenance uses uv 0.11.15 offline without upgrading
third-party dependencies. Historical release packets retain their old identity;
strict historical validators must not accept this new candidate as 1.3.0.

The [native image contract](../contracts/native-image-v1.md) freezes names and
closed fields. Existing text APIs, embedding fingerprints and query instructions
remain unchanged. The new provider port lives beside the legacy text protocol.
Canonical space records, including metadata, are independent of input modality.
Unsupported selected transport refuses before source resolution or DB creation.
No file name/caption/base64-as-text fallback, no extra encoder, no model loaders.

`MediaSearchService` owns policy; core owns execution, grouping and RRF. A single
lexical and vector family cap representation voting. Candidate scope and target
are independent of query modality; image hybrid is vector-only without a false
partial outcome. Stored vector similarity is separately permitted by capability.
All space resolution, basis reads, branches and eager hydration share one
operation-owned reader. Provider await/preparation precedes it.

`SourceResolver` opens only caller-selected Paths or host-provided namespace/ref
bindings. Guarded raw snapshots carry revision/signature/budget facts and never
become durable authority. The host owns mappings needed by later operations;
search/read reopen from SQLite without reopening original sources. Immutable
prepared image bytes are accepted as queries. Missing/changed/over-budget inputs
refuse safely. Late enrichment and conditional publication remain W3/W4.

LM Studio's supported selected text transport does not prove pixels capability.
The production image route is explicitly unsupported. Explicit synthetic CLI and
injected providers demonstrate offline/local orchestration, not runtime or quality.
This ADR is not a publication, release verdict or independent acceptance.
