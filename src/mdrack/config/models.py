"""Pydantic models for MDRack configuration sections."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PathsConfig(BaseModel):
    """File system paths configuration."""

    root: str = Field(default=".", description="Project root directory")
    store: str = Field(default=".mdrack", description="Knowledge store directory")
    config_file: str = Field(default=".mdrack/config.toml", description="Config file path")

    model_config = {"frozen": True}


class ScanConfig(BaseModel):
    """File scanning configuration."""

    include: list[str] = Field(default_factory=lambda: ["**/*.md"])
    exclude: list[str] = Field(
        default_factory=lambda: [
            "tests/**",
            "node_modules/**",
            ".git/**",
            ".venv/**",
        ]
    )

    model_config = {"frozen": True}


class ChunkingConfig(BaseModel):
    """Chunking parameters configuration."""

    min_chunk_chars: int = Field(default=1200, ge=1)
    target_chunk_chars: int = Field(default=3200, ge=1)
    hard_limit_chars: int = Field(default=8000, ge=1)
    overlap_chars: int = Field(default=300, ge=0)
    max_chunk_tokens: int = Field(default=2000, ge=1)
    code_window_lines: int = Field(default=80, ge=1)
    table_rows_per_chunk: int = Field(default=40, ge=1)
    mermaid_window_lines: int = Field(default=80, ge=1)

    @model_validator(mode="after")
    def validate_limits(self) -> "ChunkingConfig":
        if self.min_chunk_chars > self.target_chunk_chars:
            raise ValueError("min_chunk_chars cannot exceed target_chunk_chars")
        return self

    model_config = {"frozen": True}


class ParsingConfig(BaseModel):
    """Selectable Markdown parser backend for A/B compatibility."""

    backend: Literal["markdown_it", "legacy"] = Field(default="markdown_it")

    model_config = {"frozen": True}


MetadataProjectionMode = Literal[
    "store_only",
    "canonical_title",
    "facet",
    "facet_many",
    "lexical_text",
    "ignore",
]


class MetadataProjectionConfig(BaseModel):
    """One deterministic JSON Pointer projection rule."""

    path: str = Field(min_length=1)
    mode: MetadataProjectionMode
    namespace: str | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> "MetadataProjectionConfig":
        if not self.path.startswith("/"):
            raise ValueError("metadata projection path must start with '/'")
        index = 0
        while index < len(self.path):
            if self.path[index] != "~":
                index += 1
                continue
            if index + 1 >= len(self.path) or self.path[index + 1] not in {"0", "1"}:
                raise ValueError("metadata projection path contains an invalid JSON Pointer escape")
            index += 2
        needs_namespace = self.mode in {"facet", "facet_many"}
        if needs_namespace and not self.namespace:
            raise ValueError("facet projections require a namespace")
        if not needs_namespace and self.namespace is not None:
            raise ValueError("namespace is only valid for facet projections")
        return self

    model_config = {"frozen": True}


def _default_metadata_projections() -> list[MetadataProjectionConfig]:
    return [
        MetadataProjectionConfig(path="/title", mode="canonical_title"),
        MetadataProjectionConfig(path="/tags", mode="facet_many", namespace="tag"),
        MetadataProjectionConfig(path="/aliases", mode="lexical_text"),
    ]


class MetadataConfig(BaseModel):
    """Bounded normalization settings and explicit metadata projections."""

    max_serialized_bytes: int = Field(default=65_536, ge=1)
    max_depth: int = Field(default=8, ge=1)
    max_object_keys: int = Field(default=1_000, ge=1)
    max_array_items: int = Field(default=1_000, ge=1)
    max_string_bytes: int = Field(default=16_384, ge=1)
    invalid_policy: Literal["warn_and_continue", "fail_resource"] = "warn_and_continue"
    projections: list[MetadataProjectionConfig] = Field(default_factory=_default_metadata_projections)

    @model_validator(mode="after")
    def validate_unique_projection_paths(self) -> "MetadataConfig":
        paths = [item.path for item in self.projections]
        if len(paths) != len(set(paths)):
            raise ValueError("metadata projection paths must be unique")
        return self

    model_config = {"frozen": True}


class EmbeddingConfig(BaseModel):
    """Embedding provider configuration."""

    provider: Literal["lmstudio"] = Field(default="lmstudio")
    model: str = Field(default="qwen3-embedding-0.6b")
    endpoint: str = Field(default="http://localhost:1234/v1")
    timeout_secs: int = Field(default=120, ge=1)
    dimensions: int = Field(default=1024, gt=0)
    requested_dimensions: int | None = Field(default=None, gt=0)
    dimensions_capability: Literal[
        "tested", "not_installed", "unsupported", "not_tested"
    ] = Field(default="not_tested")
    runtime: str = Field(default="lmstudio-gui", min_length=1)
    model_family: str = Field(default="qwen3-embedding", min_length=1)
    quantization: str = Field(default="unknown", min_length=1)
    query_instruction: str = Field(default="Represent the query for retrieval", min_length=1)
    normalization_mode: str = Field(default="l2", min_length=1)
    endpoint_family: str = Field(default="openai_embeddings", min_length=1)
    instruction_profile: str = Field(default="retrieval-query-v1", min_length=1)
    profile_schema_version: int = Field(default=1, ge=1)

    model_config = {"frozen": True}


class SearchConfig(BaseModel):
    """Search parameters configuration."""

    default_mode: Literal["text", "semantic", "hybrid"] = Field(default="hybrid")
    text_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    semantic_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    top_k: int = Field(default=20, ge=1)
    rrf_k: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def validate_weights(self) -> "SearchConfig":
        if self.text_weight == 0.0 and self.semantic_weight == 0.0:
            raise ValueError("at least one search weight must be positive")
        return self

    model_config = {"frozen": True}


class ProfilingConfig(BaseModel):
    """Profiling configuration."""

    embedding_profiles: list[str] = Field(default_factory=lambda: ["default"])

    model_config = {"frozen": True}


class MDRackConfig(BaseModel):
    """Root configuration model containing all sections."""

    paths: PathsConfig = Field(default_factory=PathsConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    metadata: MetadataConfig = Field(default_factory=MetadataConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    profiling: ProfilingConfig = Field(default_factory=ProfilingConfig)

    model_config = {"frozen": True}
