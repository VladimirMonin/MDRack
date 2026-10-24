# MDRack operations and troubleshooting

This runbook covers ordinary local operation. MDRack stores derived SQLite state;
Markdown and explicitly supplied media/image inputs remain read-only sources.
Normal startup uses only `<store>/catalog.sqlite3`; it has no generation,
candidate, activation, rollback, retention, or old-store migration mode. For a
bounded offline acceptance pass over synthetic fixtures, use
[one-store acceptance evidence](one-store-acceptance.md); it is not a normal
operational command or a recovery procedure.

## Routine commands

Run commands from the repository checkout with `uv run` and select the source root
explicitly:

```bash
uv run mdrack --root ./notes init
uv run mdrack --root ./notes scan
uv run mdrack --root ./notes status
uv run mdrack --root ./notes doctor
uv run mdrack --root ./notes search "query" --mode text
```

Use `mdrack <command> --help` before an uncommon or destructive-looking operation.
The normal command surface has no alternate-catalog or generation-management
commands. `rebuild fts` and `rebuild embeddings` update only derived search data
in the fixed catalog.

## Agent workflow and private data

MDRack is normally operated by an agent under a human's explicit authority. The
agent must distinguish a source-checkout command (`uv run mdrack`) from a separately
installed `mdrack` command, obtain an explicit `ROOT`, and name every operation that
will alter derived state before it runs. `guide`, `--help`, text search, `status`,
and `doctor` are suitable read-only discovery steps; initialization, scanning,
explicit ingest, rebuild, deletion, and provider/model actions are not.

Keep private human material outside the repository checkout. An authorized workflow
may scan Markdown below the selected root or ingest exactly selected raw
text/Markdown, direct-image, WAVE, or ISO-BMFF inputs. The WAVE and ISO-BMFF paths
need their explicit authorization flags and a caller-selected local executable; do
not silently substitute a tool, provider, or remote service. Search with text mode
and, when useful, a unified scope. Retain logical IDs and portable locators or
available line/time bounds rather than copying source text or paths into an issue.

After a requested write completes, start a fresh CLI process with the same root and
run `status`. It reopens the existing derived store and reports aggregate state;
also verify independently that selected source bytes did not change. This is a
real agent-use workflow only when the installed public CLI or engine is exercised
on separately authorized human-like private data. Synthetic fixtures, fake
providers, and offline checks remain supporting evidence and must not be reported
as that real-use boundary.

## Output, logs, and privacy

The CLI reserves stdout for one documented JSON object. Application logs go to
stderr and use stable events, reason categories, counts, lengths, dimensions, and
durations where possible. Keep these streams separate when automating:

```bash
uv run mdrack --root ./notes status >status.json 2>status.log
```

Do not publish raw logs or diagnostic output without inspection. Queries, Markdown
or OCR/caption text, vectors, credentials, provider bodies, raw endpoints,
absolute/private paths, metadata/facet values, and private exception strings are
sensitive. `doctor`, evaluation output, support bundles, and release evidence obey
the same privacy boundary; they are not exceptions.

The root `.mdrack/` directory, SQLite files, logs, caches, and local reports are
generated/private state and must not be committed.

## Diagnose common failures

### Configuration fails before the command

An explicit missing, unreadable, or invalid `--config-file` returns
`CONFIG_ERROR` without echoing the private path or parser exception. Check TOML
syntax and precedence:

1. built-in defaults;
2. `<root>/.mdrack/config.toml` or explicit `--config-file`;
3. `MDRACK_<SECTION>_<FIELD>` variables;
4. command-line overrides.

