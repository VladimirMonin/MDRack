"""Registered Click routes use the real decoder, engine and SQLite; synthetic vectors only."""

import json
import wave

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.embeddings.synthetic import SyntheticTemporalProvider
from mdrack.public_api import MDRackEngine


def test_native_cli_audio_engine_parity_reopen_read_and_safe_refusals(tmp_path):
    runner = CliRunner()
    root = tmp_path / "collection"
    root.mkdir()
    assert runner.invoke(main, ["--root", str(root), "init"]).exit_code == 0
    source = tmp_path / "owned.wav"
    with wave.open(str(source), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x23\x00" * 32000)
    args = [
        "--root",
        str(root),
        "native",
        "ingest",
        str(source),
        "--kind",
        "audio",
        "--source-namespace",
        "fixture",
        "--source-ref",
        "owned-audio",
        "--provider",
        "synthetic",
        "--start-ms",
        "250",
        "--end-ms",
        "1750",
        "--window-ms",
        "750",
        "--hop-ms",
        "750",
    ]
    out = runner.invoke(main, args)
    assert out.exit_code == 0, out.output
    value = json.loads(out.stdout)["data"]
    rid = value["resource_id"]
    assert value["provider_call_count"] == 2
    second = runner.invoke(main, args)
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["data"]["publication_status"] == "already_published"
    assert json.loads(second.stdout)["data"]["provider_call_count"] == 0
    cap = runner.invoke(main, ["--root", str(root), "native", "capabilities", "--provider", "synthetic"])
    assert cap.exit_code == 0, cap.output
    assert {r["input_modality"] for r in json.loads(cap.stdout)["data"]["routes"]} == {
        "text",
        "image",
        "audio",
        "video",
    }
    searched = runner.invoke(
        main,
        [
            "--root",
            str(root),
            "native",
            "search",
            str(source),
            "--kind",
            "audio",
            "--start-ms",
            "250",
            "--end-ms",
            "1000",
            "--candidates",
            "audio",
            "--provider",
            "synthetic",
        ],
    )
    assert searched.exit_code == 0, searched.output
    result = json.loads(searched.stdout)["data"]
    assert result["effective_mode"] == "semantic"
    assert result["results"][0]["resource_id"] == rid
    queried = runner.invoke(
        main, ["--root", str(root), "native", "query", "--unit-id", value["unit_ids"][0], "--provider", "synthetic"]
    )
    assert queried.exit_code == 0, queried.output
    with MDRackEngine(root=root, config=MDRackConfig(), prepared_embedding_provider=SyntheticTemporalProvider()) as e:
        unit = e.read_unit(value["unit_ids"][0])
        assert unit is not None and unit["text"] is None
        snapshot = e.storage.resource_store.read_snapshot(rid)
        assert len(snapshot.batch.units) == 2
    bad = runner.invoke(
        main,
        [
            "--root",
            str(root),
            "native",
            "ingest",
            str(tmp_path / "PRIVATE_PATH_SENTINEL"),
            "--kind",
            "video",
            "--source-namespace",
            "fixture",
            "--source-ref",
            "clip",
            "--provider",
            "synthetic",
        ],
    )
    assert bad.exit_code == 1 and not json.loads(bad.stdout)["ok"]
    assert "PRIVATE_" not in bad.output
    unsupported = runner.invoke(
        main,
        [
            "--root",
            str(root),
            "native",
            "ingest",
            str(tmp_path / "PRIVATE_UNOPENED_SOURCE"),
            "--kind",
            "audio",
            "--source-namespace",
            "fixture",
            "--source-ref",
            "unsupported",
        ],
    )
    assert unsupported.exit_code == 1
    assert json.loads(unsupported.stdout)["error"]["code"] == "UNSUPPORTED_EMBEDDING_ROUTE"
    assert "PRIVATE_" not in unsupported.output
