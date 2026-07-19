# Public evaluation corpus contract v1

Status: frozen fixture contract for offline evaluation plumbing. This document and the files under `tests/evaluation/` do not claim retrieval quality, provider quality, performance, or an optimized policy.

## Published identities

| Artifact | Contract | Immutable identity |
|---|---|---|
| `tests/evaluation/corpus-v1/manifest.json` | `mdrack.evaluation-corpus`, schema 1 | `sha256:2183e11b7e80b660e178322ae1305392c3311a801a749064e1845aeb3b78a534` |
| `tests/evaluation/queries-v1/queries.json` | `mdrack.evaluation-queries`, schema 1 | `sha256:91dd1b7e8b2531702069bdc83c442170881c72843413bb0853d17388bb42b1a7` |
| `tests/evaluation/benchmark-v1/manifest.json` | `mdrack.evaluation-benchmark`, schema 1 | `sha256:1a2df8d9c85fc4ebe0ac1b1645d56ee58fafe0880b0cb3a76f92a473b9c4614d` |

A contract digest is SHA-256 over UTF-8 canonical JSON with sorted keys, compact separators, one trailing LF, and the top-level `contract_digest` field omitted. Every corpus artifact has a separate byte digest. Any byte, label, normalization, policy reference, or judgment change requires a new contract digest and corpus/query version.

## Corpus and publication ledger

The checked-in fixture is entirely synthetic, text-only, de-identified, and publishable under `CC0-1.0`. It contains no media binaries and requires no extractor or provider.

| Resource class | Count | Prepared public artifact |
|---|---:|---|
| Markdown document | 20 | UTF-8 Markdown |
| image | 10 | synthetic OCR/caption JSON |
| audio | 10 | synthetic timed transcript JSON |
| video | 10 | synthetic timed transcript JSON |
| videos with frame captions | 5 | frame IDs, captions, and integer-millisecond timestamps in video JSON |

Every resource records an opaque ID, resource/media kind, fixed public namespace, repository-relative artifact reference, artifact/content digest, representation declarations, units, origin classification, SPDX license, PII review status, and publication decision. IDs contain no source title, path, user name, URL, or provider value.

