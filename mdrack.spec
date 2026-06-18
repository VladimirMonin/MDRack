# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)
source_root = project_root / "src"
migrations_root = source_root / "mdrack" / "storage" / "sqlite" / "migrations"

datas = [
    (str(path), "mdrack/storage/sqlite/migrations")
    for path in sorted(migrations_root.glob("*.sql"))
]


a = Analysis(
    ["src/mdrack/__main__.py"],
    pathex=[str(source_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mdrack",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="mdrack",
)
