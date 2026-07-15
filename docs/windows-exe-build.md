# Windows EXE Build

MDRack can be packaged as a Windows console EXE with PyInstaller.

## Output

The supported build target is an onedir bundle:

```text
dist\mdrack\mdrack.exe
```

The bundle includes the SQL migration files required by `init`, `scan`, `rebuild`, and `doctor`.

## Build

From the repository root:

```powershell
./scripts/build_windows_exe.ps1 -Clean
```

The script runs PyInstaller through `uv` and uses the checked-in `mdrack.spec` file.
The package spec includes every checked-in `*.sql` migration, including the
asset registry schema.

If you prefer the raw command:

```powershell
uv run --with "pyinstaller>=6.16,<7" pyinstaller --noconfirm --clean mdrack.spec
```

## Smoke Test The Build

Run the built EXE from the repository root or pass an explicit writable `--root` directory.

Recommended verification flow:

```powershell
dist\mdrack\mdrack.exe --help
dist\mdrack\mdrack.exe --root . init
dist\mdrack\mdrack.exe --root . scan --provider fake
dist\mdrack\mdrack.exe --root . status
dist\mdrack\mdrack.exe --root . search "MDRack" --mode hybrid --provider fake --limit 3
dist\mdrack\mdrack.exe --root . doctor
```

## Notes

- The EXE is a CLI application, not a GUI app.
- The selected `--root` must be writable because MDRack creates `.mdrack\` there.
- `scan --provider fake` is the fastest smoke test because it does not require LM Studio.
- If you want to test real embeddings, switch the command to `--provider lmstudio` and make sure LM Studio is running first.
- On non-Windows hosts only the PowerShell/spec contract and Python wheel can be
  validated; that is not evidence that `mdrack.exe` executed successfully.
- Run `scripts/verify.ps1` before packaging. It intentionally excludes the LIVE
  LM Studio evaluation entrypoint.
