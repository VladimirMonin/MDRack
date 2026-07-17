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
- Prefer stable event names and structured `extra` fields over interpolated prose.
- Use lazy formatting (`logger.info("indexed count=%d", count)`) when structured fields are unavailable.
- Log lifecycle and safe branch outcomes for important operations: started, completed,
  skipped/degraded/retrying/failed, with reason categories and counts/durations.
- Do not add noisy per-token, per-vector, or per-character logs in hot loops.
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

## Verification

- Capture CLI stdout in tests and prove logs do not corrupt JSON output.
- Exercise success, skip/degradation, retry, and failure branches added by the change.
- Search changed logging calls for raw paths, queries, payloads, secrets, and eager f-strings.
- Run the gates in `TEST.quality-gates.instructions.md`.
