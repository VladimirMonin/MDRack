# Native evaluator FTS repair and guarded-run readiness — 2026-10-24

## Decision and boundary

**Independently ACCEPTED: bounded MODEL0 software repair and private caller-guard
readiness, required repairs0.** This is not acceptance of a real-data A–D campaign,
semantic/runtime fitness, latency/cost/SLO, operations or package/tag/release.
The closed pilot remains **NONCONFORMANT37/24**, without waiver; its global canonical
fitness is **NOT_COMPUTED**. No new target model/GPU/native-host/embedding-HTTP/app/resource
call occurred in this repair review or its documentation delivery.

The five-file candidate uses baseline `c49a534d70223ef262ebf7027faf5c8aa981b2d6`.
Two production payloads changed; the other three files add synthetic owning regressions.
No tokenizer/schema/migration/ranking/fusion/profile/weight/role/recipe, source caption,
query, grade or policy change is part of the repair.

| Production owner | Accepted SHA-256 |
| --- | --- |
| `mdrack_sqlite/fts.py` | `1521684418458bcdb321d6b23d1ad0aa9137e5afd7bb981e16c0dea3fe6a336e` |
| `mdrack/eval/native_runner.py` | `bb6f0e3c251be3e1f9f3dfa9f3a7c9ea64fc6125e656314a383dd3e1a00be5af` |

## Corrected software behavior

FTS5 keywords are case-sensitive. The shared
[`plain_query_fallback`](../../packages/mdrack-sqlite/src/mdrack_sqlite/fts.py)
no longer mistakes ordinary lower/mixed-case conjunction words for explicit uppercase
operators. For synthetic plain input such as `alpha And beta.`, a raw syntax error may
use the existing quoted-literal retry. Explicit uppercase operators, quotes,
parentheses, stars and column filters still forbid silent reinterpretation. Real
SQLite/Core fresh-v2 and the legacy helper path share the same implementation.

