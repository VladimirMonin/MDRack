# mdrack-media

`mdrack-media` publishes immutable timed-text, transcript, frame-caption, locator,
policy, identifier, fingerprint, event and future-builder input contracts for MDRack.

Distribution version `1.0.0rc1` publishes media contract version `1.0.0-rc.1`.
Python 3.11 or newer is required. The only runtime dependency is
`mdrack-core==1.0.0rc1`.

## Boundary

The package validates caller-prepared values, groups timed atoms with a caller-owned
token counter, projects typed millisecond locators to generic
`mdrack_core.Locator` records, and provides provider-free audio and video transcript
batch projections. The builders do not read files, access a database or network,
call providers, load a tokenizer, or create embeddings; vectors remain caller-owned.
Source
identity is accepted only by deterministic ID helpers and is never admitted to
media event fields.

Token counts always retain `exact` or `estimated` truthfulness together with the
fingerprint of the counter. Producer, normalization, grouper, token-counter,
aggregation and embedding fingerprints use distinct runtime types so they cannot be
silently interchanged.

`group_timed_atoms()` is strict: it never sorts, repairs, normalizes, truncates, or
adds retrieval overlap. Input atom overlap remains in exact source provenance;
boundaries cannot split an overlap-connected component, so emitted passage ranges
never overlap. A component that cannot satisfy a hard limit is rejected by default
or returned only with an explicit `unsplittable`/`hard_limit_exceeded` flag.

`provisional_abc_policies()` and `run_grouping_variants()` provide deterministic
experiment plumbing and aggregate structural metrics. A/B/C are not an optimized
or selected default; policy selection remains deferred to retrieval evaluation.

## Provider-free examples

- [Transcript and transcript-builder serialization](examples/transcript_serialization.py)
- [Frame artifact and frame-builder serialization](examples/frame_builder_serialization.py)

Both examples construct deterministic identities, serialize with `to_dict()`, and
round-trip with `from_dict()`. They require no media file, provider, database, or
network access.

See [API.md](API.md) for the frozen surface and [CHANGELOG.md](CHANGELOG.md) for
release notes.
