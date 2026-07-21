# Prepared-resource manifest v1

`mdrack.application.manifest` converts between one untrusted JSON document and one
`PreparedResourceBatch` and can submit the decoded graph to an explicitly supplied core catalog.
The codec and facade do not import Click, open files or locators, resolve binary
sources, call providers, or use the network.

## Contract

The top-level discriminator is exact:

```json
{
  "contract": "mdrack.prepared-resource",
  "version": 1,
  "resource": {},
  "representations": [],
  "units": [],
  "spaces": [],
  "vectors": [],
  "facets": []
}
```

All top-level and record fields are closed: missing required fields and unknown
fields fail. The normative field grammar is published in
[`prepared-resource-manifest-v1.schema.json`](prepared-resource-manifest-v1.schema.json).
The Python codec remains authoritative for byte, depth and privacy-safe error
behavior that JSON Schema cannot express.

A manifest represents exactly one complete resource graph. It may contain text,
ready vectors, opaque locator values and producer fingerprints. It must not contain
binary payloads, provider responses, API keys or database record IDs. Locator values
are stored as opaque JSON; import never dereferences them.

## Fixed limits

Version 1 limits are not configurable:

| Boundary | Limit |
|---|---:|
| Raw UTF-8 JSON | 16,777,216 bytes |
| JSON nesting | 32 levels |
| Each record collection | 100,000 items |
| Space/vector dimensions | 8,192 |
| Metadata per record | 65,536 UTF-8 JSON bytes |
| Each text/title field | 8,388,608 UTF-8 bytes |

The policy is reject, never truncate. Domain records may impose stricter limits on
nested opaque JSON. Large imports are split into one manifest per resource;
streaming import is deferred.

## Validation and writes

`decode_prepared_resource_manifest(payload)` performs strict UTF-8 and JSON parsing,
rejects duplicate keys and non-finite numbers, enforces the closed grammar and fixed
limits, then constructs the typed batch.

`encode_prepared_resource_manifest(batch)` runs the same canonical graph validation
and emits deterministic compact UTF-8 JSON using this exact contract and version.
Record collections are ordered by logical identity (units by representation and
ordinal); mapping keys are canonicalized. Decoding the default export reconstructs
the same semantic graph. `include_vectors=False`, `include_text=False`, and
`redact_source_metadata=True` are explicit projections. A projection that leaves a
unit with neither text nor a vector fails with `invalid_graph` instead of emitting an
unimportable manifest.

`import_manifest(catalog, payload)` additionally runs `CoreIndexingService` graph
validation before exactly one `catalog.replace_resource()` call. Invalid ownership,
duplicate IDs, dangling relationships, vector mismatches and other graph errors
therefore cannot open an adapter transaction. `index_prepared_resource(catalog,
batch)` provides the same explicit-catalog path for an already prepared batch.

`PreparedResourceCatalog.open(catalog_path)` is the public Click-free lifecycle
facade for an existing clean `mdrack_sqlite_catalog_v1` database. Its
`import_file`, `import_bytes`, `export_file`, `export_bytes`, `export_batch`,
`inspect`, and `delete` methods always use the
explicit path; they never select or modify the application's configured/default
store. `inspect` returns only logical identity, aggregate counts, kinds, and
SHA-256 fingerprints. It never returns source namespace, title, text, metadata,
facet values, vectors, producer values, or locator payload.

The matching CLI is singular and path-explicit:

```text
mdrack resource import <manifest.json> --catalog <catalog.sqlite3>
mdrack resource export <resource-id> --catalog <catalog.sqlite3> --output <manifest.json>
mdrack resource inspect <resource-id> --catalog <catalog.sqlite3>
mdrack resource delete <resource-id> --catalog <catalog.sqlite3>
```

Each invocation writes exactly one JSON envelope to stdout. Runtime failures use
fixed messages and stable codes; paths, payloads, IDs from failed requests, raw
SQLite errors, and exception text are not serialized or logged.

Export defaults to the complete semantic graph, including text, vectors and source
metadata. The manifest file is therefore an intentional sensitive payload. Use
`--no-text`, `--no-vectors`, or `--redact-source-metadata` only when that reduced
graph remains importable. Export creates a new output file and refuses to overwrite
an existing path. The stdout envelope contains only logical identity, counts, byte
size and a SHA-256 digest, never manifest content or the output path.

Failures expose only stable `ManifestErrorCode` values. Untrusted text, metadata,
facets, vectors, locator payloads and raw parser exceptions are never included in the
exception string.

## Deferred surfaces

Streaming import/export, source resolution, implicit embedding, catalog creation,
default-store selection, and export of unreferenced global embedding spaces remain
deferred. Catalog export takes one SQLite read snapshot and includes only spaces
referenced by the selected resource's vectors; it does not widen the frozen core read
port or add another version-1 grammar.
