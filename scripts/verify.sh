#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
uv sync --all-extras
uv run pytest
uv run ruff check src/ tests/
uv run python scripts/check_no_forbidden_deps.py
git diff --check
