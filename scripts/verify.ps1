$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot

uv sync --all-extras
uv run pytest
uv run ruff check src/ tests/
uv run ruff check packages/mdrack-core/src/
uv run mypy packages/mdrack-core/src/mdrack_core
uv run python scripts/check_no_forbidden_deps.py
uv run python scripts/check_core_boundaries.py
git diff --check
