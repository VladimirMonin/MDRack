"""Regression coverage for the withdrawn legacy sections CLI surface."""

from __future__ import annotations

from click.testing import CliRunner

from mdrack.cli import main


def test_sections_group_is_not_registered_in_the_fixed_catalog_cli() -> None:
    result = CliRunner().invoke(main, ["sections", "--help"])

    assert result.exit_code == 2
    assert "No such command 'sections'" in result.output
    assert "sections" not in main.commands
