# mdrack-sqlite

`mdrack-sqlite` is the single SQLite catalog/search adapter owner for
`mdrack-core`. It depends only on `mdrack-core` and Python's standard library.

The 1.0 release-candidate API opens existing MDRack bridge databases and creates
independent clean catalogs. `create()` preserves the immutable
`mdrack_sqlite_catalog_v1` `0000`–`0003` compatibility history. `create_v2()`
creates the fresh `mdrack_sqlite_catalog_v2` `0000`–`0004` history directly,
including its exact vector codec/backend registry. Neither path copies, rewrites,
or upgrades app migrations `0000`–`0007`.

```python
from mdrack_sqlite import SQLiteCatalog

with SQLiteCatalog.open("candidate.db") as catalog:
    verification = catalog.verify()

with SQLiteCatalog.create("clean.db") as clean_catalog:
    clean_catalog.verify()

with SQLiteCatalog.create_v2("compact.db") as compact_catalog:
    compact_catalog.verify()
```

Use `open_readonly()` for independent readers. One catalog owns one thread-bound
SQLite connection and serializes its writes. Do not start caller transactions
around `replace_resource()` or `delete_resource()`.
