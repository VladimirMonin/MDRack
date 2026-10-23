# ADR-0015: Ready OCR is an application merge, not another replacement writer

Status: first ready-OCR family independently accepted on Windows/CPython 3.14.2,
including source/local components and non-editable installed packages. Broader W4,
live runtime, quality and full-release qualification remain separate.

## Decision

The application consumes the accepted neutral publication port and shared Core
validator. `application/enrichment.py` owns pure captured inputs, explicit active
purpose/variant slots, immediate artifact dependencies, exact merge outcomes and
bounded freshness-checked CAS remerge. `application/ready_ocr.py` owns bounded
caller-file availability/origin/digest validation and the shared engine/CLI route.
It never implements SQL, a second validator, a model pipeline or a cache lifecycle.
Retained dependents are invalidated transitively by changed immediate-input material,
not only changed revision labels: corrected OCR under the same source/artifact
revisions must not leave vectors derived from the previous text eligible. The legacy
ready-OCR projector also refuses automatic lexical publication over an active manual
slot for the current source/lifecycle, preserving its graph and publication state.

The first route is existing image + ready whole-image OCR, lexical-only. Retained
caller-owned original files are checked, not asserted durable through metadata.
Unavailable host durability refuses, and read-only retention never enables FTS.
Source generations are explicit app provenance, distinct from raw/prepared hashes;
missing historical provenance stays missing. A selected original image can be
validated through the existing source resolver without learning paths from the DB.

Image producer capability is explicit at app composition. Both the engine and
registered `image ingest` CLI use the conditional producer/reindex path, including
its automatic-over-manual refusal; the CLI is not a destructive bypass. A source-validated
ready-projection rescan preserves the complete enriched graph through CAS, while
explicit provider calls retain their original execution/failure semantics. Legacy
standalone storage injection does not acquire publication merely because a concrete
adapter happens to have similarly named methods on an older schema.

## Consequences and compatibility

No Core/SQLite/schema changes or package-pin refresh. Additive engine methods,
public operation/error exports and registered CLI commands are documented in the
[ready OCR contract](../contracts/ready-ocr-v1.md). Existing import/export manifests
remain graph data, not portable lifecycle authority. Existing text/image commands
and their envelopes remain available. Other media enrichment, changed-source full
rebuilding, archival storage, live inference, quality and release qualification are
separate obligations, not implied by this route.
