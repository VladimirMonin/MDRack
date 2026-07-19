# mdrack-media API

The package root exposes the complete supported API. `__all__` is frozen by the
repository packaging tests.

## Records

- `TokenCount`: non-negative count, `exact|estimated`, and mandatory
  `TokenCounterFingerprint`.
- `TimedTextAtom`: one non-empty timed extraction observation.
- `TimedPassage`: a future grouper output with exact source-atom provenance.
- `TranscriptArtifact`: zero or more transcript atoms under one resource and
  representation.
- `FrameCaptionObservation` and `FrameCaptionArtifact`: timed textual frame evidence.

All intervals use integer milliseconds and `[start_ms,end_ms)` semantics. Text is
validated but never normalized or truncated.

Transcript atoms are strict canonical input: ordinals must be contiguous from zero,
caller order must follow the timeline, and intervals must not overlap. The package
does not reorder or merge atoms. When `duration_ms` is present it must include the
end of every atom.

## Locators

`TimeSegmentLocator`, `VideoFrameLocator` and `WholeMediaLocator` serialize to
generic `mdrack_core.Locator` values. They contain no path or provider behavior.

## Identity and fingerprints

Deterministic helpers publish resource, representation, atom, passage, frame and
whole-resource IDs as kind-prefixed SHA-256 values. Inputs are framed through
canonical JSON. IDs are stable identifiers, not claims that low-entropy inputs are
secret.

Canonical JSON validates the complete recursive JSON grammar before serialization;
object keys must be strings and numbers finite. Containers must be acyclic and may
be nested at most 64 levels, counting the outermost object or array as level one.
Cycles and deeper trees fail with the same generic `ValueError` before hashing. Atom
and frame IDs are recomputed from their record fields. Transcript, frame-caption and
timed-passage representation kinds are explicit and their IDs are validated against
resource, producer/policy and normalization identity.

Separate fingerprint types cover producer, normalization, grouper, token counter,
aggregation and embedding identity. `from_payload()` uses canonical JSON and
SHA-256.

## Policies and builder inputs

`TimedChunkingPolicy`, `TextNormalizationPolicy` and `WholeResourceTextPolicy` are
validated policy records only. `TokenCounter` is the caller-owned protocol for an
exact tokenizer or deterministic estimate and exposes its typed fingerprint.
`TranscriptBatchBuilderInput` and
`FrameBatchBuilderInput` validate future builder inputs but perform no grouping,
projection, embedding or persistence.

Executable provider-free examples:

- [`examples/transcript_serialization.py`](examples/transcript_serialization.py)
- [`examples/frame_builder_serialization.py`](examples/frame_builder_serialization.py)

## Events

`MediaEvent` accepts only frozen event names and aggregate/fingerprint field names.
Unknown fields fail closed; arbitrary strings are redacted. Resource IDs, locators,
text, paths and source identities are not part of the event schema.
