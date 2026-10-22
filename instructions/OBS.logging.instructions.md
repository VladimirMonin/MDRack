---
applyTo: "**/*.py"
name: "OBS.Logging"
description: "When to use: add, change, review, sanitize, or test logging, diagnostics, CLI error reporting, provider lifecycle, or support evidence."
---

# Logging and diagnostics

## Responsibility

Keep MDRack observable without leaking credentials, user Markdown, search text, private
absolute paths, raw URLs, embedding payloads, provider bodies, or database contents.

## Rules

- Production code uses Python `logging` with a module logger; do not use `print`.
- stdout is reserved for documented CLI JSON/output. Logs and diagnostics must not corrupt it.
- Prefer stable event names and allowlisted structured fields over interpolated prose.
  Verify the actual serializer: extra-only fields disappear in a message-only formatter.
  Core SafeEvent already serializes safe fields into its message; do not duplicate it.
- Use lazy formatting (`logger.info("indexed count=%d", count)`) when structured fields are unavailable.
- Log lifecycle and safe branch outcomes for important operations: started, completed,
  skipped/degraded/retrying/failed, with reason categories and counts/durations.
- Do not add noisy per-token, per-vector, or per-character logs in hot loops.
- One shared redaction/event primitive may be frozen for `mdrack_core`; indexing and
  retrieval own separate event payload schemas. Downstream lanes must not fork the
  primitive or create competing event vocabularies.
- Preserve exception context with `logger.exception` only after ensuring the message,
  arguments, chained error, and attached metadata cannot contain sensitive values.

## Safe fields

Generally safe: operation, status, reason/category enum, logical/internal correlation ID,
counts, lengths, byte sizes, dimensions, elapsed milliseconds, attempt number, parser or
strategy version, profile fingerprint, HTTP status class, and boolean presence flags.

Sensitive unless explicitly sanitized: Markdown/query/alt/frontmatter text, embedding arrays,
raw CLI args, credentials, headers, provider request/response bodies, database rows, absolute
paths, vault names, raw URLs, host/IP/port, and exception strings derived from any of these.
Relative paths are public DTO data but can still reveal user organization; prefer logical IDs
or a documented safe reference in logs.

## LM Studio/provider boundary

Log operation, safe model/profile identity, dimensions, batch count, timeout, attempt,
status class, degradation reason, and duration. Never log embedding inputs/outputs, raw
endpoint strings, authorization material, or provider bodies.

## Diagnostics

Diagnostics and support exports obey the same redaction rules as logs. They must not create
a second, less-safe representation. CLI-facing errors use stable categories/codes and safe
messages; detailed internal traces stay out of stdout JSON.

Evaluation, support, doctor, and release evidence are observability surfaces, not a
privacy exception. They must use safe references/categories and must not emit raw
queries, source/generated text, paths, IDs derived from private locations, endpoint
parts, metadata/facet values, vectors, provider bodies, or raw exception strings.

## v0.3 privacy oracle

For success, empty, degradation, validation/storage/provider failure, and cleanup
failure, capture logs, CLI stdout, CLI stderr, diagnostics, and generated
eval/support/release JSON. Supply distinct sentinels for query; Markdown/OCR/caption
text; relative/absolute path; root name; URL and host/port/path fragments; vector;
frontmatter/metadata/facet; provider body; and private-derived exception text.
Fail if a sentinel appears outside an explicitly tested intentional public result
payload. Assert that CLI stdout remains one parseable JSON object.

## Actual sink and product-state boundary

On synthetic input exercise emitter/filter/formatter/handler and flush/drain, then
inspect nonempty actual serialized bytes: needed safe counts, reasons and durations
must be present AND forbidden sentinels absent. caplog.records or an empty/disabled
sink alone does not prove useful persistent privacy. Unknown is null/omitted, not 0;
real zero remains 0, with units and accounting scope. Test affected exports separately.

Product owners determine search/commit/retry state; logging only reflects it. Required
sink I/O isolation must not turn a successful search or confirmed commit into logical
failure/rollback. Distinguish sink I/O from schema/programming errors; never suppress
the latter as diagnostics failure or write raw fallback. Verify product result/reopen,
CLI exit/one JSON independently. Isolation applies to the routed emitters below,
not every existing logging call. No root reconfiguration/handler per library call,
second sanitizer, log database or mandatory unbounded file. Bound noise, bytes and
retention for an explicitly chosen sink; DEBUG does not authorize user content.

### Current private owners and limits

- `src/mdrack/_observability.py` owns the finite existing app/CLI vocabulary:
  `AppEvent` validates event names, allowed fields and value types; `AppEventLogger`
  puts the sanitized fields in the message as JSON, not only in `extra`. It reuses
  core `SafeEvent`/`_sanitize_value`; it is not an open-field adapter or public API.
  Unknown schema keys/types fail; arbitrary strings/foreign enum labels are redacted.
- `packages/mdrack-core/src/mdrack_core/observability.py` owns shared sanitization and
  private `_log_safely`/`_deliver_record` delivery. Core `emit_event` uses this path.
  LM Studio's private `_log_message`/`_log_event` in
  `src/mdrack/integrations/lmstudio/client.py` reuse it without changing provider
  messages, request recipes or intended provider errors. Do not fork these mechanics.
- Delivery isolates `OSError` from handler `emit`, including standard file open,
  write and inline flush. The additional closed-stream `ValueError` exception is
  limited to standard StreamHandler/FileHandler emit with an exact `io.StringIO`
  or `io.TextIOWrapper` whose `closed is True`. Subclasses and unknown streams do
  not qualify merely by exposing `closed`; custom emit has no broad ValueError catch.
- Event validation, record factories, logger/handler filters and pre-rendered
  formatting remain outside the delivery-I/O catch. Their errors, including
  `OSError`, unknown fields, nonfinite numbers and programming `ValueError`, must
  propagate. Never catch all exceptions or treat a formatter defect as sink loss.
- `_diagnostic_loss_count` is a content-free in-process counter saturating at
  `2**31 - 1`, not durable telemetry, a public metric or a commit acknowledgement.
  Loss neither recursively logs through the failed sink nor invokes the standard
  raw traceback fallback. Logger levels, filters, propagation, locks, host handler
  identity and `lastResort` remain intact; never assign its read-only stream.
  Only a newly opened delayed FileHandler stream is transferred to its owner.
- This boundary is not a sandbox for host-installed filters, formatters or custom
  handlers. Their side effects, blocking, swallowed errors, separate later flush/
  queue drain and arbitrary host serializers are outside the guarantee. The host
  owns sink selection, drain, rotation and retention; legacy error envelopes and
  eval/support exports still need their own privacy checks.

## Verification

- Capture CLI stdout in tests and prove logs do not corrupt JSON output.
- Exercise success, skip/degradation, retry, and failure branches added by the change.
- Search changed logging calls for raw paths, queries, payloads, secrets, and eager f-strings.
- Exercise standard stream/file open/write/flush/closed and `lastResort` delivery,
  repeated handler identity/cleanup, and negative filter/formatter/custom-stream
  controls; assert loss accounting changes only for supported delivery failures.
- Compare the full search DTO under sink-only failure; after a confirmed commit,
  check actual CLI exit/one JSON and fresh SQLite reopen, not only mock call counts.
  Keep core/app/LM Studio/CLI observability regressions together with schema,
  foreign-label, true-zero/unknown and actual-byte privacy controls.
- Run the gates in `TEST.quality-gates.instructions.md`.
