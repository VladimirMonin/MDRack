# ADR-0016: Prepared enrichment, preserving reindex and real artifact custody

Status: implemented and independently accepted as the prepared W4 component on Windows/CPython 3.14.2 source/local components, actual Root and non-editable installed packages; W4-R2 closed.

## Context

[ADR-0015](0015-ready-ocr-publication.md) closed the first ready-OCR family, not
M03-06. Other ready media projectors, dense/read-only intents, ordinary producer
reconciliation, source transitions and host-owned retention need the same dependency
and publication semantics without invoking models or adding another SQL owner.

## Decision

Implement the additive [prepared enrichment v1](../contracts/prepared-enrichment-v1.md)
application contract. Reuse the pure dependency owner and provider-free transcript /
video `.prepare()` methods. Project real prepared text, typed evidence and supplied
vectors; preserve native/independent data and let the existing Core validator and
SQLite CAS/withdraw ports remain authoritative.

Use explicit purpose/variant slots and captured pre-I/O input revisions. Profile /
recipe changes are not independent purpose variants. For composite video, derive
separate component material revisions so changing frames alone does not invalidate
a transcript-only dependent. Whole-resource textual aggregates explicitly depend on
their component artifacts. Never infer a raw source generation from a prepared hash
or bless an old result with a newer snapshot. Managed normal typed-artifact ingestion
requires the preparer's original source/lifecycle binding for existing resources;
initial unbound creation is explicitly a prepared-catalog binding, not observed raw
media. Opaque staged operations remain bound to their owning writer context.

Provide an explicitly bound conditional reindex consumer around the unchanged full
manifest-v1 codec. Keep explicit unconditional import/replace semantics unchanged.
Changed source removes stale source-dependent outputs; only explicitly independent
material survives. Source change without a remaining projection uses withdrawal,
not an invalid empty batch. Delete/recreate and withdrawal cannot reauthorize old
prepared jobs. Bound retries reuse ready values and do not rerun providers.

Give the host a distinct immutable archive with real bounded bytes, readback, flush
and atomic directory publication. Reuse the existing private no-follow filesystem
adapter, never the ArtifactCache lifecycle/catalog. Archive and cache/store roots
cannot overlap. Caller files and unknown directories are not cleanup targets.
Historical artifact reads do not open/create the Core catalog or publish FTS.
Missing custody is explicit unavailability; metadata flags are not durability.

## Consequences and qualification boundary

The three new public types are additive; the original 37 ordered exports remain
an exact prefix of the closed 40-name app inventory, including installed/documented
consumers. No Core/media/SQLite/shared validator/schema 0005/lock/version changes
are needed. The specialized ready-OCR API retains its narrower contract.

Offline component oracles exercise public engine/CLI, actual file SQLite FTS and
vectors, manual correction, no-text removal, source transition, withdrawal/reentry,
delete/recreation, real connection conflicts and archive/cache/reopen behavior.
Synthetic prepared values are not model/runtime or retrieval-quality evidence.
Completed independent checks bind actual Root, assertions and fresh installed
payloads. Affected strict typing is qualified without suppressions; inherited
whole-repository static debt is not green. The existing Core publication importer
inventory is corrected in documentation, without changing neutral implementation.
Live-runtime, quality/cost, W5/W6 and release claims require separate qualification.
