# MDRack 1.3.0 base release candidate

Status: ready for independent review; not published.

This candidate releases the compact SQLite base independently of the optional
experimental `mdrack-sqlite-vec` package. The authoritative machine-readable
record is the [base release packet](evidence/v1.3.0-base-release-packet.json);
its analyzer and benchmark reports are linked from that packet. No package upload,
Git tag, push, deployment, or release publication is claimed here.

## Version set

| Distribution | Version | Release role |
|---|---:|---|
| `mdrack` | `1.3.0` | Application and Click/engine surface. |
| `mdrack-core` | `1.0.0rc1` | Existing provider/storage-neutral core contract. |
| `mdrack-media` | `1.0.0rc1` | Existing provider-free media records contract. |
| `mdrack-sqlite` | `1.0.0rc2` | Fresh v2 clean catalog, codec/backend registry, and builtin exact search. |

`mdrack-sqlite` RC2 is a reviewed release-candidate increment rather than a final
1.0 declaration. It adds the separately identified `mdrack_sqlite_catalog_v2`
clean history while retaining the v1 compatibility API. The base application pins
RC2; it has no `mdrack-sqlite-vec` dependency.

## Included behavior

- Fresh compact generations are created from the independent v2 manifest
  (`0000_identity` through `0004_vector_encoding`). A v1 catalog or app-bridge
  `0007` database is not upgraded, copied, or read as a fresh-rebuild source.
- The fresh application path stores canonical `ieee754-f32-le-v1` vector payloads
  and uses the builtin exact linear backend. This is a local SQLite/Python design,
  not an ANN, `sqlite-vec`, or vector-database claim.
- `mdrack storage rebuild-fresh` creates an inactive candidate from authorized
  source Markdown; `storage verify` checks it; `storage activate` performs the
  explicit one-way cutover only for a verified v2 candidate.
- `mdrack storage-analyze` reports aggregate catalog/vector payload and
  codec/backend information without exposing source content, paths, locators,
  vectors, endpoints, or private exception text.

## Fresh-reindex cutover

1. Stop writers and long-lived readers, then preserve the complete store directory
   (database, WAL/SHM, metadata, and active pointer).
2. Rebuild into a new inactive candidate with `mdrack storage rebuild-fresh` using
   the separately authorized embedding provider/profile.
3. Record the returned generation ID and run `mdrack storage verify GENERATION_ID`.
4. Under one-writer quiescence, run `mdrack storage activate GENERATION_ID`.
5. Confirm the selected generation through `mdrack status` and `mdrack doctor`.
   Retain only their privacy-safe aggregate output.

The activation is one-way. It does not treat a retained legacy generation as a
runtime rollback target. If a different catalog is required after activation,
preserve the current state and use a separately authorized fresh rebuild/cutover;
do not reverse migrations, copy rows, or delete retained generations as part of
this release procedure. See [recovery procedures](recovery.md) for the operational
boundaries.

## Evidence and non-claims

The packet records local Linux/Python 3.11 evidence for base wheel/sdist build,
installed-package smoke, fresh-v2 analyzer output, deterministic synthetic
benchmark/parity source hashes, tests, lint/type/boundary gates, and privacy
checks. Its artifact rows include exact filenames, byte counts, SHA-256 hashes,
and the artifact-matrix digest; generated artifacts remain outside versioned
source.

The candidate does not claim real-vault or user-source coverage, live LM Studio or
other external provider behavior, Windows, Python 3.12, semantic quality,
production latency/RSS SLOs, optional sqlite-vec behavior, package publication,
or destructive cleanup authorization.

## Optional sqlite-vec status: not promoted

The separately maintained `mdrack-sqlite-vec` source package remains an
experimental compatibility probe, not a released MDRack accelerator. Its current
[non-promotion packet](evidence/v1.3.0-sqlite-vec-nonpromotion.json) records a
local Linux CPython 3.11 probe with the exact `sqlite-vec==0.1.9` pin. The pin,
float32 dimensions, cosine/L2, metadata scope, delete, transaction rollback, and
extensionless-reopen observations pass, but the deterministic tie-boundary gate
fails with `tie_boundary_requires_full_scan`.

MDRack therefore keeps `builtin-exact-v1`. The base distribution defines no
`mdrack[sqlite-vec]` extra, and this release ships no sqlite-vec production
backend, plugin wheel, or plugin sdist. The probe result is not permission to
create vec0 catalogs, route searches through sqlite-vec, or fall back after a
partial extension-backed write.
