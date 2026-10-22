#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
export UV_OFFLINE=1
export UV_NO_PROGRESS=1

development=0
if [[ $# -eq 1 && "$1" == "--development" ]]; then
  development=1
elif [[ $# -ne 0 ]]; then
  echo 'usage: verify.sh [--development]' >&2
  exit 2
fi

uv run --no-sync --offline python scripts/check_documentation.py --check
# mktemp reserves an owned unique directory; matrix may delete only this output.
release_artifacts="$(mktemp -d "${TMPDIR:-/tmp}/mdrack-release-artifacts.XXXXXX")"

uv sync --all-extras --frozen --offline
uv run ruff check src/ tests/
uv run ruff check scripts/check_documentation.py
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
  --output-dir "$release_artifacts" \
  --smoke
if [[ "$development" -eq 1 ]]; then
  echo 'SKIPPED historical release identity (--development); NOT_RUN release acceptance'
else
  uv run python scripts/check_v13_release_packet.py \
    --artifacts-dir "$release_artifacts"
fi
git diff --check
