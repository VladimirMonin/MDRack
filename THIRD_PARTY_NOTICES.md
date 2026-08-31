# Third-party runtime notices

> Русское резюме: это проверяемый реестр сторонних библиотек обязательного runtime-графа
> MDRack. Он не является юридической консультацией, гарантией отсутствия рисков или
> разрешением на выпуск. Лицензии сторонних библиотек не заменяются лицензией MIT MDRack.

## Scope and verification method

This ledger covers the non-development dependency closure of `mdrack` from the committed
root `uv.lock` for supported Python 3.11+ Linux and Windows installations. The closure
comes from `uv tree --locked --no-dev`: the four MDRack distributions are local project
packages; all rows below are separately resolver-installed dependencies. `colorama` is
included because Click declares it only when `sys_platform == 'win32'`.

For every row, the exact-version PyPI source distribution was inspected and the listed
license file was identified by path. The bracket after the path is the versioned PyPI
release locator; the bracket after the license identifier is the standard license text
used to interpret the recorded obligation. Metadata labels alone were not treated as
sufficient evidence.

`resolver-only` means the dependency is not bundled inside the four base MDRack
wheel/sdist artifacts. In a normal resolver installation its upstream package carries
its own license. If a future binary or bundle embeds any row, the distributor must ship
the applicable upstream license/notice material and comply with the row's redistribution
duties. This ledger is a versioned inventory, not a substitute for an exact bundle
manifest.

**Commercial use / payment** records only the permission and royalty/fee language of
the identified software license. It is not tax, export, trademark, patent, contract, or
legal-clearance advice.

## Locked mandatory runtime graph

| Package and platform | Exact license source | Commercial use / payment | Redistribution duties if bundled or redistributed | Delivery status |
|---|---|---|---|---|
| `annotated-types` 0.7.0 — Linux + Windows | `annotated_types-0.7.0/LICENSE` [6]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `anyio` 4.14.2 — Linux + Windows | `anyio-4.14.2/LICENSE` [7]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `certifi` 2026.6.17 — Linux + Windows | `certifi-2026.6.17/LICENSE` [8]; MPL-2.0 [4] | Allowed; MPL-2.0 grants a royalty-free license. | Preserve MPL notices. If modified MPL-covered source files are distributed, make their corresponding Source Code Form available under MPL-2.0; do not present MPL-covered files as relicensed by MDRack. | `resolver-only` |
| `click` 8.4.2 — Linux + Windows | `click-8.4.2/LICENSE.txt` [9]; BSD-3-Clause [3] | Allowed; no license royalty or payment term was found. | Retain copyright, conditions, and disclaimer in source/binary redistribution; do not use contributor names to endorse derived products without permission. | `resolver-only` |
| `colorama` 0.4.6 — **Windows only** through Click marker | `colorama-0.4.6/LICENSE.txt` [10]; BSD-3-Clause [3] | Allowed; no license royalty or payment term was found. | Retain copyright, conditions, and disclaimer in source/binary redistribution; do not use contributor names to endorse derived products without permission. | `resolver-only` (Windows branch) |
| `h11` 0.16.0 — Linux + Windows | `h11-0.16.0/LICENSE.txt` [11]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `httpcore` 1.0.9 — Linux + Windows | `httpcore-1.0.9/LICENSE.md` [12]; BSD-3-Clause [3] | Allowed; no license royalty or payment term was found. | Retain copyright, conditions, and disclaimer in source/binary redistribution; do not use contributor names to endorse derived products without permission. | `resolver-only` |
| `httpx` 0.28.1 — Linux + Windows | `httpx-0.28.1/LICENSE.md` [13]; BSD-3-Clause [3] | Allowed; no license royalty or payment term was found. | Retain copyright, conditions, and disclaimer in source/binary redistribution; do not use contributor names to endorse derived products without permission. | `resolver-only` |
| `idna` 3.18 — Linux + Windows | `idna-3.18/LICENSE.md` [14]; BSD-3-Clause [3] | Allowed; no license royalty or payment term was found. | Retain copyright, conditions, and disclaimer in source/binary redistribution; do not use contributor names to endorse derived products without permission. | `resolver-only` |
| `markdown-it-py` 4.2.0 — Linux + Windows | `markdown_it_py-4.2.0/LICENSE` [15]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `mdurl` 0.1.2 — Linux + Windows | `mdurl-0.1.2/LICENSE` [16]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `pydantic` 2.13.4 — Linux + Windows | `pydantic-2.13.4/LICENSE` [17]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `pydantic-core` 2.46.4 — Linux + Windows | `pydantic_core-2.46.4/LICENSE` [18]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `pygments` 2.20.0 — Linux + Windows | `pygments-2.20.0/LICENSE` [19]; BSD-2-Clause [2] | Allowed; no license royalty or payment term was found. | Retain copyright, conditions, and disclaimer in source/binary redistribution. | `resolver-only` |
| `pyyaml` 6.0.3 — Linux + Windows | `pyyaml-6.0.3/LICENSE` [20]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `rich` 15.0.0 — Linux + Windows | `rich-15.0.0/LICENSE` [21]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `toml` 0.10.2 — Linux + Windows | `toml-0.10.2/LICENSE` [22]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |
| `typing-extensions` 4.16.0 — Linux + Windows | `typing_extensions-4.16.0/LICENSE` [23]; PSF-2.0 [5] | Allowed; the PSF agreement grants a royalty-free worldwide license. | Retain the PSF agreement and copyright notice. A distributed derivative based on the covered software must include a brief summary of changes; do not use PSF trademarks for endorsement. | `resolver-only` |
| `typing-inspection` 0.4.2 — Linux + Windows | `typing_inspection-0.4.2/LICENSE` [24]; MIT [1] | Allowed; the MIT grant is free of charge and has no license royalty. | Keep the copyright and permission notice with copies or substantial portions; no source-publication duty in MIT itself. | `resolver-only` |

