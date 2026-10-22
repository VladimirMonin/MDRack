# ADR-0017: native temporal observations and bounded app preparation

Date: 2026-10-22. Status: independently accepted source/local and installed component
on Windows/CPython3.14.2; live runtime, quality and release remain unqualified.
Programme: original M03-07, following accepted W4 commit `fe606389`.

## Decision

Keep neutral Core/SQLite/publication/schema unchanged. Add four immutable native
media records/input and three provider-free builders to `mdrack_media`; preserve
all existing textual artifact invariants. App preparation separately implements
actual sampled frames, decoded audio windows and **direct visual MP4 clip** inputs.
Frames use image capability; audio/video each require their own typed capability.
No transcript, caption, filename or frame average is a substitute for these inputs.

Use existing bounded source byte/snapshot primitives, host authority, shared Core
validation/canonical space/value contracts, W4 source/dependency reconciliation
and bounded CAS, and the existing read-session/planner/grouping/RRF. Capture catalog
authority before I/O; perform awaitable provider/CPU work outside transactions.
Sync event-loop ownership stays at the CLI edge, not inside async engine paths.
Native range is purpose; policy/decoder/profile identify material/recipe. Exact
retained vectors may be reused; changed source and lifecycle invalidate stale jobs.

Use CPU FFmpeg/ffprobe with bounded protocols/output/time, actual decoded PTS,
explicit duration/range/sampling/prepared budgets, and child wait/cleanup on error
or cancellation. Native metadata has its own source schema rather than fabricating
old textual-video frame counts. Direct clips are visual-only; audio is independent.
These choices and precision/format limits are normative in
[native temporal v1](../contracts/native-temporal-v1.md).

## Versions and public integration

App `1.4.0rc3`, media distribution `1.0.0rc3` and media contract `1.0.0-rc.2` carry
this additive API. Core `1.0.0rc2`, SQLite distribution `1.0.0rc3` and schema0005 do
not change. Synchronize exact app/media pins/lock, installed importer inventory,
ordered exports, packaging/future-host consumers and current docs in this family;
third-party lock nodes do not change. Historical release packets remain historical.
App public exports append five names after accepted40; public models remain closed
and unchanged. Registered `native` commands invoke the same engine use cases.

## Evidence limits

Deterministic typed providers and explicit `SyntheticTemporalProvider` replace only
the external encoding edge. CPU decode, source checks, SQLite, engine and CLI are
real local components. The synthetic profile hashes supplied content and has no
semantic quality interpretation. It is never a fallback from production LM Studio.
The selected production HTTP/SDK embedding route remains textual; no native payload,
model absence, model call, download, GPU use or runtime/settings mutation is inferred.

Independent source/local and fresh noneditable installed verification qualifies this
bounded component, including current-role/input candidate resolution and effective
pre-limit scope filtering; W5-R1 is closed. Affected31 strict is green; all103 exact
inherited whole-static diagnostics remain red, with no new or missing diagnostics.
This does not qualify native model transport or relevance. Real corpus/language/source-
group/held-out, public A–D/optional-E quality runner, paired enrichment and cost
policy remain original W5 obligations. W6/global/full/ordinary-process/release
campaigns remain OPEN. Scoped repository delivery is separate from tag or package
release approval.
