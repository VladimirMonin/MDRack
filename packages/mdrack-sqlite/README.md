# mdrack-sqlite

`mdrack-sqlite` is the single SQLite catalog/search adapter owner for
`mdrack-core`. It depends only on `mdrack-core` and Python's standard library.

The 1.0 release-candidate API opens existing MDRack bridge databases and creates
independent clean catalogs with the immutable `mdrack_sqlite_catalog_v1`
`0000`–`0003` migration history. The clean history reuses the frozen `core_*`
semantics without copying or rewriting app migrations `0000`–`0007`.

```python
from mdrack_sqlite import SQLiteCatalog

with SQLiteCatalog.open("candidate.db") as catalog:
    verification = catalog.verify()

with SQLiteCatalog.create("clean.db") as clean_catalog:
    clean_catalog.verify()
```

Use `open_readonly()` for independent readers. One catalog owns one thread-bound
SQLite connection and serializes its writes. Do not start caller transactions
around `replace_resource()` or `delete_resource()`.
