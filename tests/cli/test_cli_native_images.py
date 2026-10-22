"""Registered native routes, child reopen, safe refusals and next operation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from tests.integration.test_native_image_v2 import PNG


def test_registered_child_native_ingest_reopen_read_search(tmp_path: Path) -> None:
    source = tmp_path / 'image.png'
    source.write_bytes(PNG)
    def child(*args):
        command = [sys.executable, '-B', '-m', 'mdrack', '--root', str(tmp_path), *args]
        completed = subprocess.run(command, text=True, capture_output=True, env=os.environ.copy(), timeout=30)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)['data']
    data = child('image', 'native-ingest', str(source), '--resource-id', 'native', '--source-namespace', 'host',
                 '--source-ref', 'image.png', '--provider', 'synthetic')
    read = child('read', 'unit', data['unit_ids'][0])
    assert read['unit']['text'] is None
    found = child('image', 'native-search', '--image-file', str(source), '--provider', 'synthetic')
    assert found['results'][0]['resource_id'] == 'native'
    assert found['effective_mode'] == 'semantic' and not found['degraded']
    text = child('image', 'native-search', '--text', 'a scene', '--provider', 'synthetic', '--mode', 'semantic')
    assert text['results'][0]['resource_id'] == 'native'
    assert source.read_bytes() == PNG


def test_unsupported_transport_before_source_and_database(tmp_path: Path, monkeypatch) -> None:
    from mdrack.application.source_resolution import SourceResolver
    def forbidden(*args, **kwargs):
        raise AssertionError('PRIVATE_FILESYSTEM_SENTINEL')
    monkeypatch.setattr(SourceResolver, 'image', forbidden)
    runner = CliRunner()
    for arguments in (
        ['image', 'native-ingest', str(tmp_path / 'PRIVATE_PATH_SENTINEL'), '--resource-id', 'x',
         '--source-namespace', 'host', '--source-ref', 'x.png'],
        ['image', 'native-search', '--image-file', str(tmp_path / 'PRIVATE_PATH_SENTINEL')],
    ):
        result = runner.invoke(main, ['--root', str(tmp_path), *arguments])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload['error']['code'] == 'UNSUPPORTED_EMBEDDING_ROUTE'
        assert 'PRIVATE_' not in result.output
    assert not (tmp_path / '.mdrack').exists()


def test_native_cleanup_failure_does_not_hide_commit(tmp_path: Path, monkeypatch) -> None:
    from mdrack.cli.commands import images
    from tests.integration.test_native_image_v2 import make_engine
    class BrokenClose:
        async def close(self):
            raise OSError('PRIVATE_CLOSE_SENTINEL')
    monkeypatch.setattr(images, '_native_engine', lambda ctx, name: (make_engine(tmp_path), BrokenClose()))
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    result = CliRunner().invoke(main, ['--root', str(tmp_path), 'image', 'native-ingest', str(source),
                                      '--resource-id', 'x', '--source-namespace', 'host', '--source-ref', 'x.png',
                                      '--provider', 'synthetic'])
    assert result.exit_code == 0
    data = json.loads(result.output)['data']
    with make_engine(tmp_path) as reopened:
        assert reopened.read_unit(data['unit_ids'][0])['text'] is None
    assert 'PRIVATE_' not in result.output
