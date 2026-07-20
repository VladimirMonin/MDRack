#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
export UV_OFFLINE=1
export UV_NO_PROGRESS=1

uv sync --all-extras --frozen --offline
uv run ruff check src/ tests/
uv run ruff check packages/mdrack-core/src/
uv run ruff check packages/mdrack-media/src/
uv run ruff check packages/mdrack-sqlite/src/
uv run mypy packages/mdrack-core/src/mdrack_core
uv run mypy packages/mdrack-media/src/mdrack_media
uv run mypy packages/mdrack-sqlite/src/mdrack_sqlite
uv run pytest -m 'not e2e and not privacy'
uv run pytest -m e2e
uv run pytest -m privacy
uv run python scripts/check_no_forbidden_deps.py
uv run python scripts/check_core_boundaries.py
uv run python scripts/check_sqlite_boundaries.py
uv run python scripts/check_media_boundaries.py
uv run python -m compileall -q scripts src packages/mdrack-core/src packages/mdrack-media/src packages/mdrack-sqlite/src
uv run python scripts/offline_release_matrix.py \
  --output-dir "${TMPDIR:-/tmp}/mdrack-release-artifacts" \
  --candidate-packet docs/evidence/v0.4-release-packet.json \
  --smoke \
  --expected-manifest docs/evidence/w5-offline-release-matrix.json
test -s docs/evidence/w5-offline-release-matrix.md
test -s docs/evidence/w5-offline-release-matrix.json
uv run python scripts/check_release_docs.py
git diff --check
