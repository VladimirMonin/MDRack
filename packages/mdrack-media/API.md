# mdrack-media API

The package root exposes the complete supported API. `__all__` is frozen by the
repository packaging tests.

## Records

- `TokenCount`: non-negative count, `exact|estimated`, and mandatory
  `TokenCounterFingerprint`.
- `TimedTextAtom`: one non-empty timed extraction observation.
- `TimedPassage`: deterministic grouper output with exact source-atom provenance.
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
validated policy records. `TokenCounter` is the caller-owned protocol for an exact
tokenizer or deterministic estimate and exposes its typed fingerprint.
`TranscriptBatchBuilderInput` and `FrameBatchBuilderInput` validate provider-free
builder inputs. `build_audio_transcript_batch()` and `build_video_transcript_batch()` project a
transcript through the timed grouper into a `mdrack_core.PreparedResourceBatch`;
they accept only
caller-supplied vectors, never reads media bytes, and persists no data itself.

The audio projection uses a `timed_passage` representation with `time_segment`
units and integer-millisecond `time_segment` locators. An explicit whole-resource
policy adds a `whole_resource` representation and unit for transcript-semantic
resource retrieval. Embedding spaces are shared by compatible fingerprint,
dimension, and metric identities so semantic retrieval can cross resources.
Changing producer, normalization, grouping, aggregation, or embedding fingerprints
changes the corresponding logical identities and requires a fresh replacement batch.
The video projection reuses the same representation, grouping, vector, and
whole-resource path while requiring a video resource and preserving
`track: "video"` in every timed seek locator; it never projects an audio resource
as video (or vice versa).

## Deterministic timed grouper

`group_timed_atoms()` accepts canonical `TimedTextAtom` values, a
`TimedChunkingPolicy`, a caller-owned `TokenCounter`, and an explicit exact or
estimated count kind. Empty input additionally requires explicit resource and
normalization identity. It returns `TimedGroupingResult`, exact `TimedPassage`
records and `GroupingMetrics`.

The frozen `deterministic-window-v1` rules are:

1. Caller order is strict and start times must be nondecreasing. Atoms are never
   sorted or repaired; ordinals, IDs, resource, producer, and normalization identity
   must agree.
2. Atom text is preserved byte-for-byte. The join inserts one ASCII space only when
   the previous text does not end in whitespace and the next text does not begin in
   whitespace. Existing spaces, line breaks, and Unicode are unchanged.
3. A boundary is safe only before the next atom when its start is at or after the
   maximum end seen in the candidate. Therefore overlapping input remains in one
   provenance component and output passage ranges do not overlap.
4. The candidate window begins when both soft minima are met and closes at the first
   soft maximum, next-atom hard-limit risk, or end of input. A closing condition is
   itself a candidate even below the soft minima.
5. Boundary weights are `+100` hard risk, `+45` speaker change, `+40` sentence end,
   `+35` strong pause, `+20` soft max, `+15` medium pause, `+15` line break, and
   `+10` for each reached token/duration target. Each axis subtracts
   `floor(10 * abs(value-target) / target)`.
6. Ties resolve by score descending, token distance ascending, duration distance
   ascending, then earliest boundary.
7. A single atom or overlap-connected component beyond a hard limit raises
   `TimedGroupingError("unsplittable_hard_limit")` by default. `unsplittable="flag"`
   preserves it with truthful passage metadata; no result silently claims compliance.
8. Passage IDs include resource, complete grouper fingerprint, ordinal, first/last
   atom IDs, exact range, and canonical joined-text digest. The fingerprint includes
   the algorithm, window, scores, tie order, join behavior, complete policy,
   token-counter identity/count kind, and unsplittable behavior.

`provisional_abc_policies()` exposes the roadmap A/B/C experiment cells.
`run_grouping_variants()` returns one `GroupingVariantResult` per policy with
coverage, overlap, hard-limit, token, duration, and boundary-reason metrics. This is
runner plumbing only and makes no retrieval-quality or default-policy claim.

Executable provider-free examples:

- [`examples/transcript_serialization.py`](examples/transcript_serialization.py)
- [`examples/frame_builder_serialization.py`](examples/frame_builder_serialization.py)

## Events

`MediaEvent` accepts only frozen event names and aggregate/fingerprint field names.
Unknown fields fail closed; arbitrary strings are redacted. Resource IDs, locators,
text, paths and source identities are not part of the event schema.
