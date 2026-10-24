# Opt-in native text/image composition, 2026-10-24

## Independently accepted bounded result

The explicit `mdrack-cuda-retrieval-v2` composition is independently accepted for
short text DOCUMENT, text QUERY over text/image candidates, and fixed RGB PNG
DOCUMENT inputs. The accepted path uses the real `CUDAEncoderProvider`, normal
`MDRackEngine`, SQLite catalog/import/search/read/reopen and a source-free,
noneditable installation, including its ordinary Windows console launcher.
Default LM Studio HTTP, legacy A and `mdrack-cuda-abi12-v1` remain unchanged.
This closes the named native B text-DOCUMENT functional/composition blocker;
it does not complete a target-corpus A–D experiment or qualify optional dense E.

[ADR-0018](../decisions/0018-caller-owned-native-encoder.md), the
[contract](../contracts/native-image-v1.md) and
[caller recipe](../../examples/native_cuda/README.md) own the current behavior.
The [earlier image product](native-image-product-2026-10-23.md) and
[HTTP A result](text-provider-functional-2026-10-24.md) retain their original cutoffs.
Native CUDA evidence is not transferred to the HTTP text-kernel device, which
remains unknown in that separate qualification.

## Role and identity boundary

| Direction | Qualified opt-in v2 declaration |
|---|---|
| DOCUMENT image → image | Noninterlaced 224×224, 8-bit RGB PNG, at most 1 MiB |
| DOCUMENT text → text | UTF-8 body with `title: none \| text: ` added once |
| QUERY text → image/text | One capability row, `task: search result \| query: ` added once |

The body is unchanged; a filename or caller identity is not used as model text.
The formatted text wire is at most 65,536 bytes and native tokenization must fit
the complete 2,048-token context, including prefix/special tokens. The byte ceiling
is not a long-context guarantee; excess tokens are refused before forward, not
truncated. Image QUERY, SIMILARITY, audio/video inference and document-vector reuse
are not added. The old protocol keeps its image-document/text-query-for-image roles.

The full space binds producer/source, qualified runtime/weights/recipe, role
formatting, decoder, pooling and normalization/codec. Equal 768 dimensions are not
space compatibility; existing catalogs/vectors are not silently rekeyed.

| Binding | SHA-256 / fingerprint |
|---|---|
| Native producer source | `32387c6aaba6ff62657f6ccf2c2c158396b4a90d659a3d1ece94bf5c52a09a05` |
| Native producer executable | `5b4439a1ce3eaa3a97819c8d13b08938598af2cbd84ea81ea691fadd4bf58414` |
| Encoder-profile fingerprint | `653db4b5cba61fb68541953fa2fff29607734eb0e336d7216e803cde94e03443` |
| Complete embedding-space fingerprint | `267123e9bdf0b13092e86b3a3cfab6282b923adc3edae1c3755c39c42f80d125` |

These identifiers are evidence bindings, not a redistributable producer/configuration
or a global provider registration. Producer source/binary, models, vendor files,
private configuration and caller factories are not delivered in the repository.

## Fresh independent checks

| Boundary | Actual result |
|---|---|
| Current Root canonical suite | 2,449 collected; 2,443 PASS, six historical SKIP, zero FAIL/deselection/xfail |
| Original affected checks | 149 PASS: 145 overlap the full suite, four additional caller-lifecycle checks |
| Strict typing / lint / boundaries | 216 source files; Ruff and all four boundary scripts PASS |
| Fresh isolated offline builds/install | Four PEP 517 wheels; 236/236 payloads equal Root, frozen candidates and fresh noneditable installation |
| Installed origins and factories | 149 actual module origins pinned to installed packages; two real factory constructions/closures without model calls |
| New native window | 14 dispatch cells / 16 conservative units, 12 individual read-only controls, one bounded monitor, zero retries |
| Actual learned forwards | Three text DOCUMENT, five text QUERY and two image DOCUMENT forwards |
| Evaluated CUDA observations | 96 sampled post-evaluation operation outputs: 80 backbone, 16 vision |
| Fresh numeric witness | All ten actual raw/f32 pairs independently reconstructed as raw → double-L2 → f32, matching production admission |
| Host refusals | Unknown role, invalid UTF-8, excess wire bytes and excess tokens: four expected native exit-2 refusals, no forward/vector |

