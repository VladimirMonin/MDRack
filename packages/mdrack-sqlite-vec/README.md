# mdrack-sqlite-vec

`mdrack-sqlite-vec` is an experimental package containing only the explicit
compatibility probe for the pinned `sqlite-vec==0.1.9` distribution. It does not
implement or auto-discover a production vector backend, duplicate the MDRack
catalog, or change the extensionless builtin path.

Run the probe as an installed package:

```bash
python -m mdrack_sqlite_vec
```

The command writes one machine-readable JSON report and exits non-zero when a
promotion-blocking capability is unproven. A `fail` decision preserves
`builtin-exact-v1`; it is not a fallback permission for a partially written
extension-backed catalog.