Publication licenses use an exact allowlist: `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `MIT`, `Apache-2.0`, `BSD-2-Clause`, and `BSD-3-Clause`. A license expression may combine only these identifiers with SPDX `AND`, `OR`, and balanced parentheses. `LicenseRef-*`, `NONE`, `NOASSERTION`, proprietary/fake identifiers, exceptions introduced by `WITH`, and every identifier outside the allowlist fail closed even when `publishable` is true.

The corpus has a one-resource-to-one-artifact publication policy: artifact references and artifact byte digests are both globally unique. Per-resource unit ordinals are zero-based, ordered, contiguous, and unique. Timed intervals are integer milliseconds, half-open `[start_ms, end_ms)`, ordered by start, and non-overlapping within a resource. Frame evidence is an integer timestamp plus opaque frame ID; frame timestamps are unique and ordered within a resource. Unit IDs are globally unique and belong to exactly one resource.

## Query and judgment ledger

The query set reaches the roadmap minimum directly:

| Case kind | Count |
|---|---:|
| lexical | 50 |
| semantic | 50 |
| hybrid | 30 |
| resource similarity | 20 |
| timestamp | 20 |

Each case includes an opaque query ID, input-only public synthetic query text, retrieval mode and target, explicit textual basis, allowed resource/representation/unit kinds, fixed Recall@5/10, MRR@10 and nDCG@10 cutoffs, diagnostic slice tags, and one or more graded judgments from 0 through 3. A scored case must have at least one positive judgment. Zero-gold cases fail validation instead of producing a zero metric.

The typed case matrix is frozen:

| Case kind | Mode | Target |
|---|---|---|
| lexical | text | unit |
| semantic | semantic | unit |
| hybrid | hybrid | unit |
| resource similarity | similarity | resource |
| timestamp | hybrid | unit |

Every unit-target judgment must name a unit. Resource-target judgments must not contain a unit or temporal evidence. Every judgment basis must equal its case basis; unit judgment bases must also match the unit representation, and case bases must be admitted by the declared representation slice. Every timestamp judgment carries exact interval or frame evidence.

Judgments may target a resource, a unit, an exact half-open timed interval, or an exact frame timestamp. Cross-document validation rejects missing/foreign resources or units, interval drift, frame drift, duplicate IDs, duplicate judgments, and judgments outside the declared slice.

Query text and raw judgment content are input fixtures only. Release, support, evaluation, and benchmark reports must use ordinals or the opaque/digested identities defined here; they must not copy query text, artifact text, paths, source locators, or raw labels.

## Benchmark fixture

`benchmark-v1/manifest.json` freezes the Stage 13 materialization matrix:

- units: 1,000; 10,000; 50,000; 100,000;
- dimensions: 384; 768; 1,024;
- all 12 cross-product cells;
- fixed operation names for import, replace, FTS, linear semantic scan, hybrid, grouping, RRF, duplicates, similarity, reopen, and migration.

The matrix is deliberately `gated_manifest` under `W5-B13` with deterministic seed `20260719` and policy `generated-no-binaries`. Large datasets and vectors are not checked in. This manifest is a reproducibility contract, not benchmark evidence, a capacity promise, or a backend decision.

## Schemas and validation

The normative JSON Schema documents are:

- `tests/evaluation/schemas/corpus-v1.schema.json`
- `tests/evaluation/schemas/queries-v1.schema.json`
- `tests/evaluation/schemas/benchmark-v1.schema.json`

The strict cross-file validator is `tests/evaluation/contract_validator.py`. It adds semantic checks that JSON Schema alone cannot express: canonical digests, artifact byte digests, safe relative paths, ID and artifact uniqueness/ownership, ordinal and timeline invariants, exact roadmap counts, provenance/license/PII publication policy, the typed case matrix, target-shaped judgments, basis coherence, temporal/frame linkage, zero-gold rejection, complete benchmark cells, and cross-contract digest references.

Run:

```bash
uv run python tests/evaluation/generate_fixture.py
uv run pytest tests/evaluation/test_public_contract.py
```

The generator is deterministic and validates its output before returning. It is not a migration tool: review any regenerated diff and bump versions whenever published bytes or labels change.

## Failure and privacy contract

Validation fails closed for:

- unsupported schema or unknown/missing fields;
- duplicate JSON keys, resource IDs, unit IDs, frame IDs, query IDs, or judgments;
- missing artifacts or any digest mismatch;
- insufficient roadmap counts;
- missing or non-positive gold;
- invalid, drifting, or foreign interval/frame evidence;
- unreviewed PII, non-publishable classification, or absent/unsafe license;
- POSIX absolute paths; Windows drive paths with slash or backslash; UNC, device,
  or slash-normalized network paths; parent-traversing paths; and URI locators
  using any syntactically valid scheme;
- duplicate artifact references/digests, non-contiguous ordinals, overlapping intervals, or duplicate frame timestamps;
- invalid case-kind/mode/target combinations, target-mismatched judgments, or incoherent bases;
- incomplete or ungated benchmark matrices.

Before publication, every string key and value in the corpus, query, and benchmark
documents is scanned after JSON decoding. Artifacts declared as JSON, or accepted by
a strict JSON parse, are likewise traversed recursively so every decoded string key
and value is scanned as an exact token; malformed artifacts declared as JSON fail
closed. Other UTF-8 artifacts use the same locator policy through a boundary-aware
text scanner. The locator classifier preserves the contract's exact `sha256:`
identities and diagnostic tag namespaces, ordinary prose whose colon is followed by
whitespace, relative repository references, MIME types, and numeric timestamps. It
rejects all other URI schemes plus the path families above.
The privacy sentinels cover email addresses, IPv4 literals, private-key headers, and
Basic/Bearer credential forms. This is a deterministic fail-closed policy, not a claim
that pattern matching can prove de-identification; `reviewed_no_pii` remains mandatory.

Every prohibited locator returns the single generic error `Published value contains a
prohibited locator`. Other errors name only the violated contract rule. No error echoes
offending query text, private locator values, privacy sentinel values, artifact content,
or raw exceptions.

## Evidence boundary and non-claims

This fixture proves only that a public, immutable, provider-free corpus/query/benchmark contract can represent document, image-text, timed transcript, frame-caption, unit/resource, similarity-basis, and diagnostic-slice evaluation inputs at roadmap counts.

It does not prove:

- retrieval relevance or metric thresholds;
- LM Studio, OpenRouter, OCR, caption, Whisper, or VLM quality;
- visual, acoustic, or native-media similarity;
- benchmark latency, memory, capacity, or backend suitability;
- real/private corpus behavior;
- Windows execution;
- that any current chunking, frame-caption, centroid, or ranking default is optimal.
