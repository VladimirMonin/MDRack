"""Pydantic models for MDRack configuration sections."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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

    model_config = {"frozen": True}


class ParsingConfig(BaseModel):
    """Selectable Markdown parser backend for A/B compatibility."""

    backend: Literal["markdown_it", "legacy"] = Field(default="markdown_it")

    model_config = {"frozen": True}


class EmbeddingConfig(BaseModel):
    """Embedding provider configuration."""

    provider: Literal["lmstudio"] = Field(default="lmstudio")
    model: str = Field(default="qwen3-embedding-0.6b")
    endpoint: str = Field(default="http://localhost:1234/v1")
    timeout_secs: int = Field(default=120, ge=1)
    dimensions: int = Field(default=1024, gt=0)
    runtime: str = Field(default="lmstudio-gui", min_length=1)
    model_family: str = Field(default="qwen3-embedding", min_length=1)
    quantization: str = Field(default="unknown", min_length=1)
    query_instruction: str = Field(default="Represent the query for retrieval", min_length=1)
    normalization_mode: str = Field(default="l2", min_length=1)
    endpoint_family: str = Field(default="openai_embeddings", min_length=1)

    model_config = {"frozen": True}


class SearchConfig(BaseModel):
    """Search parameters configuration."""

    default_mode: Literal["text", "semantic", "hybrid"] = Field(default="hybrid")
    text_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    semantic_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    top_k: int = Field(default=20, ge=1)
    rrf_k: int = Field(default=60, ge=1)

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
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    profiling: ProfilingConfig = Field(default_factory=ProfilingConfig)

    model_config = {"frozen": True}
