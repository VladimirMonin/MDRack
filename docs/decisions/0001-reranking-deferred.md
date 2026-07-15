# ADR-0001: Defer production reranking from MDRack v0.2

- **Status:** Accepted
- **Date:** 2026-07-16
- **Decision owners:** MDRack maintainers
- **Applies to:** MDRack v0.2

## Context

MDRack v0.2 targets stable indexing, provenance, assets, an embedded Python API,
and hybrid retrieval based on full-text and semantic candidates fused with
Reciprocal Rank Fusion (RRF).

A local `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` artifact is discoverable and
loadable through LM Studio. Its public model card identifies it as a 0.6B Q8_0
GGUF of 639 MB converted from `Qwen/Qwen3-Reranker-0.6B`. Installation and model
loading do not prove that a compatible reranking transport exists or that scores
are semantically correct.

LM Studio's documented REST and OpenAI-compatible APIs expose model management,
chat/completions, responses, and embeddings, but no documented reranking
endpoint. MDRack therefore has no supported LM Studio production transport for
this model.

Qwen3-Reranker is not a conventional embedding model with rank/classification
pooling. The official model computes relevance from the logits of the `yes` and
`no` tokens. An HTTP 200 response from a different pooling contract is not
sufficient evidence of correct reranking.

Upstream llama.cpp history is also not a v0.2 production contract:

- PR `#14029` is closed and was superseded by later work.
- PR `#15824` added Qwen3-Reranker support and was merged on 2025-09-25.
- Issue `#16407` documented near-zero or otherwise incorrect rerank scores and is
  now closed as completed.

These upstream changes correct an earlier assumption that the relevant support
PR was still unmerged. MDRack still does not adopt a separate `llama-server`
because the exact runtime, converted artifact, prompt template, score semantics,
and quality gate have not been validated for this project. Operating a second
runtime is also outside the v0.2 release boundary.

## Decision

Production reranking is **deferred** and treated as a separate integration task
blocked on a documented, semantically validated runtime contract.

The supported v0.2 retrieval path is:

```text
FTS candidates + semantic candidates -> deterministic RRF -> final results
```

Absence of reranking is a normal documented operating mode. It is not an error
and does not make an RRF result incomplete. In this mode:

- `rerank_rank` is `null`;
- `rerank_score` is `null`;
- no reranker model is invoked by the CLI or embedded engine;
- no chat/completions emulation is permitted.

MDRack retains only the future integration seam and offline proof machinery:

- `RerankerProvider`;
- `RerankDocument` and `RerankScore`;
- `DeterministicReranker`, explicitly documented as a test adapter;
- offline tests for reordering, malformed responses, and fail-open behavior;
- nullable reranker fields in result DTOs.

MDRack v0.2 will not add:

- `LMStudioRerankerProvider`;
- `LlamaCppRerankerProvider`;
- a `--rerank` CLI option;
- reranker endpoint/model configuration;
- automatic startup of a second model runtime;
- reranking through `chat/completions`.

The project must not say that the Qwen3 reranker is missing or not installed.
The accurate statement is: the model was discovered and loaded, but no supported
LM Studio reranking API was available to MDRack.

## Consequences

### Positive

- v0.2 is not blocked by one unavailable LM Studio endpoint.
- The release makes no unsupported claim about production reranking.
- RRF remains deterministic, testable, and usable offline.
- The future adapter boundary remains available without speculative runtime code.

### Negative

- v0.2 does not provide a production reranking stage.
- Reranking quality and latency are not part of v0.2 acceptance.
- A future integration must independently validate both transport and semantic
  ranking quality; endpoint reachability alone is insufficient.

## Re-entry criteria

Production reranking may be reconsidered only when all of the following exist:

1. a documented runtime endpoint and request/response schema;
2. a validated adapter for the exact runtime and model artifact;
3. semantic tests proving meaningful ordering rather than merely HTTP success;
4. measurable nDCG/MRR improvement over the RRF baseline;
5. privacy-safe telemetry and fail-open behavior;
6. an explicit decision about lifecycle management for any second runtime.

## Sources

- [LM Studio REST API endpoints](https://lmstudio.ai/docs/developer/rest/endpoints)
- [LM Studio OpenAI compatibility endpoints](https://lmstudio.ai/docs/developer/openai-compat)
- [Qwen3-Reranker-0.6B model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)
- [ggml-org Q8_0 GGUF card](https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF)
- [llama.cpp PR #14029](https://github.com/ggml-org/llama.cpp/pull/14029)
- [llama.cpp PR #15824](https://github.com/ggml-org/llama.cpp/pull/15824)
- [llama.cpp issue #16407](https://github.com/ggml-org/llama.cpp/issues/16407)
