# Changelog

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