The [`native_runner`](../../src/mdrack/eval/native_runner.py) now handles the declared
public Core `BranchExecutionError` at its per-query search boundary. It emits constant
`search_branch_failure`, retaining BLOCKED cells and the full planned denominator;
query text, branch ID and raw exception are not serialized. Existing missing-policy
precedence remains: null policy gives `BLOCKED_POLICY_MISSING`, while a supplied policy
with incomplete coverage gives `BLOCKED_COVERAGE`. These reports return exit2, not a
success score for a blocked case. Programming errors/cancellation still propagate and
engine contexts close. Handling a failure does not itself fix lexical retrieval or
establish useful D quality. The [CLI contract](../cli-contracts.md#native-corpus-evaluator-search-failures)
describes this separate maintainer script, not a new normal Click command.

## Verification and exact evidence reuse

Windows/CPython3.14.2 and pinned uv0.11.15 were used with owned cache/TMP, offline/no-index,
bytecode off before import and isolated `-I -B -S` software callers. Synthetic providers
and fixtures are labelled software mechanics, never native/model relevance.

- The final exact regression assertions on the two byte-exact old production payloads
  produced24 FAIL/17 PASS; the same final assertions on the repairs produced41 PASS.
  Earlier fixture/setup failures remain recorded separately, not product RED.
- Exact author full Root:2484 PASS/0 FAIL/6 historical SKIP in398.68s; affected274 PASS.
  The reviewer checked these bound raw results and retained them by impact, not as a
  repeated independent full run. All46 original top-level callable/class ASTs across
  the three owning test files are preserved;41 new Root cases were added.
- Independent affected280 PASS =249 Root cases +25 private guard cases +6 additional
  reviewer discriminators. Those include poisoned exception-string privacy, interrupted
  witness/checkpoint commits, pending async cancellation and all-delegate close failure.
- Independent source-free installed41 PASS repeats the new assertions at another
  boundary; it is not321 independent cases. Strict216, Ruff over source/packages/scripts /
  tests, all four dependency boundaries passed independently.
- Four fresh isolated offline PEP517 wheels/noneditable236 payloads were retained from
  the candidate. Every payload matched wheel, actual Root, the fresh installation and
  the reviewer's exact copies. Only the two hashes above differ from the prior payloads.
  Source-free module origins and public factory/runner checks were exercised; this is
  not a new ordinary installed-PE startup or new reviewer build/install claim.

The public runner still lacks provider-close `finally`; the outer caller remains the
owner. Its actual no-argument factory port and canonical runner were exercised in both
Root and source-free installed lanes with private guarded factories and deterministic
synthetic delegates. Each completed12 planned/executed cells, charged14 individual fake
controls/5 synthetic A calls/15 conservative synthetic native units, closed both returned
delegates and retained expected null-policy exit2. No physical control/model operation
was delegated. See the durable [TEST owner](../../instructions/TEST.quality-gates.instructions.md)
and [future-run procedure](../operations.md#guarded-evaluation-after-a-closed-partial-run).

## Closed real-data pilot: partial evidence, not a completed experiment

Eight original/companion × EN/RU assistance × dev/held-out invocations attempted32
scenario slots once each. Every public CLI returned1 before a complete canonical
native-experiment report.24 complete A/B/C reports cover624 cases; D contributes96
successful prefix rankings,8 actual failed searches and104 NOT_RUN cases. Thus832
planned cases contain720 ranked/8 failed/104 NOT_RUN, with no fabricated zero scores.
The successful prefix and available-pair reports do not replace complete D or supply a
full-policy verdict. Available per-query criterion violations remain observations;
large mean nDCG does not waive regression bounds or missing coverage.

The historical calls are296 A HTTP200 attempts and696 successful native processes:
88 text DOCUMENT +520 text QUERY +88 image DOCUMENT,784 conservative native units and
992 completed encoded batches.6272 sampled evaluated CUDA events are bound to the
native process/role/input/profile identities; this is not all-operator/GPU-preprocessing
proof and is not transferred to HTTP A, whose actual kernel device remains UNKNOWN.
Preserved native exits/creation identities/Jobs and app-restoration observations retain
their own closed cutoff, not a fresh resource poll.

35 individual helper commands plus2 local metadata GETs exceeded24 by13; even excluding
four own lifecycle operations leaves33. Earlier grouped21 and stale19 are retained but
are not compliance. The separately bounded single monitor had74 frames/148 helper
commands. No software repair retroactively waives this breach, restarts the clock or
refunds any of the296 A/696 native/784 units/8 invocations/32 slots.

Both complete52-case families and harder original cases remain mandatory; shared50
and union54 are denominator mappings, not executable mixed input or permission to copy
rankings after assistance changes. Four pooled unknowns stay unknown; computational
nonrelevant is not a new factual grade0. There are11 query-source groups/split, not
repeated independent samples. The one strict held-out pair and6/7-member negative scopes do not
establish robust multilingual/abstention fitness. Policy, cutoffs and min8 query-source
groups remain unchanged. Evaluable private inputs remain non-publishable.

## Bounded private guard and remaining execution

The private guard reserves durably before each named individual command/metadata leaf
and through each awaited prepared-provider encode. Hash-chained journal, separate event
witnesses, atomic checkpoint and exclusive lock must agree; interrupted commits and stale
restoration fail closed. Failed/interrupted reservations stay spent. Original pending
work blocks dispatch/close until its retained outcome is known; PID absence is not exit
evidence. Counters/T0/deadline persist across continuation. The monitor has one separately
declared session, frame/interval limits and fixed leaves, never a relabelled regular
control. MODEL0 rejects non-synthetic transport/configured factory construction first.

The software-proved control schedule is8 setup +6 final =14 within24, leaving10 hard-cap
contingency. It is a plan, not a real compliant run. Actual live command/transport/model /
producer/profile/data/runner/ownership bindings, single-monitor scheduling, final-control /
time reserve and a fresh explicit grant remain required before any target operation.
Private glue is not shipped public support or a sandbox against arbitrary bypass or
replacement of every trusted anchor/witness.

Keep old partial reports and exact identity-bound stored DOCUMENT-vector evidence in
that boundary. A SHA is not a query vector; no accepted public resume-D/cache interface
or MODEL0 real-D recovery is introduced. Do not merge old partials into a complete
invocation or relabel old execution as conformant. A separately frozen full eight-run
A–D campaign and independent actual policy/critical-slice assessment remain next; retain
legitimate regressions/POLICY_EXCEEDED without tuning. Optional E and other original
owning-plan obligations retain their declared scope, without new generic gates.

Whole quality/cost/latency/SLO/W6/operations/release stays OPEN. Accepted native roles,
functional A, DATA/OCR/software results and catalog MEASUREMENTS_ONLY remain intact.
The [active execution entry](../../.hermes/plans/2026-10-21-native-media-foundation.md#current-fts-repair-and-guarded-quality-remainder)
is authoritative for the remaining sequence; this scoped repository delivery is not
package/tag/release approval or automatic model authorization.
