# Independently reviewed source-caption pilot, 2026-10-24

## Separate decisions

On baseline `754cb37cf2df00c2bbfb590ed476ae97d1b80cdf`, an immutable proposed
real-image packet received independent data review, without model operations:

- **ACCEPTED:** a versioned private frozen source-caption pilot, with recorded
  grade disagreements and four explicitly pooled unknown pairs.
- **ACCEPTED WITH LIMITATIONS:** its pre-score experimental policy, not a user SLA,
  negative-intent fitness criterion or proof of useful enrichment gain.
- **OPEN:** mandatory missing slices, required native B-role/matrix disposition,
  actual model relevance/cost/latency fitness, W6, operational and release acceptance.

The accepted [normal native product](native-image-product-2026-10-23.md) and
[functional short-text A](text-provider-functional-2026-10-24.md) remain accepted
within their own scopes. Neither was replayed or reclassified as a missing model.
The decisions above retain this original DATA-review cutoff. The later
[paired-query companion](paired-query-companion-review-2026-10-24.md) accepts a separate
whole family with one concrete strict held-out pair; the separately accepted
[native text/image v2 result](native-text-image-product-2026-10-24.md) closes the named
B text-DOCUMENT functional blocker. Neither changes this family's original cases/gold,
policy, rights limit or absence of target-model scoring.

## Physical inputs, rights and independence

All 256 proposed evidence members, 85,132,916 bytes, were verified by size/hash and
preserved unchanged. The selected pilot has 22 image parents and 22 creator-derived
source groups, 11 dev and 11 held-out. Four historical parent identities and their
2+2 split, 23 legacy artifacts and the spent historical acquisition ledger remain
unchanged. Distinct supplied creator identities do not prove absence of aliases,
unseen duplicate relationships or model pretraining contamination.

The submitted DOCUMENT inputs are 22 distinct RGB PNG224×224 views. All were actually
visually inspected, with selected retained parents consulted where needed. Independent
CPU decode/recipe replay and PNG CRC/IEND/filter checks established exact submitted RGB,
white-padding and unscaled contact-tile equality. This is caller-prepared input proof,
not production full-resolution preprocessing or a target-model/GPU run. Larger-source
information was not used to invent detail missing at the submitted size.

Four parents are retained dataset-archive images; 12 new publisher original-URL bodies
match declared OriginalMD5; six are explicit publisher thumbnail variants. None of these
counts implies universal camera-original or unchanged-full-resolution equivalence.
Per-image media CC-BY-2.0 and annotation CC-BY-4.0 authority, exact source rows, credits
and derivative changes were checked and retained privately. Local technical review
supports private evaluation only: `publishable=false`, not legal/no-PII certification.
Pixels, source text, URLs, creator identities and execution glue are not published.

## Shared gold, unknowns and language axes

There are 44 unchanged original-human query texts and 52 scope-cases: 44 positive and
eight negative. Both fixed-assistance language variants share the same queries, gold,
scopes, groups and submitted media. Selection, alignment and grades are independent
agent judgments, not new human labels or machine translations presented as human text.
Separate annotation ordinals and global normalized query/projection copy absence were
verified for both assistance languages.

The 538 scoped case-pairs contain 534 judged rows and four unknowns. These reduce to
480 known unique text/target pairs plus four unknown pairs; 54 negative-subscope rows
reuse existing decisions. They are not 534 independent labels, 104 independent queries
or twice as many source groups. Three proposed grades were downgraded with retained
rationales: one 2→1 and two 3→2. Final scoped histogram: grade0=420, grade1=53,
grade2=39, grade3=22. Genuine broad-query alternate positives were retained.

There are 48 complete cases and four pooled cases. Unknowns remain omitted judgments,
not grade0. The current evaluator's pooled `unjudged_policy=nonrelevant` is a computational
convention with judged-pool recall and coverage; it does not establish irrelevant truth.
Future results must retain unjudged-return counts and this limit.

Negative gold applies only to two six-member and six seven-member declared subcatalogs,
not all 22 images. The intents have positives outside those scopes. No empty-result or
abstention mechanism is implied. Full literal EN/RU query intent aligns strictly in only
one dev source group: 18 pairs share a core but differ in constraints, and three differ
materially. There is no strict held-out translation slice. Paired *assistance* variants
must not be mislabeled matched query-language intent. A minimal retained-human-caption
selection correction is separately proposed, not silently applied or called broad
bilingual completion. Arbitrary notes, numbers/names/code, long text, image similarity,
audio/video/localization and real OCR/ASR enrichment are not covered by this pilot.

