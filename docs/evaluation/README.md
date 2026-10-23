# Evaluation contracts

- [Quality v2: ranked input, strict reader/serializer, manual oracle and offline command](quality-v2.md).
- [Native input-side corpus v2 and common-public A–D/optional E runner](native-corpus-v2.md) — frozen inputs/policy, paired enrichment and explicit blocked coverage; source/local and installed synthetic mechanics, not real-model quality.
- [Frozen public corpus/query/benchmark v1](public-corpus-contract-v1.md) — immutable historical fixture, not migrated by v2.
- [v1.1 fixture contract](v1.1-fixture-contract.md).
- [Privacy evidence oracle](privacy-evidence-oracle.md).

Evaluation correctness, provider usefulness, production search latency and installed
release readiness are separate evidence boundaries. The v2 manual oracle proves
only synthetic/offline evaluator and serialization behavior.

## Current-tree historical v1 check

Run `uv run python scripts/check_v11_input_freeze.py --check` to verify the unchanged
66 historical inputs and manifest in an owned temporary original-input view. The
unchanged original generator checks that view; exactly four separately versioned v2
schema additions are admitted in the shared schema directory, not added to the v1
freeze. This read-only check does not regenerate labels, input hashes or results.

## Current bounded verification

The strict repair and connected suite K1–K7 are independently accepted on Windows /
CPython 3.14.2. The full pre-taxonomy-correction checkout selected 2412 cases:
2406 passed, six historical skips, no failures or deselection. The subsequent one-file
ledger correction passed all 28 original affected checks plus a real host-artifact /
catalog-backed sibling discriminator. Unchanged strict213, Ruff/boundary and four-wheel
233-payload/installed-console evidence is retained by exact impact, not rerun here.
The current read-only skill and separate committed-input control remain distinct;
whole user-dirty skill content is not part of this repository delivery.

These are software/fixture/installed-boundary results, not pixel, acoustic, OCR/model
quality or fitness acceptance. The tiny paired-instance text-model pilot and separate
caller-caption CPU baseline remain limited results, not competing-model or full-corpus
proof. Next execution order is maintained in the
[active plan](../../.hermes/plans/2026-10-21-native-media-foundation.md#current-acceptance-and-next-execution).
