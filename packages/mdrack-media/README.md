# mdrack-media

`mdrack-media` publishes immutable timed-text, transcript, frame-caption, locator,
policy, identifier, fingerprint, event and future-builder input contracts for MDRack.

Distribution version `1.0.0rc1` publishes media contract version `1.0.0-rc.1`.
Python 3.11 or newer is required. The only runtime dependency is
`mdrack-core==1.0.0rc1`.

## Boundary

The package validates caller-prepared values and projects typed millisecond locators
to generic `mdrack_core.Locator` records. It does not read files, access a database
or network, call providers, tokenize text, group transcript atoms, create
embeddings, or execute builders. Source identity is accepted only by deterministic
ID helpers and is never admitted to media event fields.

Token counts always retain `exact` or `estimated` truthfulness together with the
fingerprint of the counter. Producer, normalization, grouper, token-counter,
aggregation and embedding fingerprints use distinct runtime types so they cannot be
silently interchanged.

## Provider-free examples

- [Transcript and transcript-builder serialization](examples/transcript_serialization.py)
- [Frame artifact and frame-builder serialization](examples/frame_builder_serialization.py)

Both examples construct deterministic identities, serialize with `to_dict()`, and
round-trip with `from_dict()`. They require no media file, provider, database, or
network access.

See [API.md](API.md) for the frozen surface and [CHANGELOG.md](CHANGELOG.md) for
release notes.
