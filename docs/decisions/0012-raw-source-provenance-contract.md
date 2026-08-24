# ADR-0012: raw-source provenance contract

- Status: accepted contract, implementation-only R0
- Scope: app-owned pure provenance values for a future raw local-resource path
- Date: 2026-08-21

## Decision

MDRack defines `mdrack.raw-source-provenance.v1` in
`src/mdrack/ingestion/raw_source_provenance.py`. Raw resources use the generic
`mdrack_core.Locator` with kind `raw_local_source` and a normalized relative
POSIX `source_ref`; the document `SourceLocator` is not applicable.

The raw input digest is a separate lowercase `sha256:` value and is the
prospective raw resource content hash. `prepared_evidence_sha256` is a distinct
canonical digest for validated prepared artifacts. Existing prepared
transcript/video and direct-image hash meanings are unchanged and are not
backfilled.

The contract freezes four media kinds, eight signature kinds, payload-free fixed
error codes, and bounded defaults: 33,554,432 source bytes, 3,600,000 ms,
600 selected video frames, 8,388,608 prepared-text bytes, and 8,000 whole-
resource tokens. Signature facts must be probe-derived and MIME-compatible;
filename extensions do not establish media identity.

`RawSourceSnapshot` is transient and intentionally non-serializable. It holds a
private snapshot copy and execution-only path. A future adapter must hash the
bounded source before snapshotting and immediately before catalog replacement;
any mismatch returns `source_changed` and must not write a catalog record.
Only the allowlisted provenance metadata shape is suitable for persistence.

## Explicit non-scope

This R0 package does not add extraction, subprocesses, FFmpeg/OCR/STT/VLM,
providers, network access, SQLite writes, migrations, CLI/API behavior, or
public envelope changes. It does not claim raw-media ingestion or recognition
quality, installed-package behavior, Windows execution, or real-source evidence.
A later adapter must return to architecture ownership if it requires core,
media, sqlite, migration, public API, or raw absolute-path changes.

## R1 relation

R1 adds the CLI-only `ingest text` adapter. It accepts one explicitly selected
UTF-8 plain-text or Markdown file outside the scan root, preserves the R0 raw
digest, and stores one `raw_text` graph in the existing `catalog.sqlite3`.
Prepared evidence is hashed independently; `raw_local_source_span` is the
portable unit locator. No engine method, migration, provider, or second store
is introduced.

## R2 direct-image hardening

The existing `image ingest` command and `MDRackEngine.ingest_image` path now use
the frozen contract without adding a public surface. Image bytes are captured
once within the image budget and identified by stdlib magic bytes for PNG,
JPEG, GIF, or WEBP; a caller-supplied media type is an assertion, not an
override. Caption/OCR observations are bounded before provider calls and their
canonical prepared evidence receives a separate digest.

The existing single catalog replacement is guarded by an immediate bounded
source re-read. A changed source returns `source_changed` and leaves the
previous graph intact. Image locators and metadata contain only validated
relative POSIX `source_ref` values; CLI failures retain the payload-free
`IMAGE_INGEST_ERROR` envelope. No binary image bytes are persisted.

## R3 direct local WAVE adapter

R3 implements the first raw-audio adapter as the CLI-only command
`ingest audio`. It accepts RIFF/WAVE magic plus a parseable stdlib `wave`
stream, derives bounded duration, and captures the source before invoking an
explicit `--allow-external-stt --stt-command` executable with shell-free stdin.
The executable output is bounded and parsed only by the existing strict timed
JSON reader. A guarded write port rechecks the source immediately before the
single existing transcript replacement, decorates the resulting resource with
the raw digest and prepared-evidence digest, and leaves the previous graph
untouched on failure. The path remains provider-free, stores no audio bytes,
adds no engine API or migration, and does not claim live STT quality.

## R4 direct local ISO-BMFF video adapter

R4 adds the CLI-only `ingest raw-video` path. It accepts only ISO-BMFF `ftyp`
input outside the root and requires an explicitly authorized, shell-free stdin
extractor. The extractor must return exact `mdrack.raw-video-extraction.v1`
JSON with `media_type=video/mp4`, one positive matching duration, strict timed
transcript atoms, and 1..600 ordered selected captions. Existing transcript and
frame readers plus `VideoCompositionService` are reused once with embeddings
disabled; no decoder, provider, network, engine method, migration, or second
store is added. Raw SHA-256 is the resource content hash and prepared evidence
has its separate canonical digest. A bounded source recheck immediately before
the single catalog replacement preserves the previous graph on source change.
