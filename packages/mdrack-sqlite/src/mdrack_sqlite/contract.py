"""Public contract identity for the standalone SQLite adapter."""

SQLITE_CATALOG_API_VERSION = "1.0.0rc1"
SQLITE_BRIDGE_SCHEMA_ID = "mdrack-app-core-bridge-v1"
SQLITE_CATALOG_SCHEMA_ID = "mdrack_sqlite_catalog_v1"
SQLITE_CATALOG_SCHEMA_VERSION = "0003"
SQLITE_MIGRATION_MANIFEST: tuple[tuple[str, str], ...] = (
    ("0000_identity.sql", "079a67a9e90d423e6e020fe37cbb0829818a6dbbe5d9a5b1817a249c46a6e3c1"),
    ("0001_catalog.sql", "5df6b532ffa5247dd08dc0200830d0371b97abd447dc0b81cd5936ca634ac60a"),
    ("0002_vectors_facets.sql", "09e8d9a60722ee4c87515e2e1173305db6f6406add7c9ed2e42a685f36ace7b6"),
    ("0003_search.sql", "be76b2774d51c344f01b4a497ba91ebd8b4de02c01838cbecd316852708fb00b"),
)
SQLITE_MIGRATION_MANIFEST_DIGEST = (
    "bc0e36bd47ed2fab4a1a0614c431a35ac0e70d23f1f27a222ea4bd72bb997b4c"
)
