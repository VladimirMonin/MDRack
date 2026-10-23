# Caller-owned native CUDA composition

This is an **opt-in outer host**, not MDRack's default embedding runtime. Python
never loads model weights or vendor DLLs. `CUDAEncoderProvider` spawns a separately
selected, hash-pinned caller executable and injects prepared vectors/space into the
existing `MDRackEngine`. SQLite/catalog/ranking/public reads are unchanged.

Only the `mdrack-cuda-abi12-v1` producer protocol is supported here: Windows x64,
PNG-only WIC CPU decode, fixed 224×224 8-bit RGB PNG, native mean pooling, 768 output,
double L2 to float32. Image documents and text queries for image candidates only.
No text-document, image-query/similarity, legacy-space compatibility, CPU-vector
fallback, OCR/caption substitution, HTTP image request or general quality/SLO claim.
A caller-supplied profile is an identity declaration, not a qualification certificate.
A new producer/runtime/recipe needs its own real qualification.

## Producer/source/build contract

The caller retains the matching producer C++ source, selected ABI headers/import
definitions and its qualified profile outside the repository. No executable,
proprietary runtime, weights, private profile or data is bundled in MDRack.
The ABI12 source uses genuine MSVC release `std::string&` as the third argument of
both vendor model load and context init; do not use upstream two-argument declarations,
NULL or guessed blobs. This supplement is specific to the pinned vendor runtime,
not a generic upstream ABI. Release x64 build recipe in a caller-owned build directory:

```bat
call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
lib /nologo /machine:x64 /def:llama.def /out:llama.lib
lib /nologo /machine:x64 /def:mtmd.def /out:mtmd.lib
lib /nologo /machine:x64 /def:ggml_llamacpp.def /out:ggml_llamacpp.lib
lib /nologo /machine:x64 /def:ggml-base.def /out:ggml-base.lib
cl /nologo /utf-8 /EHsc /std:c++17 /O2 /MD /DNDEBUG /D_ITERATOR_DEBUG_LEVEL=0 host.cpp /Fe:encoder.exe /link llama.lib mtmd.lib ggml_llamacpp.lib ggml-base.lib bcrypt.lib ole32.lib windowscodecs.lib
```

All paths above are generic placeholders; obtain source/headers under the caller's
own authority. Bind the actual compiler, binary/source, loaded CRT, runtime/weights,
selected GPU, decoder, query prefix and normalization in the producer profile. Retain
post-evaluation CUDA outputs in both backbone and vision/MTMD stages with device checks
and finite nonzero synchronous readback. Allocation logs alone do not qualify it.
The Python adapter preserves the executable's no-child policy and uses a private
kill-on-close job assigned **before resume**, retaining process/thread handles.

## Private configuration and normal commands

`configure.py --help` describes explicit inputs. For example, with caller-owned
qualified artifacts, an existing project directory and an existing private work
directory outside the checkout:

```text
uv run --offline --no-sync python examples/native_cuda/configure.py --profile C:/caller/qualified-profile.json --executable C:/caller/encoder.exe --source C:/caller/host.cpp --runtime-path C:/caller/runtime --vendor-path C:/caller/vendor --work-directory C:/caller/work --output C:/caller/native-host.json
mdrack --root C:/caller/project image native-ingest C:/caller/image.png --resource-id image-A --source-namespace caller --source-ref images/A.png --provider external --host-config C:/caller/native-host.json
mdrack --root C:/caller/project image native-search --text "a visual description" --mode semantic --candidates image --provider external --host-config C:/caller/native-host.json
```

The `image native-*` commands alone expose `--provider external --host-config`.
Other/default provider selection is unchanged. Host configuration is trusted local
composition, never indexed/untrusted resource metadata. It is drift-checked on each
call; only its explicit pinned files are read. Input bytes, not source paths/locators,
are passed through private files; no shell, source discovery, URL or tool fallback.
Temporary inputs, outputs, executable copy and CUDA cache are removed after reap,
including timeout/cancellation/refusal. stdout remains the normal single CLI envelope.

An optional `private_diagnostic_directory` config key selects an existing caller-owned
private directory for bounded producer stdout/stderr and native-exit/job receipts.
These **can contain sensitive native details/private paths**. They are NOT logs,
public support exports, Git artifacts or CLI output; no input/vector files are copied.
Default is no retention. Sink `OSError` increments `diagnostic_losses`; it cannot turn
a successful encoding into failure. Native exit alone is never vector admission.

## Embedded, enrichment and evaluator composition

```python
from pathlib import Path
from mdrack.hosts.cuda_encoder import CUDAEncoderProvider
from mdrack.public_api import MDRackEngine

provider = CUDAEncoderProvider(Path("C:/caller/native-host.json"))
engine = MDRackEngine(root=Path("C:/caller/project"), config=config,
                      prepared_embedding_provider=provider)
# await engine.ingest_native_image(...); await engine.search_media(...)
# Search evidence supplies the portable source locator/revision. read_unit(unit_id)
# verifies logical identity/whole_image but intentionally redacts source coordinates.
# Join those public values; only the caller's explicit allowlist grants original
# opening authority, followed by revision/hash and image-decode verification.
# engine.close(); await provider.aclose()
```

Use existing `prepared_enrichment_binding` and `enrich_prepared(..., kind="text",
lexical=True, dense=False)` for **honest manual** text on the SAME image resource.
The caller supplies a bound prepared-enrichment-v1 artifact; no extraction or native
encoding is requested. Native representation/vector/source revision must remain intact.
A subsequent semantic query is a separate encoding operation. Never imply that manual
text qualifies a text-document embedding role.

The existing native evaluator accepts no-argument provider factories. A caller-owned
factory can return `CUDAEncoderProvider(Path("C:/caller/native-host.json"))`; use it
with the evaluator's existing explicit factory arguments. Do not invent relevance
labels/thresholds or turn this two-image integration into independent RU/EN,
near-distractor/negative/held-out quality/cost/latency/SLO evidence.
