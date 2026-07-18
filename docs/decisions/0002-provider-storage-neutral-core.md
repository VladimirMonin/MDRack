# ADR-0002: Provider- and storage-neutral retrieval core

- **Status:** Accepted and implemented for the v0.3 compatibility release
- **Date:** 2026-07-17
- **Decision owners:** MDRack maintainers
- **Applies to:** MDRack v0.3 plan and all implementation slices derived from it
- **Baseline:** `e7eeb3cda2d10836f4dffe03c0ec134d448026d5`
- **Audited inputs:** architecture `8989b19f65cfaa635ad99356495d3e7f6a448f8e47ec658dc120d2cd03834f57`; storage `b8cb03c0161655383d72ba590929dc813155dc50719d94a6277d82842bf083d1`; compatibility/privacy `f1f2bdc1962cda94751a38a4a3b04af81898a906078d94e3bcdcdcc7996bcec2`; synthesis `c7fd97cc636c24e891d7624102e80b902236ae0743c2b105ad253ee6e6cfbb74`

## Context

The v0.2 application combines Markdown preparation, provider calls, chunk-oriented
public compatibility DTOs, and one SQLite implementation. Protocols exist, but the
reusable retrieval boundary is not physically isolated: indexing prepares Markdown
and assets and obtains embeddings, retrieval obtains a query embedding, and current
storage contracts expose v0.2 records.

The v0.3 target needs one portable kernel for prepared resources, search units, and
vectors. The kernel must be independently copyable and must not acquire provider,
Markdown, Click, HTTP, or persistence behavior. The index is derived data, but an
upgraded SQLite file is not executable rollback evidence: the v0.2 migration runner
rejects an unknown `0007` ledger entry.

This ADR records the owner-approved Gates A–D and the audited preconditions that
governed the staged implementation. The baseline context above is historical; the
checked-out v0.3 package now implements the accepted core, compatibility, schema,
generation, privacy, image-ingestion, and retrieval slices described below.

## Decision

### A. Physical package boundary

Create `src/mdrack_core/` as a separate import root in the existing MDRack
distribution. A second repository or PyPI distribution is deferred until there is a
proven second consumer.

The dependency direction is:

```text
mdrack -> mdrack_core
mdrack_core -X-> mdrack
```

`mdrack_core` is standard-library-only and must not import or initiate Click, HTTP,
SQLite, Markdown parsing, model/provider, filesystem, or network behavior. It must
not import current `mdrack` domain or public compatibility DTOs.

The current `Document`, `SourceBlock`, `RetrievalChunk`, `PreparedFile`,
`SourceLocator`, `EmbeddingProfile`, and legacy retrieval DTOs remain owned by
`mdrack` for the compatibility release. They are mapped at the application edge;
they are not aliases of new core records.

### B. Core ownership and contracts

The core owns provider-neutral resource/representation/unit/vector/facet records,
graph validation, retrieval orchestration, branch-local resource grouping,
weighted RRF, exact-duplicate and similarity contracts, stable error/degradation
categories, and privacy-safe lifecycle event schemas.

The application package owns source scanning, Markdown/image preparation,
source-specific deterministic ID generation, query-vector preparation, provider
calls, compatibility mapping, CLI/engine composition, and runtime diagnostics.

IDs are caller supplied. Producers in `mdrack` generate deterministic resource,
representation, and unit IDs using source semantics. Core validates non-empty values,
uniqueness, relationships, and batch ownership but never regenerates source IDs.

Every string that can cross the catalog persistence boundary, including locator and
metadata JSON keys and values, must be UTF-8 encodable. This rejects unpaired
surrogates without normalizing or otherwise narrowing valid Unicode. Finite vectors
preserve their exact floating-point values across catalogs, including the sign of
`-0.0`; an adapter must not normalize signed zero.

Core ports are frozen as complete use-case surfaces before retrieval and indexing
lanes diverge:

