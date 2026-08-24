from pathlib import Path

from mdrack.ingestion.raw_video import RawVideoIngestionService


class Catalog:
    def replace_resource(self, batch: object) -> None:
        self.batch = batch

    def delete_resource(self, resource_id: str) -> None:
        del resource_id


def test_raw_video_uses_strict_extractor_and_composer(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"0000ftypisompayload")
    extractor = tmp_path / "extractor.py"
    extractor.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdin.buffer.read()\n"
        "print(json.dumps({\n"
        "'schema':'mdrack.raw-video-extraction.v1', 'media_type':'video/mp4', 'duration_ms':1000,\n"
        "'transcript':{'schema':'mdrack.timed-transcript.v1','duration_ms':1000,"
        "'atoms':[{'start_ms':0,'end_ms':500,'text':'needle'}]},\n"
        "'frames':[{'timestamp_ms':250,'caption':'slide'}]}))\n",
        encoding="utf-8",
    )
    extractor.chmod(0o755)
    catalog = Catalog()
    result = RawVideoIngestionService(catalog).ingest(
        source, source_ref="videos/clip.mp4", root=root,
        video_extractor_command=str(extractor), allow_external_video_extractor=True,
    )
    assert result.resource_kind == "video"
    assert result.media_type == "video/mp4"
    assert result.transcript_unit_count == 1
    assert result.frame_unit_count == 1
    assert catalog.batch.resource.content_hash.startswith("sha256:")
    assert catalog.batch.resource.locator.payload == {"source_ref": "videos/clip.mp4"}
    assert catalog.batch.resource.metadata["provenance"]["selected_frame_count"] == 1


def test_raw_video_rejects_non_video_extractor_media_type(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"0000ftypisompayload")
    extractor = tmp_path / "extractor.py"
    extractor.write_text("import sys; sys.stdin.buffer.read(); print('{}')\n", encoding="utf-8")
    extractor.chmod(0o755)
    catalog = Catalog()
    try:
        RawVideoIngestionService(catalog).ingest(
            source, source_ref="videos/clip.mp4", root=root,
            video_extractor_command=str(extractor), allow_external_video_extractor=True,
        )
    except Exception:
        pass
    else:
        raise AssertionError("invalid extraction must fail")
    assert not hasattr(catalog, "batch")
