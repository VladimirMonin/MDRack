$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot

uv sync --all-extras
uv run pytest
uv run ruff check src/ tests/
uv run python scripts/check_no_forbidden_deps.py
git diff --check
