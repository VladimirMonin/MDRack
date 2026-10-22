# Native temporal/media preparation v1

App `1.4.0rc3`; media distribution `1.0.0rc3`, media API `1.0.0-rc.2`.
This is the original M03-07 source/local-component family, not whole W5/W6 or
live-model/quality/release acceptance. Core, SQLite, schema0005 and their versions
are unchanged. See [ADR-0017](../decisions/0017-native-temporal-preparation.md).

## Separate inputs, owners and capabilities

- `frames`: app decodes selected actual video frames to PNG `ImageInput`; pure
  `NativeFrameObservation` / `build_native_frame_batch` produce image-modality
  frame units. Image capability is required; this is **not direct video**.
- `audio`: app decodes the audio track to mono PCM16/16kHz WAV `AudioInput`
  windows. `NativeAudioObservation` / `build_native_audio_batch` produce
  audio-modality time-segment units. No transcript is made, even for video audio.
- `video`: app produces visual-only MP4 `VideoInput` clips (MPEG-4, no audio).
  `NativeVideoObservation` / `build_native_video_batch` produce video-modality
  time-segment units. This requires explicit direct-video capability; image/frames
  support never grants it. Sound remains the independent audio route.

The media package owns immutable coordinates/ready observations, deterministic
IDs and pure graph assembly through the existing shared Core validator. It opens
no files, calls no providers and performs no persistence or ranking. Existing
transcript/frame-caption types still require genuine text.

The app owns caller/host source authority, bounded byte snapshots, CPU codec
processes, sampling/ranges, awaitable typed provider calls, canonical space/value
checks and existing W4 producer reconciliation/CAS. CLI only adapts this use case.
All native representation/unit text is `None`; filenames/base64/empty captions
are never searchable substitute content. Only actual ready vectors make these
units valid. No FTS words are manufactured.

## Engine and preparation contract

`await MDRackEngine.ingest_native_media(path, kind=..., resource_id=...,
source_namespace=..., source_ref=..., policy=NativeMediaPolicy(...))` prepares and
conditionally publishes a selected family. Resource IDs use the existing media
`resource_id(namespace, identity)` helper; CLI generates one unless explicitly
supplied. `path=None` requires an explicit `SourceResolver` host mapping. Catalog
locators are not file-opening authority. `expected_revision` checks the selected
raw SHA-256, not an old prepared-artifact content hash.

`NativeMediaPolicy` declares `[start_ms,end_ms)`, audio/video window and hop,
frame sampling step, maximum units, total source duration/bytes, total prepared
bytes, pixel bounds and process timeout. Invalid/excess ranges are refused, never
silently clamped. Overlap/gaps remain explicit in returned coordinates. Whole
source duration must satisfy the budget even for a short selected range.

The default decoder supports RIFF/WAVE and ISO-BMFF inputs with the required
actual track, using local CPU FFmpeg/ffprobe. Executables may be explicitly
selected by a `NativeMediaDecoder`; no runtime/settings installation occurs.
Decoder version and complete policy participate in the recipe. Frame selection
chooses the first actual decoded PTS at or after each grid point; integer-ms PTS
are floored. Non-zero stream origins, unsupported containers/tracks, missing
frames, excessive probe output or unsupported timelines fail closed. No exact
sub-ms seek claim is made.

Audio windows use sample-exact trim at 16 samples/ms and validate actual decoded
PCM coverage. Direct-video trim selects source frames within the window; its
start is the first selected actual PTS. MP4 playback duration may be frame-quantized;
provider limits check that actual encoded duration, while unit coordinates refer
to the original selected source interval. These are visual clip observations,
not ASR, complete video understanding or an audio-video joint encoder.

Every decoder process has bounded output/time; cancellation/failure kills and
awaits its own child before temporary cleanup. Tools see an owned snapshot, not
the changing caller path; no shell or HTTP protocol is used. Shared raw-source
bounded read/snapshot primitives are reused without changing legacy raw contracts.

Capabilities (input, role, candidate routes, byte/duration limits) are required
before incompatible work. All prepared units are validated before the first
encoder call; `EmbeddingResult` and exact canonical space are checked before SQL.
No fallback to text, ASR, another model, averaged frames or another space occurs.

## Publication, retention and repeat

