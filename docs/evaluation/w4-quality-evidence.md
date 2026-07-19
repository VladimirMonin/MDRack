# W4-EVAL provider-free quality evidence

`tests/evaluation/` is the frozen synthetic corpus/query contract. This runner evaluates
ranked prepared units without opening media, resolving locators, or calling a provider:

```bash
uv run python scripts/media_quality_eval.py \
  --root tests/evaluation \
  --output /tmp/mdrack-w4-quality.json
```

The report contains ordinal per-case results, macro metrics, case-kind and diagnostic
slice aggregates, duplicate rate, evaluation latency, and exact corpus/query/runner
fingerprints. It includes no query text, source text, paths, or unit IDs.

Metrics:

- Recall@5 and Recall@10 over positive graded judgments;
- MRR@10 and graded nDCG@10;
- exact timestamp hit@K, interval overlap hit@K, best interval IoU, and absolute
  start/end errors where temporal evidence is present. Temporal aggregates report
  `temporal_cases` and exclude non-temporal cases from their denominators;
- duplicate result rate, unit/case counts, and local evaluation latency.

The checked-in fixture has 20 Markdown, 10 image-text, 10 audio, and 10 video resources;
20 videos are not claimed and only the five frame-caption resources in the fixture are
eligible for frame evidence. The corpus contract has 50 lexical, 50 semantic, 30 hybrid,
20 resource-similarity, and 20 timestamp cases. The current runner uses a deterministic
lexical token-overlap ranker as an offline orchestration baseline. It is not semantic
provider evidence and must not be used to select production chunk, frame, centroid,
summary, or similarity defaults. A caller-supplied summary experiment is not run because
this public fixture supplies no summary representation; that cell remains `not_available`,
not zero-filled.

A report generated on a local run is evidence for that exact checkout, fixture revision,
and runner fingerprint only. It does not prove LM Studio/OpenRouter quality, native visual
or acoustic similarity, private-corpus behavior, Windows behavior, capacity, or latency
outside the local run.
