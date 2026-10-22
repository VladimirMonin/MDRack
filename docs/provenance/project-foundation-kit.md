# Selective Project Foundation Kit adaptation

Upstream: [Project Foundation Kit](https://github.com/VladimirMonin/Project-Foundation-Kit),
version **0.2.0**, revision `add0238e31b85588ef9c9c4af8e973f02170b40e`.
This is selective adaptation, not installation of the complete pack, hooks, client
profiles or six skills. Project-native instructions and manual decisions remain.

## Actual source → recipient map

All upstream paths below refer to that exact revision.

| PFK source | MDRack recipient and adaptation |
| --- | --- |
| `pack/references/metadata.md` | `instructions/DOCS.architecture.instructions.md`: passport only for four selected imported docs; native grammar retained |
| `pack/references/lifecycle.md`, `pack/skills/pf-maintain/SKILL.md`, `pack/templates/instructions/documentation.md.tmpl` | DOCS architecture and WORKFLOW build: existing owner first, same-change knowledge update/no-impact, history/proposal/evidence separation and finite completion |
| `pack/templates/instructions/instruction-authoring.md.tmpl` | DOCS instruction style and AGENTS routing: one responsibility, real consumers, sync does not authorize policy changes |
| `pack/references/quality.md`, `pack/templates/instructions/testing.md.tmpl` | TEST quality gates: pre-import isolation, partial-acquire cleanup, observable/reopened result and raw exit/skip limits |
| `pack/templates/instructions/logging.md.tmpl`, `pack/templates/foundation/observability-policy.md.tmpl`, `pack/references/quality.md` | OBS logging: positive useful facts plus negative sentinels in actual serialized bytes, unknown vs zero, bounded sinks separate from product state |
| `pack/tools/check_foundation.py` | `scripts/check_documentation.py`: substantial YAML/path/frontmatter/fence/code-span/link/anchor helpers adapted; project-only scope, native instruction/routes and one fixed navigation block added; no pack/templates/translation/Bases subsystem |
| `tests/test_check_foundation.py`, `pack/tools/README.md` | `tests/unit/test_documentation.py` and development guide: synthetic subprocess scenarios and bounded Markdown contract adapted; scratch external to project, updater/no-op/no-write cases added |

The imported native-media design texts are the project's separately supplied technical
sources, not PFK payload. Their baseline/verification metadata retain their original
historical meaning. Their new editorial date is not runtime evidence.

PFK itself substantially adapts selected ECC lifecycle/document-role/regression
procedures. Applicable donor: [affaan-m/ECC revision
30b68924ae9729159f44326aa3134b1d9d67c0e2](https://github.com/affaan-m/ECC/tree/30b68924ae9729159f44326aa3134b1d9d67c0e2),
particularly `skills/living-docs-governance/SKILL.md`, `skills/tdd-workflow/SKILL.md`,
`skills/module-regression/SKILL.md` and `skills/test-collaboration/SKILL.md` through
PFK lifecycle/quality/maintenance. No ECC runtime, hooks or full skills are bundled.
UIZZE/UI procedures, services/assets and client integrations are **not used**.

## Distribution boundary

Keep this notice with source or sdists containing the adapted checker/docs/instructions.
The application wheel contains runtime Python, not this tooling/docs payload; this
notice does not add PFK or ECC to the 19 resolver-only runtime rows in
[the runtime ledger](../../THIRD_PARTY_NOTICES.md). MDRack's own LICENSE is unchanged.
The checker is structural, not automatic architecture authorship or review acceptance.
Upstream updates never overwrite project adaptations automatically.

## PFK — full MIT notice

```text
MIT License

Copyright (c) 2026 Project Foundation Kit contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## ECC — full applicable MIT notice

```text
MIT License

Copyright (c) 2026 Affaan Mustafa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