- `ports/catalog.py`: atomic replace/delete, resource/unit/vector reads, exact-hash lookup;
- `ports/search.py`: lexical and vector candidate search with scope applied before limit;
- package exports are owned by the contract-freeze stage only.

Core observability has one small shared redaction/event primitive. Retrieval and
indexing own their event payload schemas without editing the shared primitive after
contract freeze.

There must be exactly one production implementation owner for each of: graph
validation, weighted RRF, branch grouping, source ID generation, query-vector
preparation, compatibility mapping, migration identity, and active-store switching.
Compatibility wrappers delegate; they do not retain competing algorithms.

### C. SQLite generations, migration identity, and rollback

V0.3 is built in a separate candidate database/store generation. It is never built
into the active v0.2 database. Under one-writer quiescence, the application verifies
and closes the candidate, checkpoints and fsyncs required files, and atomically
switches an application-owned active-generation pointer.

Rollback switches the pointer to the untouched v0.2 generation. It never asks a v0.2
build to open a database containing `0007`. The complete v0.2 generation is retained
read-only for at least one compatibility release. Cleanup or deletion is a separate,
explicit destructive operation.

The migration runner enforces a compiled expected schema version and exact ordered
migration manifest/digest independent of directory discovery. It rejects a package
missing an expected migration before applying SQL and retains fail-closed handling of
unknown future versions.

Schema version and readiness are separate. A durable store-generation state machine
must represent at least:

```text
legacy_only -> rebuild_required -> building -> ready
                                      |-> failed
```

The state includes generation identity and resource-contract readiness. Production
search/write must reject every non-`ready` generation; table existence or zero rows is
not readiness evidence.

`replace_resource()` initially owns one synchronous, serialized SQLite transaction
and rejects an already-active caller transaction. All validation and provider/
filesystem work completes before the transaction opens. Resource graph, manual FTS,
vectors, facets, invariant checks, and commit share that transaction. Any failure
preserves the previous complete graph. No fixed reusable savepoint name or surprise
commit of caller-owned work is allowed.

Migration `0007` was authored only after an independent data/schema review mapped
every frozen contract to DDL. That accepted review settled:

- explicit foreign-key actions and cross-resource representation/unit integrity;
- canonical source identity, uniqueness, upsert, and rename semantics;
- non-empty/type/range checks, ordinal scope, and orphan cleanup;
- NULL-safe facet deduplication;
- deterministic vector codec, finite-number checks, dimensions, metric dispatch,
  fingerprint/space compatibility, and empty-vector policy;
- filter/join indexes required to apply scope before candidate limits;
- candidate path, lock, verification, checkpoint/fsync, pointer switch, reader,
  interruption recovery, retention, and cleanup semantics.

Migrations `0000`–`0006` remain immutable. `0007` is create-only, changes no legacy
row, performs no backfill, and drops no legacy table.

### D. Markdown image policy and direct image capability

Markdown image syntax contributes only author text:

- Markdown/HTML alt text contributes ordinary prose exactly once;
- a textual Obsidian alias contributes ordinary prose exactly once;
- bare embeds, empty aliases, numeric size/width, path, target, title, dimensions,
  and `src` contribute nothing;
- surrounding prose is not duplicated.

Markdown scanning never resolves, stats, opens, hashes, MIME-probes, or diagnoses a
referenced image. It creates no image resource, asset row/reference, or
`IMAGE_REFERENCE`/`image_reference` unit. Source bytes and stable document/unit IDs
must not depend on target-file existence.

Direct image ingestion is an explicit local-file app/CLI/API operation, never a side
effect of Markdown scan. The minimum v0.3 capability is OCR or caption text, ready
text embeddings, SQLite persistence, and resource-filtered lexical, semantic, and
hybrid search. Full generated text is retained; core performs no hidden truncation.
The default is one `whole_resource` unit per bounded representation. Image bytes stay
outside SQLite.

Text and visual vectors use distinct explicit embedding spaces even when dimensions
match. Deterministic fake extraction/vectors plus real local SQLite/files prove the
offline orchestration contract. Live LM Studio OCR/caption and visual embeddings are
opt-in, separately authorized, and non-blocking for the core release.

