"""Tests for Unicode-safe CLI JSON output."""

from __future__ import annotations

import pytest

from mdrack.output.json_output import emit_json


def test_emit_json_falls_back_to_ascii_when_terminal_encoding_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_echo(text: str) -> None:
        calls.append(text)
        if len(calls) == 1:
            raise UnicodeEncodeError("cp1251", text, 0, 1, "cannot encode")

    monkeypatch.setattr("mdrack.output.json_output.click.echo", fake_echo)

    emit_json({"emoji": "🕒", "title": "Привет"})

    assert len(calls) == 2
    assert "🕒" in calls[0]
    assert "\\ud83d\\udd52" in calls[1]
    assert "\\u041f\\u0440\\u0438\\u0432\\u0435\\u0442" in calls[1]