## Pre-score policy, not acceptance by arithmetic alone

The frozen configs retain A–D, budget24 per branch, weights1/1, semantic A/B and
cutoffs1/2/5/10; optional E is unselected. Policy semantics were reviewed against the
current runner **before any target scores**:

| Field | Accepted experimental interpretation |
|---|---|
| Regression0.05 | Absolute per-query Recall/MRR/nDCG limit at the compared last cutoff10, not an aggregate allowance |
| Gain0 | Conservative per-query C→D nDCG nondegradation hypothesis, not a product invariant or useful average-gain proof |
| Search30,000ms | Query preparation/public search/ranked conversion/first evaluation envelope, not isolated API latency or SLO |
| Index64MiB | Per-stage catalog and sidecar snapshot bound, not peak RSS or whole-disk usage |
| Calls200 | Cumulative attempted calls per supplied-provider meter/experiment, never sum of cumulative stage snapshots or execution permission |
| Groups8 | Minimum query-source groups per split; actual11, not multiplied by languages/subscopes |
| Negative ceiling10 | Structural only; six/seven-member scopes cannot exceed it, so it is not semantic refusal fitness |

Means, case counts, critical slices, raw paired differences, preparation and cost
observations remain necessary under [the original requirements](../proposals/native-media/04-mdrack-acceptance-quality-and-observability.md).
A raw future `POLICY_SATISFIED` would establish only these limited pilot criteria.
It cannot close missing bilingual/negative fitness or whole-profile usefulness.

## Actual normative checks and execution remainder

Current physical/native/quality-input validators and `require_evaluable` accept both
reviewed bundles as `frozen` / `real_reviewed`. Actual public source-script `inspect`
exited0 for each: 22 media, 52 cases, `evaluable=true`, `publication_ready=false`.
The immutable originals also inspect with exit0, but remain draft/evaluablefalse;
`require_evaluable` refuses them. Full current `RunConfig.parse`, not an extracted AST,
accepted both configs. Fifteen actual validator/config refusal controls and a damaged
public-inspect control passed; the latter exited1 with payload-free refusal.
Standalone JSON-Schema-engine validation was NOT_RUN; Python validators are normative.
This is source/callable CLI evidence, not an installed launcher qualification.

| Private approved artifact | SHA-256 |
|---|---|
| EN-assistance bundle | `149d2117aef2d42b9f3ce107345d2229bae0912f9601ff16f1dd58536c1e47cf` |
| RU-assistance bundle | `f1f64257004c5292e0ebabb38729931fbaef7c35182f9451d07e9e740f781a72` |
| Shared query reference | `878e9bb989b9621f72e5309421b2af111e458186eb1b0ce5ae45372704fe5918` |
| Shared judgment reference | `881593002913a65450afa856a7325f1498685e23ab85efcc9e43b0ae0f981754` |
| Shared group reference | `1cebfb305ff3c751d733f38ac75bb79881b33664de397b9342b1003dda703aa4` |

New acquisition accounting remains 34/48 GET, 52,577,990 bytes, 154.190376s; three
nominal20s attempts overran slightly and remain recorded/unwaived. Historical24 GET /
27,203,637 bytes /17.852104s and distinct authoritative-raw/normalized-ledger identities
are preserved. Independent review made **zero** model/GPU/LM Studio/lifecycle/factory,
HTTP GET, ranking/scoring or installation calls. Own checker-setup failures were
retained and corrected only in owned helpers, not product/tests or proposed inputs.

At this DATA-review cutoff the [runner contract](native-corpus-v2.md#current-real-provider-qualification-and-matrix-boundary)
still required the external B-role source disposition; approved data could not manufacture
DOCUMENT TextInput capability or make C/D-only valid. The later independent opt-in v2
result resolves that named functional blocker, not by changing data or the A–D/optional-E
matrix. Target execution still needs its concrete frozen families/profile/configuration
and a separately authorized hard budget with outer provider-close/exact-owned-unload
lifecycle. Keep both complete original/companion families; their overlap map is not an
executable mixed corpus and shared labels do not authorize copied model rankings. No
closed native/text proof ledger is renewed. Catalog scaling stays MEASUREMENTS_ONLY;
no relevance, latency SLO or whole-plan acceptance follows from this data approval.
