# W5 offline release matrix evidence

The offline harness is `scripts/offline_release_matrix.py`. It builds and audits
all four distributions (`mdrack`, `mdrack-core`, `mdrack-media`, and
`mdrack-sqlite`) as both wheels and source distributions. The audit records
SHA-256 hashes, `Requires-Dist` metadata, artifact counts, and rejects standalone
package wheels that contain the application `mdrack/` package (shadowing).

## Supported distribution/install cells

The release contract has four supported offline install cells. Each cell is
published as a wheel and an sdist; the root application cell is the installed
smoke entry point and the three standalone cells must remain free of the root
`mdrack/` package.

| Cell | Distribution | Offline installation contract |
|---|---|---|
| application | `mdrack` | Installs with the three local distribution dependencies and exposes the CLI/API. |
| reusable core | `mdrack-core` | Installs independently and exposes only provider/storage-neutral core contracts. |
| media records | `mdrack-media` | Installs with `mdrack-core` and exposes provider-free media records/builders. |
| SQLite adapter | `mdrack-sqlite` | Installs with `mdrack-core` and exposes the standalone catalog/search adapter. |

The workflow matrix is a CI execution matrix, not additional support claims:
Linux and Windows × Python 3.11 and 3.12 are declared, while only cells
actually executed on a host are evidence.

The optional installed smoke creates a fresh temporary virtual environment,
installs the root wheel and its three local distribution dependencies with
`uv pip --offline` (resolving only from the artifact directory), clears
`PYTHONPATH`, verifies version/import/source isolation, and runs
`scripts/check_installed_package.py`. The local Linux cell completed
successfully with zero network attempts; the privacy-safe manifest is
`w5-offline-release-matrix.json`. The artifact audit covers all eight
wheel/sdist outputs; the installed smoke is intentionally bounded to the root
application install graph.

The workflow definition is `.github/workflows/offline-release-matrix.yml`. Its
matrix declares Ubuntu and Windows with Python 3.11 and 3.12, runs strict lint,
type, test, dependency, boundary, and compile gates, and keeps offline mode
explicit. The workflow also checks that the release evidence documentation exists and
that the checkout has no whitespace errors. This Linux evidence does not claim that
Windows or Python 3.12 were executed.
