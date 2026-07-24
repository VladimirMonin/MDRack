"""Independent v2 clean-schema identity for fresh compact catalogs."""

SQLITE_CATALOG_V2_SCHEMA_ID = "mdrack_sqlite_catalog_v2"
SQLITE_CATALOG_V2_SCHEMA_VERSION = "0004"
SQLITE_V2_MIGRATION_MANIFEST: tuple[tuple[str, str], ...] = (
    ("0000_identity.sql", "079a67a9e90d423e6e020fe37cbb0829818a6dbbe5d9a5b1817a249c46a6e3c1"),
    ("0001_catalog.sql", "5df6b532ffa5247dd08dc0200830d0371b97abd447dc0b81cd5936ca634ac60a"),
    ("0002_vectors_facets.sql", "09e8d9a60722ee4c87515e2e1173305db6f6406add7c9ed2e42a685f36ace7b6"),
    ("0003_search.sql", "be76b2774d51c344f01b4a497ba91ebd8b4de02c01838cbecd316852708fb00b"),
    ("0004_vector_encoding.sql", "e6ef422ce7aa80b2b7d4e1d8b181e70788be904011e65ef2e3798a46d63405bc"),
)
SQLITE_V2_MIGRATION_MANIFEST_DIGEST = "353facc85a46cfc30fea8511993e06aa49910b09d60fe7750cb1855df120dd98"
