param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$specPath = Join-Path $repoRoot 'mdrack.spec'

if (-not (Test-Path -LiteralPath $specPath)) {
    throw "Spec file not found: $specPath"
}

$pyinstallerArgs = @(
    'run',
    '--with', 'pyinstaller>=6.16,<7',
    'pyinstaller',
    '--noconfirm'
)

if ($Clean) {
    $pyinstallerArgs += '--clean'
}

$pyinstallerArgs += $specPath

& uv @pyinstallerArgs

$exePath = Join-Path $repoRoot 'dist\mdrack\mdrack.exe'
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Build finished without EXE: $exePath"
}

"Built EXE: $exePath"
