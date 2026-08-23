from pathlib import Path

import pytest

from mdrack.ingestion.raw_source_provenance import RawSourceError, RawSourceErrorCode
from mdrack.ingestion.raw_text import RawTextIngestionService


class Catalog:
    def __init__(self) -> None:
        self.batch = None

    def replace_resource(self, batch: object) -> None:
        self.batch = batch

    def delete_resource(self, resource_id: str) -> None:
        del resource_id


def test_plain_text_prepares_deterministic_raw_graph(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("needle text", encoding="utf-8")
    first = Catalog()
    second = Catalog()
    result_a = RawTextIngestionService(first).ingest(source, source_ref="docs/source.txt", media_type="text/plain", root=root)  # noqa: E501
    result_b = RawTextIngestionService(second).ingest(source, source_ref="docs/source.txt", media_type="text/plain", root=root)  # noqa: E501
    assert result_a == result_b
    assert first.batch == second.batch
    assert first.batch.resource.resource_kind == "raw_text"
    assert first.batch.units[0].evidence_locator.kind == "raw_local_source_span"


def test_source_inside_root_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("needle", encoding="utf-8")
    with pytest.raises(RawSourceError) as error:
        RawTextIngestionService(Catalog()).ingest(source, source_ref="source.md", media_type="text/markdown", root=tmp_path)  # noqa: E501
    assert error.value.code is RawSourceErrorCode.SOURCE_REF_INVALID


def test_invalid_utf8_is_payload_free(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source.txt"
    source.write_bytes(b"\xff")
    with pytest.raises(RawSourceError) as error:
        RawTextIngestionService(Catalog()).ingest(source, source_ref="docs/source.txt", media_type="text/plain", root=root)  # noqa: E501
    assert error.value.code is RawSourceErrorCode.SIGNATURE_UNSUPPORTED