## Compatibility policy

All surfaces in
[`docs/compatibility/v0.3-compatibility-registry.md`](../compatibility/v0.3-compatibility-registry.md)
remain app-owned façades for one compatibility release. Existing CLI envelopes,
legacy search DTO key sets, score/rank/nullability, heading arrays, locators, and
CLI/engine parity are preserved where the registry says `SURVIVE`. New resource/unit
DTOs are discriminated contracts and never repurpose `chunk_id` or expose SQLite IDs.

File-layout relocation of LM Studio may happen early only behind compatibility
re-exports. Semantic importer cutover waits until app-side query/index preparation
exists. Compatibility removal requires the registry's importer and installed-package
oracles, not merely absence of one local call site.

## Privacy and evidence

Logs, stderr, diagnostics, eval/support/release artifacts, and failure paths must not
contain raw query/content/generated text, paths, root names, endpoints or endpoint
fragments, metadata/facet values, vectors, provider bodies, or sensitive exception
text. Stable event names, categories, counts, sizes, dimensions, fingerprints, and
durations are permitted. Intentional public result payloads are tested separately
from observability.

Evidence labels are cumulative only within their actual boundary:

- `unit/offline` — fakes and fixtures;
- `local components` — stated real local SQLite/filesystem/provider process;
- `installed package` — built artifact outside the source import path;
- `real source` — separately authorized source corpus with before/after evidence;
- `live external` — real external runtime request/response and cleanup;
- `Windows` — execution on Windows.

A PASS at one boundary must not be promoted to another. Fake vectors do not prove a
live model, local source fixtures do not prove a real vault, and Linux does not prove
Windows.

## Implementation sequencing and review gates

The following is the historical implementation ledger, retained to explain dependency
order. The completed sequence was ADR/plan amendment and review; baseline/guard; core
domain; complete contract freeze; then retrieval and indexing lanes. SQLite work was
split into schema review, migration identity/readiness, create-only schema and adapter,
then candidate rebuild/cutover/recovery. App compatibility cutover preceded complete
Markdown asset removal; privacy/LM Studio cutover preceded direct image acceptance;
facets/similarity and final release evidence followed.

Each semantic slice is implemented, independently reviewed at an exact revision, and
only then committed by its designated writer. Push remains a separate owner gate.

## Consequences

### Positive

- The core can be copied and tested without providers, Markdown, Click, or SQLite.
- SQLite and a future PostgreSQL adapter can share use-case contract tests.
- Rollback is an executable generation switch rather than a same-file claim.
- Public v0.2 consumers receive a bounded compatibility interval.
- Markdown references and explicit image ingestion have unambiguous product ownership.
- Evidence remains honest across fake, local, installed, real-source, platform, and live boundaries.

### Negative

- V0.3 retains a staged compatibility layer and more than one storage generation.
- Schema and cutover implementation required contract and data review before mutation.
- Legacy tables and a full old generation consume storage for at least one release.
- The initial core does not provide Postgres, live vision, audio/video, ANN, or reranking.

## Stop conditions

Stop the current stage and return to the owning reviewed contract if it needs to
change a frozen DTO/port/ID/transaction rule, creates overlapping writers, introduces
a second production algorithm owner, writes SQL before schema PASS, targets the
active v0.2 database, accepts a non-ready generation, alters compatibility without
parity evidence, touches referenced images during Markdown scan, leaks a privacy
sentinel, overstates evidence, or lacks review of the exact revision.

## Current implementation boundary and non-claims

The checked-out v0.3 package implements and tests `mdrack_core`, create-only migration
`0007`, store generations, compatibility mapping, direct image ingestion, and the
provider-neutral resource/retrieval contracts. This ADR does not claim a
PostgreSQL/pgvector adapter, live OCR/caption/visual execution, audio/video ingestion,
Windows execution, or a real-source run. Those evidence boundaries remain separate
and require explicit authorization.