Run `uv run mdrack --root ./notes status` after correction. See
[getting started](getting-started.md#configure-lm-studio) for a minimal config.

### LM Studio or semantic search fails

Text search is provider-free, so first isolate storage/index health:

```bash
uv run mdrack --root ./notes search "query" --mode text
uv run mdrack --root ./notes status
uv run mdrack --root ./notes doctor
```

Then check `uv run mdrack model --help` and verify that the configured endpoint,
model identity, output dimensions, and profile match the running LM Studio
instance. Do not paste provider bodies or raw endpoint details into an issue.
Changing model or dimensions requires an explicit embedding rebuild.

A loaded model or Vision label is not proof that an embedding endpoint accepts pixels.
The checked LM Studio 0.4.26+4 `/v1/embeddings` wrapper accepted text but rejected typed
PNG content at its string-only guard before embedding. See the
[native input qualification](contracts/native-image-v1.md#lm-studio-input-qualification).
Do not substitute filenames, captions or base64-as-text for image vectors, remove
capability refusals, or assume that reloading/updating a model repairs an API/worker
contract. A concrete supported image-embedding input/output contract must be qualified
before using that route; no replacement runtime is implied.

The earlier [external-host canary](evaluation/native-image-cuda-canary-2026-10-23.md)
and later [normal product qualification](evaluation/native-image-product-2026-10-23.md)
are separate. The latter actually qualifies explicit prepared-provider injection and
normal engine/catalog/read/reopen plus installed PE. It does not repair HTTP input.

### Functional textual A and evaluator factories

The [short-text qualification](evaluation/text-provider-functional-2026-10-24.md)
exercised existing plain DOCUMENT and prefixed QUERY transport at actual context 2048.
It does not qualify the adapter's 8 MiB declaration as a server/token limit, infer an
HTTP text-kernel device from GPU-max/runtime/residency, or establish semantic quality.
Do not normalize/truncate outputs or mix its full space with native/legacy vectors
merely because all have 768 components.

For an explicitly authorized evaluator trial, follow the [runner contract](evaluation/native-corpus-v2.md):
caller-owned no-argument factories receive no automatic model acquisition or hard budget.
Preflight the exact intended instance/artifacts/context, keep a fresh explicit ledger,
then close providers and unload only that owned instance in outer `finally`. The current
script has no provider-close finally. A closed proof ledger or unavailable alias is not
a new execution allowance or JIT/fallback route. Private factory glue is not bundled
installed support. The separately qualified [native text/image v2 composition](evaluation/native-text-image-product-2026-10-24.md)
closes the named native B text-DOCUMENT functional blocker; default/v1 remains unchanged.
Required A/B/C/D(/E), policy and coverage gates are not relaxed. The later
[partial-pilot and repair disposition](evaluation/native-fts-repair-2026-10-24.md)
retains eight failed complete-report attempts, not a completed target-corpus matrix or
optional-E experiment. Do not fabricate roles or reinterpret C/D-only as accepted configuration.

### Reviewed private evaluation inputs

The [independent caption-pilot review](evaluation/native-corpus-review-2026-10-24.md)
accepts own frozen data and a limited pre-score experimental policy, not a model trial.
Keep media/text/credits/URLs private and use only the explicitly admitted source allowlist.
Pooled unknowns stay unknown, negative gold applies only to the declared6/7-member
subcatalogs, and paired assistance variants are not strict bilingual query coverage.
The policy's per-query bounds/structural negative ceiling are not user SLAs or semantic
fitness. Functional v2 resolves the named B-role blocker separately from DATA; freeze a
concrete target input/profile/config/executor scope and fresh authorization before its
model dispatch. Approved data does not reopen closed proof budgets.

### Keep original and paired companion experiments separate

The [reviewed paired companion](evaluation/paired-query-companion-review-2026-10-24.md)
has its own exact metadata/physical root. Retain the complete original 52-case family,
including its harder cases, and use its actual approved root rather than an older
source-ledger substitution. Do not append the now assistance-copy original queries to
the companion or treat the 54-case union as executable input. Shared labels never license
sharing model results when assistance changes. Four pooled unknowns and policy bytes
remain unchanged. One concrete strict held-out pair is DATA evidence, not robust
multilingual or abstention fitness. Future target-campaign profile/space/runner-plan
bindings remain unbound until a separately named qualified phase; the closed pilot's
historical bindings are not authorization for another run.

<a id="explicit-caller-owned-native-image-encoder"></a>

### Explicit caller-owned native image/text encoder

Use only trusted qualified artifacts and an existing private work directory, following
[the caller recipe](../examples/native_cuda/README.md). From the checkout, configure
with `uv run --offline --no-sync python examples/native_cuda/configure.py --help`;
then explicitly select `image native-ingest` / `image native-search --provider external
--host-config CONFIG`. CONFIG is private caller composition, never indexed content or
a profile certificate. Default/other commands still use their existing providers.

The default v1 protocol accepts fixed 224×224 RGB PNG documents and text queries for
images. Configure the separately qualified v2 artifacts explicitly with
`--protocol mdrack-cuda-retrieval-v2` for short UTF-8 text DOCUMENT and one text QUERY
route over image/text candidates. Formatted bytes are at most 65,536, full token count
at most 2,048 and native timeout at most 120 seconds; there is no silent truncation.
This is existing prepared import/API/runner composition, not a new text-ingest command.
Image QUERY/SIMILARITY, audio/video inference and document-vector reuse remain unsupported.
Do not mix its distinct full space with v1/A/legacy vectors, rekey an old catalog,
change pins silently, weaken refusals or infer general quality/latency. Python/Core /
application/default providers do not load weights/vendor DLLs. The caller owns the
separate process, resource availability and close/reap; no new service/model is implied.

Public `read unit` intentionally redacts source coordinates. Join its logical ID and
whole-image kind with the PUBLIC search evidence locator/revision, resolve only through
an explicit caller allowlist and hash/decode the original. Do not invent a path from
an ID/filename or widen read redaction. Manual prepared lexical enrichment can retain
the same native vector without encoding; any later semantic query is separately counted.
Temporary producer files/cache are removed after reap. Optional bounded PRIVATE native
diagnostics may contain sensitive paths/details: default off, never logs/support/Git.

### Search is slow although an engine is already constructed

Normal uninjected search opens and fully verifies its own readonly catalog per operation.
Engine construction is lazy; a surviving engine object does not make later requests
already-open searches. The
[scoped catalog/search measurements](evaluation/catalog-search-scaling-2026-10-23.md)
report verified-open, already-open and default repeated timings separately. They use
synthetic stored vectors, not model/query-encoding or CLI process-startup latency.

Preserve schema, integrity/FK, graph/FTS/vector and operation-owned snapshot checks.
An injected open catalog is a different consistency boundary, not a general production
speed fix. The measurements do not establish concurrent-write safety or an applicable
latency SLO; retain the actual route, counts, runtime and warm/cold definition in diagnosis.

### Guarded evaluation after a closed partial run

The [software repair and historical pilot](evaluation/native-fts-repair-2026-10-24.md)
are separate:37 individual external operations exceeded24, even though useful partial
rankings and native-device receipts exist. No complete canonical experiment report or
overall policy result was emitted. Keep all prior spend and failures; neither unused
units/time nor a new caller counter permits another target invocation.

Before a separately authorized run, freeze the accepted source/installed identities,
full original and companion families, policy, provider spaces/roles, runner plan and
actual transport/lifecycle ownership. Use the existing public factory/runner ports;
there is no public resume-D/query-vector-cache interface. A vector hash is not a query
vector, and old partial reports cannot be merged into a claimed complete invocation.

The private caller's synthetic proof covers durable reservation before each individual
command/metadata leaf and every awaited provider encode, not before a grouped wrapper.
Its proposed schedule is8 setup plus6 final controls within24, leaving10 contingency;
protect the final-control/time reserve and close/unload only owned resources. The single
separately bounded monitor must retain its own session/frame limits: no new per-invocation
resource collections disguised as sampling or restarted sessions. Failed/interrupted
reservations stay spent, pending work requires its original exit/cleanup evidence, and
stale ledger/witness disagreement stops dispatch. Actual adapter/model/profile/data/grant
bindings are still required; this private glue is not shipped support or a sandbox for
arbitrary code bypassing the guarded ports. The public script still has no provider-close
finally, so the outer owner must close every constructed delegate on all outcomes.

### Text search or FTS is inconsistent

FTS5 boolean keywords are case-sensitive. Ordinary lower/mixed-case words in a plain
query such as `alpha And beta.` may use the existing quoted-literal retry after a syntax
error. Explicit uppercase operators, quotes, parentheses, stars and column filters are
not silently reinterpreted. This is one shared catalog/legacy helper, not a tokenizer,
schema or ranking change. A declared evaluator search-branch failure now keeps the
planned denominator and emits BLOCKED with `search_branch_failure`; it does not become
an empty successful ranking or a quality PASS. See the
[runner contract](cli-contracts.md#native-corpus-evaluator-search-failures).

Preserve the store first, stop writers, and inspect diagnostics. If the SQLite
store is otherwise healthy, the explicit command is:

```bash
uv run mdrack --root ./notes rebuild fts
```

Do not delete the database to hide a migration or integrity failure. Unknown
migration versions, non-v2 schemas, and mixed SQLite store layouts fail closed.

### The fixed catalog fails validation or integrity checks

Stop all writers and preserve the complete store directory (catalog plus any
WAL/SHM sidecars) for diagnosis. Normal MDRack does not select a replacement
catalog or migrate old data. Cleanup or recreation is destructive and separately
authorized; use the privacy-safe `status`, `doctor`, and `storage-analyze` output
to report the failure without sharing the store itself.

### A command exposes an unexpected identifier or capability

Consult [CLI contracts](cli-contracts.md) and the
[public interface matrix](current-architecture/public-interfaces.md). `files`,
`read`, resource, and search contracts use logical IDs and portable locators;
the removed `sections` command has no normal-operation contract. See
[current limitations](current-architecture/limitations.md) before treating a
missing GUI, server, reranker, visual search, ANN, or remote provider as a defect.

## Safe issue report

Include:

- MDRack version and command name (not private arguments);
- stable JSON error code and safe reason category;
- Python/OS version and whether the boundary was source checkout, installed package,
  local component, or live provider;
- aggregate counts/dimensions and reproducible sanitized steps;
- whether source hashes remained unchanged.

Exclude source content, private paths, database files, vectors, credentials, model
request/response bodies, raw URLs/endpoints, and `.mdrack/` contents.