The full Root suite consumes the pre-existing dirty tracked skill through its
unchanged original canonical fixture tests. This is not an assertion of identical
committed-HEAD fixture bytes; unrelated skill/reference work is excluded from delivery.
Ten unchanged catalog-upgrade launcher assertions use the previously qualified launcher;
the new v2 SDK/PE composition is independently exercised in the fresh installation.
Source/callable checks and ordinary PE startup remain different boundaries: the
normal launcher is not described as Python `-S` execution or source injection.

Each positive native call has operation/role/formatted-input/profile/PID bindings,
finite nonzero evaluated CUDA readback, finite 768d output and exact production
float32 admission. The document repeat and the ordinary-PE/fresh-reopen query repeat
match byte-for-byte. The two artificial images retain the existing changed-image
and correct-versus-cross discriminator controls. Actual public-evidence joins lead
to allowlisted original opens/hash/independent PNG decode; native CPU WIC bitmap
bytes match those independently decoded bytes.

B uses complete public-manifest import and normal queries/read/reopen. Ordinary
DOCUMENT C delegates through that same existing import primitive. D enriches the
same image resource/revision with real prepared lexical text: encoding delta is
zero and native representations/vectors/space remain intact. Its subsequent fresh
hybrid QUERY encodes once and returns both image and text evidence on that same
resource/revision, not a separate proxy resource.

At most eight evaluated MUL_MAT outputs per claimed stage/process are sampled,
with synchronous eight-float finite/nonzero readback. This is not all-operator GPU
coverage. WIC decoding, tokenization and host double-L2 remain CPU work. The monitor's
136 resource frames are observations within one window, not independent model trials.

## Lifecycle and retained history

The new 30-minute window starts before its first actual read-only control and is
closed after all 14 cells. Every attempted cell reserves its units before dispatch;
no retry or closed-budget reuse occurred. Owned processes are suspended and assigned
to Jobs before resume. Retained original handles, creation identity and awaited exits
establish release, rather than a bare-PID absence check. Native work directories are
empty; contexts, catalogs, helpers and observer handles are closed. Application model
roster, inventory and server state are unchanged; no app lease/unload was used.

Earlier failed aggregate release checks remain failed historical records: a serialized
documentation/index advance did not change frozen source/runtime/input bytes, and
numeric PID reuse was separately distinguished from an owned live process. The new
window does not rewrite those outcomes. Earlier private-snapshot fixture/checkout
failures and reviewer harness setup failures are retained too. Only private harness
provisioning/verifiers were corrected, followed by genuine applicable reruns; product
code, original assertions, evaluator, gold and policy were not repaired by review.

## Data campaigns and required remainder

The [reviewed caption pilot](native-corpus-review-2026-10-24.md) and
[paired-query companion](paired-query-companion-review-2026-10-24.md) are separate
bounded DATA acceptances. Both complete 52-case families remain required, including
the harder original cases. Their 54-case union is only an overlap map; changing
assistance never permits copying model rankings for shared cases.

No query, target or scoring call from either private corpus entered this native
window. Corpus relevance, robust multilingual/negative-abstention fitness, actual
owning text/media/OCR-ASR obligations, cost/latency/SLO, W6/operations and release
remain open in their declared scope. One held-out matched-intent data group is not
broad multilingual model evidence. Catalog scaling remains MEASUREMENTS_ONLY.
The existing A–D/optional-E matrix, evaluability/coverage checks and policy are not
relaxed. Actual target-campaign provider/profile/space/runner-plan identifiers remain
unbound until its separately named frozen-input, qualified and freshly authorized
phase. Functional v2 acceptance neither authorizes that phase nor renews any budget.
