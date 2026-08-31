# MDRack 1.3.0 release notes

Status: the committed licensing-ready checkpoint before the final publication
rebuild is `40029cc63d753747aed2290a4a95cb49e83d6884`
(`feat(licensing): publish verified distribution policy`). The final clean
candidate's exact source identity and artifact hashes are recorded in the base
release packet before any index upload. A checkpoint is not itself a Git tag or
an index publication: no Git tag, PyPI upload, deployment, or package-index
publication is claimed by this document alone.

The compact SQLite base was prepared independently of the optional experimental
`mdrack-sqlite-vec` package. The authoritative machine-readable candidate record
is the [base release packet](evidence/v1.3.0-base-release-packet.json); its
analyzer and benchmark reports are linked from that packet. The packet is a
point-in-time pre-publication snapshot. Its `published: false` classification
describes candidate verification before the irreversible package-index
operation. Publication is claimed only after
index read-back confirms the uploaded files and hashes.

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

## Candidate identity and package order

The packet's `source_snapshot` identifies the candidate by a sorted SHA-256
manifest over all tracked paths. Only
`docs/evidence/v1.3.0-base-release-packet.json` is excluded so the packet does
not describe its own bytes. Every other tracked change, including a documentation
change, creates a different candidate and needs a fresh packet plus two matching
artifact builds before publication can be considered.

The local application metadata pins core, media, and SQLite in that order. If
the four distributions are ever authorized for upload, publish their exact
wheel/sdist pairs in dependency order: `mdrack-core==1.0.0rc1`,
`mdrack-media==1.0.0rc1`, `mdrack-sqlite==1.0.0rc2`, then
`mdrack==1.3.0`. This is an upload order for a future release operation, not a
claim that any package is currently available from an index.

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

## External publication sequence: not run

The local packet and installed-package smoke are prerequisites, not publication
evidence. The following sequence requires new explicit authority for each
network or irreversible step and was not performed for this checkpoint:

1. Rebuild the exact clean, reviewed candidate twice outside the checkout and
   require the eight filename/byte/SHA-256 rows to match the packet. Dispatch the
   hosted matrix for that exact pushed commit; all four cells (Linux and Windows,
   Python 3.11 and 3.12) must report `passed`. A local Linux run does not fill a
   Windows or Python 3.12 cell.
2. Before TestPyPI, query the expected version for all four distributions. With a
   separately supplied `UV_PUBLISH_TOKEN`, upload each exact wheel/sdist pair to
   TestPyPI in the dependency order above using `uv publish` with
   `https://test.pypi.org/legacy/` and
   `https://test.pypi.org/simple/`. Read the returned index files back and match
   every SHA-256 against the local build.
3. From two fresh environments outside the checkout, install
   `mdrack==1.3.0` from TestPyPI plus PyPI for third-party dependencies: once
   normally and once with `--no-binary mdrack`. Run
   `scripts/check_installed_package.py` with `PYTHONPATH=` for both. This is the
   package-index installation evidence that the local smoke cannot provide.
4. Only after all TestPyPI hash and install checks pass, repeat the same ordered
   upload, hash read-back, and two install checks at PyPI. A duplicate version or
   hash, partial upload, resolver/import error, or hash mismatch is an abort:
   stop without delete, overwrite, retag, or GitHub Release.
5. Only after PyPI passes and with a further explicit owner command, verify the
   remote commit/tag state, create and push annotated `v1.3.0` at the accepted
   commit, read it back, then create a GitHub Release targeting that same SHA.

No later live model/provider-quality, real-source, optional accelerator, or
destructive-cleanup work is silently promoted by this publication sequence.

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
