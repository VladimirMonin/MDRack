import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_ingest_raw_video_emits_private_success(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "private.mp4"
    source.write_bytes(b"0000ftypisompayload")
    extractor = tmp_path / "extractor.py"
    extractor.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdin.buffer.read()\n"
        "print(json.dumps({'schema':'mdrack.raw-video-extraction.v1','media_type':'video/mp4',"
        "'duration_ms':1000,'transcript':{'schema':'mdrack.timed-transcript.v1',"
        "'duration_ms':1000,'atoms':[{'start_ms':0,'end_ms':500,'text':'PRIVATE_CAPTION'}]},"
        "'frames':[{'timestamp_ms':250,'caption':'PRIVATE_FRAME'}]}))\n",
        encoding="utf-8",
    )
    extractor.chmod(0o755)
    result = CliRunner().invoke(main, [
        "--root", str(root), "ingest", "raw-video", str(source),
        "--source-ref", "videos/private.mp4", "--allow-external-video-extractor",
        "--video-extractor-command", str(extractor),
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload["data"]) == {
        "resource_id",
        "resource_kind",
        "media_type",
        "representation_count",
        "transcript_unit_count",
        "frame_unit_count",
        "vector_count",
        "persisted",
    }
    assert payload["data"]["resource_kind"] == "video"
    assert payload["data"]["frame_unit_count"] == 1
    assert str(source) not in result.stdout
    assert "PRIVATE_CAPTION" not in result.stdout
    assert "PRIVATE_FRAME" not in result.stdout


def test_ingest_raw_video_requires_authorization(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"0000ftypisompayload")
    result = CliRunner().invoke(main, [
        "--root", str(root), "ingest", "raw-video", str(source),
        "--source-ref", "videos/clip.mp4",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "RAW_VIDEO_INGEST_ERROR"
