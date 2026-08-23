from pathlib import Path

from mdrack.ingestion.raw_text import RawTextIngestionService


class Catalog:
    def __init__(self) -> None:
        self.batch = None

    def replace_resource(self, batch: object) -> None:
        self.batch = batch

    def delete_resource(self, resource_id: str) -> None:
        del resource_id


def test_replacing_same_source_ref_reuses_resource_identity(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("first", encoding="utf-8")
    catalog = Catalog()
    first = RawTextIngestionService(catalog).ingest(source, source_ref="docs/source.txt", media_type="text/plain", root=root)  # noqa: E501
    source.write_text("second", encoding="utf-8")
    second = RawTextIngestionService(catalog).ingest(source, source_ref="docs/source.txt", media_type="text/plain", root=root)  # noqa: E501
    assert first.resource_id == second.resource_id
    assert catalog.batch is not None