The publication snapshot is captured before source I/O/decoding/provider awaits.
Provider/codec work holds no catalog transaction. Native purpose includes kind
and selected range, with explicit default variant; model/sampling recipe changes
replace that range's active output, not create accidental variants. Different
ranges/tracks remain distinct purposes and reuse existing W4 dependency/CAS rules.

Same raw material reuses its catalog source generation. Actual source transitions
get a new generation and conservatively prune dependent old outputs through the
accepted reconciler. Source bytes are rechecked before publication and immediately
before each CAS. Delete/recreate cannot grant an old operation a new lifecycle.
Independent metadata, facets, native tracks and prepared enrichment are preserved
by the shared merger, not a new native-specific SQL writer.

An exact matching retained input/recipe/space vector is reused without a provider
call. Repeat still decodes/checks source and proves publication postconditions;
it does not advertise zero CPU work. A CAS retry does not repeat encoding.
A conflict is returned as such, never as a successful publication. Temp snapshots
are cleaned; native preparation does **not** promise durable original media custody.
Existing W4 host artifact retention remains separate.

`NativeMediaResult.to_dict()` is a new `mdrack.native-media.v1` response containing
resource/kind, raw revision/duration, selected range and observed coordinates,
logical prepared unit IDs, recipe, publication status/attempts, actual provider
calls/input bytes and extraction calls0. Frames report sampled coverage, not 100%.
Unit IDs in a conflicted result identify prepared outputs, not committed rows.

## Search/read and CLI

`await engine.search_native_media(path, kind=..., policy=..., scope=...)` requires
**one** prepared frame/window. Multiple inputs are refused rather than averaged.
`search_media(MediaSearchRequest(TextInput(...)))` searches temporal candidates;
`StoredUnitInput` reuses an allowed retained frame/segment vector without a provider.
Candidate modality/namespace/representation filters remain independent of query
modality. An omitted modality scope selects only the current QUERY/SIMILARITY
role-and-input row's declared targets; explicit targets must be admitted by that
row. The effective Core scope preserves all other caller filters and applies the
admitted modalities before candidate limits. Unrelated capability rows or a shared
canonical space do not grant a route. Pure lexical mode requires no embedding route.
Existing Core grouping, family budgets, RRF and operation-owned read
sessions perform retrieval/hydration. Media hybrid without text is vector-only,
not a failed FTS branch. `engine.read_unit()` / `mdrack read unit` preserve null text
and original temporal locators. Old textual/similarity APIs retain their semantics.

Registered commands:

- `native ingest PATH --kind frames|audio|video --source-namespace NS --source-ref REF`
- `native search PATH --kind frames|audio|video --start-ms N --end-ms N`
- `native query --text TEXT` or `--unit-id ID`, independently choosing candidates/target
- `native capabilities` / `engine.native_media_capabilities()` (declarations, never a probe)

Ingest exposes range/window/hop/frame-step/unit/prepared-byte budgets and expected
revision. Search accepts candidate modality, target, result/candidate limits.
All use the configured collection and one standard JSON envelope; failures use
fixed safe codes/messages, not raw provider/codec/path exception strings.

Default `--provider lmstudio` retains the conclusive selected **text-only** embedding
API contract; native inputs fail before file/decoder/HTTP work. Explicit
`--provider synthetic` selects `SyntheticTemporalProvider`, a deterministic
byte-hash fixture with real local decode/SQLite/CLI but **no model or relevance
claim**. It is not a production fallback. No new HTTP/SDK/native payload is invented;
EmbeddingGemma-2 availability and unsupported native transport remain distinct.

## Verification boundary and remaining programme

The component is independently accepted on Windows/CPython3.14.2 in actual Root/local
and fresh noneditable installed-synthetic boundaries; W5-R1 is closed. Tests use owned
synthetic WAV/MP4, actual CPU decoding/files/SQLite, scripted typed providers, async
engine and registered CLI. Native/FTS preservation, coordinates,
repeat/reopen, real-connection CAS/lifecycle, budgets/invalid vectors and process
cleanup are component obligations. Fresh non-editable installed wheels and exact
45 exports/former40 prefix/closed import oracle are separate package gates.

This contract does not close original W5 M03-04 versioned real RU/EN/cross-language
media, held-out source groups, public A–D/optional-E runner, paired enrichment
quality or cost policy. Live audio/video transport needs its own evidence. Global
full/expanded-installed/ordinary-process/quality/release gates and W6 remain OPEN.
