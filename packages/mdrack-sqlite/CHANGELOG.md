# Changelog

## 1.0.0rc3

- API 1.0.0rc2 implements optional publication snapshot/CAS/withdraw/delete, local lifecycle/token.
- Add immutable v2 migration 0005_publication; preserve applied 0000–0004.
- Explicit exact old-v2/0004 maintenance upgrade with verified external backup and atomic transition.
- Share graph reader with export and internal graph writer across unconditional/conditional paths.
- Serialize FTS repair without source extraction or semantic token change.

## 1.0.0rc2

- Publish the independent `mdrack_sqlite_catalog_v2` clean history (`0000`–`0004`)
  for fresh compact catalogs, including exact vector codec and backend registries.
- Preserve `create()` and the v1 bridge lifecycle for compatibility; `create_v2()`
  creates v2 directly and never upgrades, copies, or backfills v1/app-bridge bytes.
- Keep builtin exact vector search in the base adapter. `mdrack-sqlite-vec` is not a
  dependency of this release candidate.

## 1.0.0rc1

- Publish the frozen catalog lifecycle API for existing bridge databases.
- Establish `mdrack_sqlite` as the single resource catalog/search adapter owner.
- Preserve the legacy app import path as a compatibility re-export.
- Freeze privacy-safe lifecycle errors, transaction ownership and verification.
- Add the independent `mdrack_sqlite_catalog_v1` `0000`–`0003` manifest and
  exclusive clean-catalog creation.
- Fail closed on foreign/future/tampered identities and schema corruption while
  preserving frozen app bridge compatibility.
- Bind clean-catalog verification to the compiled normalized schema fingerprint;
  reject unmanifested tables, columns, indexes, and triggers.
- Run the shared Memory/SQLite conformance contract and installed-wheel lifecycle.
