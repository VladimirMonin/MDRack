# mdrack-sqlite

`mdrack-sqlite` is the single SQLite catalog/search adapter owner for
`mdrack-core`. It depends only on `mdrack-core` and Python's standard library.

The current 1.0 release-candidate API opens an existing MDRack bridge database.
It does not create or migrate a clean standalone schema yet; that work belongs to
the next migration/conformance slice.

```python
from mdrack_sqlite import SQLiteCatalog

with SQLiteCatalog.open("candidate.db") as catalog:
    verification = catalog.verify()
```

Use `open_readonly()` for independent readers. One catalog owns one thread-bound
SQLite connection and serializes its writes. Do not start caller transactions
around `replace_resource()` or `delete_resource()`.
