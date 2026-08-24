# MDRack 1.3.0 release notes

Status: release-preparation source is maintained in the checked-out candidate;
the exact source identity and artifact hashes are recorded in the base release
packet. No Git tag, PyPI upload, deployment, or package-index publication is
claimed.

The compact SQLite base was prepared independently of the optional experimental
`mdrack-sqlite-vec` package. The authoritative machine-readable candidate record
is the [base release packet](evidence/v1.3.0-base-release-packet.json); its
analyzer and benchmark reports are linked from that packet. The packet is a
point-in-time pre-publication snapshot, so its `published: false` classification
and no-push non-claim describe packet generation, not the later ordinary source
push. No package upload, Git tag, deployment, or package-index publication is
claimed here.

## Version set

| Distribution | Version | Release role |
|---|---:|---|
| `mdrack` | `1.3.0` | Application and Click/engine surface. |
| `mdrack-core` | `1.0.0rc1` | Existing provider/storage-neutral core contract. |
| `mdrack-media` | `1.0.0rc1` | Existing provider-free media records contract. |
| `mdrack-sqlite` | `1.0.0rc2` | Fresh v2 clean catalog, codec/backend registry, and builtin exact search. |

`mdrack-sqlite` RC2 is a reviewed release-candidate increment rather than a final
1.0 declaration. It adds the separately identified `mdrack_sqlite_catalog_v2`
clean history while retaining the v1 library compatibility API. That compatibility
surface is for direct library callers; it is not a runtime/store fallback. The
MDRack application accepts only its fixed clean-v2 `catalog.sqlite3` contract and
rejects v1 stores. The base application pins RC2; it has no `mdrack-sqlite-vec`
dependency.

## Included behavior

- Normal application composition creates or opens exactly one clean v2 catalog at
  `<store>/catalog.sqlite3`. A v1 catalog, app-bridge `0007` database, legacy
  `knowledge.db`, or mixed SQLite layout is rejected rather than upgraded, copied,
  selected, or read as an application store.
- The clean application path stores canonical `ieee754-f32-le-v1` vector payloads
  and uses the builtin exact linear backend. This is a local SQLite/Python design,
  not an ANN, `sqlite-vec`, or vector-database claim.
- `init`, `scan`, and `MDRackEngine.scan()` are the only normal creation/open
  paths. They neither mutate source Markdown nor fetch external assets.
- `storage-analyze` reports aggregate catalog/vector payload and codec/backend
  information without exposing source content, paths, locators, vectors,
  endpoints, or private exception text.

The direct local media commands are bounded caller-authorized adapters, not
built-in media quality features:

```text
mdrack ingest audio SOURCE_PATH --source-ref REF --allow-external-stt --stt-command COMMAND
mdrack ingest raw-video SOURCE_PATH --source-ref REF --allow-external-video-extractor --video-extractor-command COMMAND
```

They accept RIFF/WAVE and ISO-BMFF input through shell-free stdin protocols.
They do not claim built-in transcription/decoding, pixel or acoustic search,
live provider quality, Windows, Python 3.12, or real-source coverage.

## Fixed-store recovery boundary

MDRack 1.3 has no normal candidate generation, activation, rollback, retention,
or old-store migration command. There is no one-way candidate activation or
cutover procedure in the normal application. On a schema or integrity failure, stop writers,
preserve the complete fixed-store directory (including WAL/SHM sidecars), and
use the privacy-safe `status`, `doctor`, and `storage-analyze` output for
diagnosis. Deleting or recreating the derived catalog is destructive and
separately authorized; it is not a runtime rollback procedure.

For a reproducible synthetic acceptance check, use the
[one-store acceptance runner](one-store-acceptance.md). It exercises the current
CLI/engine paths, temporary local SQLite state, fake embeddings, source-hash
checks, privacy sentinels, and a temporary installed-wheel target. It does not
make a live-provider or real-source claim.

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

The current release-document gate is `scripts/check_v13_release_packet.py`.
`scripts/check_release_docs.py` validates the historical v0.4 packet against its
former candidate bytes and is not a current MDRack 1.3 readiness gate.

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
