$ErrorActionPreference = 'Stop'

$development = $false
if ($args.Count -eq 1 -and $args[0] -ceq '--development') {
    $development = $true
} elseif ($args.Count -ne 0) {
    Write-Error 'usage: verify.ps1 [--development]' -ErrorAction Continue
    exit 2
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot
$env:UV_OFFLINE = '1'
$env:UV_NO_PROGRESS = '1'

# ErrorActionPreference alone does not propagate native process exits.
uv run --no-sync --offline python scripts/check_documentation.py --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$releaseArtifacts = Join-Path ([System.IO.Path]::GetTempPath()) ('mdrack-release-artifacts-' + [guid]::NewGuid())
# Reserve only a new owned output; the matrix deletes this directory before build.
New-Item -ItemType Directory -Path $releaseArtifacts | Out-Null
uv sync --all-extras --frozen --offline
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run ruff check src/ tests/
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run ruff check scripts/check_documentation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run ruff check packages/mdrack-core/src/
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run ruff check packages/mdrack-media/src/
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run ruff check packages/mdrack-sqlite/src/
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run mypy packages/mdrack-core/src/mdrack_core
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run mypy packages/mdrack-media/src/mdrack_media
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run mypy packages/mdrack-sqlite/src/mdrack_sqlite
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pytest -m 'not e2e and not privacy'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pytest -m e2e
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pytest -m privacy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python scripts/check_no_forbidden_deps.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python scripts/check_core_boundaries.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python scripts/check_sqlite_boundaries.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python scripts/check_media_boundaries.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python -m compileall -q scripts src packages/mdrack-core/src packages/mdrack-media/src packages/mdrack-sqlite/src
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python scripts/offline_release_matrix.py --output-dir $releaseArtifacts --smoke
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($development) {
    Write-Output 'SKIPPED historical release identity (--development); NOT_RUN release acceptance'
} else {
    uv run python scripts/check_v13_release_packet.py --artifacts-dir $releaseArtifacts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& git diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