The exact locked base graph above has no row classified as GPL, AGPL, non-commercial,
or paid-runtime by the verified license sources. That conclusion is limited to these
19 resolver dependencies and these exact versions; it does not cover future lockfile
changes, package-index substitutions, or a self-contained bundle.

## Certifi/MPL-2.0 in plain language

`certifi` is not MIT. Its inspected license file says that its CA bundle source form is
subject to MPL-2.0. The normal MDRack Python artifact does not embed `certifi`; a resolver
installs it separately. If a later product bundles or modifies MPL-covered files, preserve
the MPL notices and provide the corresponding modified covered source files under MPL-2.0.
MPL-2.0 is not a blanket rule that every surrounding proprietary file must be opened, but
this ledger is not a substitute for checking the exact bundle and legal situation.[4][8]

A Windows PyInstaller onedir EXE is also outside this ledger's accepted Python-artifact
scope. Before it is called release-ready, build it on Windows and retain an exact manifest
of every embedded file plus the applicable license/notice texts. Do not infer that result
from resolver-only wheel/sdist evidence.

## Sources

[1] https://spdx.org/licenses/MIT.html
[2] https://spdx.org/licenses/BSD-2-Clause.html
[3] https://spdx.org/licenses/BSD-3-Clause.html
[4] https://spdx.org/licenses/MPL-2.0.html
[5] https://spdx.org/licenses/PSF-2.0.html
[6] https://pypi.org/project/annotated-types/0.7.0
[7] https://pypi.org/project/anyio/4.14.2
[8] https://pypi.org/project/certifi/2026.6.17
[9] https://pypi.org/project/click/8.4.2
[10] https://pypi.org/project/colorama/0.4.6
[11] https://pypi.org/project/h11/0.16.0
[12] https://pypi.org/project/httpcore/1.0.9
[13] https://pypi.org/project/httpx/0.28.1
[14] https://pypi.org/project/idna/3.18
[15] https://pypi.org/project/markdown-it-py/4.2.0
[16] https://pypi.org/project/mdurl/0.1.2
[17] https://pypi.org/project/pydantic/2.13.4
[18] https://pypi.org/project/pydantic-core/2.46.4
[19] https://pypi.org/project/pygments/2.20.0
[20] https://pypi.org/project/PyYAML/6.0.3
[21] https://pypi.org/project/rich/15.0.0
[22] https://pypi.org/project/toml/0.10.2
[23] https://pypi.org/project/typing-extensions/4.16.0
[24] https://pypi.org/project/typing-inspection/0.4.2
