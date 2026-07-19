# W5-B13 SQLite operating envelope

- Contract: `mdrack.sqlite-envelope-v1`
- Evidence boundary: `local components` (disposable synthetic SQLite catalogs)
- Revision: `0f8c6b2b78d6cf323f7d71c784db70ed9c8f4105`
- Host: Linux `6.17.0-35-generic`, x86_64, Python `3.11.15`
- Harness: `scripts/sqlite_envelope_benchmark.py`
- Harness SHA-256: `c4e7a565abc610454c2ae1d368d0ab2dd601832bb712a4baca65855cf75cd4eb`
- Configuration: one warm-up, five repetitions, candidate limit 100, cosine vector scan
- Privacy: generated identifiers and vectors only; zero network attempts; temporary catalogs removed

The JSON companion (`w5-sqlite-envelope.json`) is the machine-readable report. Each
latency, CPU, RSS, candidate, and fusion value is reported as p50/p95/p99. Fusion is
the deterministic provider-neutral top-k merge over returned candidates; it is not an
alternative backend implementation or a relevance-quality claim.

## Measured cells

Times are milliseconds; RSS is KiB. Triples are p50/p95/p99.

| Units | Dimensions | DB bytes | Wall ms | CPU ms | RSS KiB | Candidates | Fusion ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 384 | 2,768,896 | 280.4/290.2/291.1 | 280.4/289.8/290.8 | 27,960/28,034/28,048 | 100 | 0.037/0.071/0.075 |
| 10,000 | 384 | 24,485,888 | 2,750.5/2,803.3/2,811.4 | 2,750.2/2,801.7/2,809.7 | 51,596/51,650/51,658 | 100 | 0.088/0.116/0.119 |
| 1,000 | 768 | 4,825,088 | 539.8/560.0/561.0 | 539.8/560.0/561.0 | 29,544/29,620/29,630 | 100 | 0.055/0.067/0.069 |
| 10,000 | 768 | 45,010,944 | 5,352.8/5,424.5/5,425.8 | 5,347.9/5,422.8/5,424.3 | 66,576/66,848/66,896 | 100 | 0.085/0.087/0.088 |
| 1,000 | 1,024 | 5,328,896 | 711.5/742.7/745.5 | 710.8/742.7/745.5 | 29,372/29,546/29,573 | 100 | 0.037/0.049/0.052 |
| 10,000 | 1,024 | 50,053,120 | 7,082.1/7,165.9/7,166.8 | 7,081.3/7,163.8/7,164.7 | 76,648/76,794/76,818 | 100 | 0.072/0.104/0.105 |

## Operating envelope and thresholds

The measured clean envelope is **up to 10,000 units and 1,024 dimensions** for
interactive local use under this harness, with a conservative warning threshold of
p95 wall time above 6,000 ms or p95 RSS above 75,000 KiB. A cell is blocked from
support claims when p95 exceeds either threshold; it is not an automatic backend
selection signal. The 10k/1024 cell exceeds the latency threshold and is therefore
`bounded/slow`, not a portable SLA.

The 50k and 100k cells in all three dimensions are supported by the harness CLI and
remain **unmeasured in this revision**. They require an explicitly budgeted run because
fixture construction and JSON vector storage are linear in both units and dimensions.
No incomplete cell selects SQLite, PostgreSQL, ANN, or another backend.

## Reproduction

```bash
uv run python scripts/sqlite_envelope_benchmark.py \
  --cells 1000x384,10000x384,1000x768,10000x768,1000x1024,10000x1024 \
  --warmups 1 --repetitions 5 \
  --output docs/evaluation/w5-sqlite-envelope.json
```

The full supported matrix is available with the script's default `--cells` value;
run it only with a separately budgeted local disposable workspace. No active store,
private corpus, network, provider, or alternative backend is touched.

## Non-claims

This is local-components capacity evidence, not installed-package, real-source,
Windows, live-provider, or relevance-quality evidence. It does not claim a universal
SQLite capacity SLA, ANN suitability, PostgreSQL suitability, or a production backend
migration decision. See [ADR-0004](../decisions/0004-sqlite-operating-envelope.md).
