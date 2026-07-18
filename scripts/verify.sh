#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
uv sync --all-extras
uv run pytest
uv run ruff check src/ tests/
uv run ruff check packages/mdrack-core/src/
uv run ruff check packages/mdrack-sqlite/src/
uv run mypy packages/mdrack-core/src/mdrack_core
uv run mypy packages/mdrack-sqlite/src/mdrack_sqlite
uv run python scripts/check_no_forbidden_deps.py
uv run python scripts/check_core_boundaries.py
uv run python scripts/check_sqlite_boundaries.py
git diff --check
