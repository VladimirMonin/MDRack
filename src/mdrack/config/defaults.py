"""Default configuration values for MDRack."""

from __future__ import annotations

from mdrack.config.models import (
    ChunkingConfig,
    EmbeddingConfig,
    MDRackConfig,
    ParsingConfig,
    PathsConfig,
    ProfilingConfig,
    ScanConfig,
    SearchConfig,
)

DEFAULT_PATHS = PathsConfig()
DEFAULT_SCAN = ScanConfig()
DEFAULT_PARSING = ParsingConfig()
DEFAULT_CHUNKING = ChunkingConfig()
DEFAULT_EMBEDDING = EmbeddingConfig()
DEFAULT_SEARCH = SearchConfig()
DEFAULT_PROFILING = ProfilingConfig()

DEFAULT_CONFIG = MDRackConfig(
    paths=DEFAULT_PATHS,
    scan=DEFAULT_SCAN,
    parsing=DEFAULT_PARSING,
    chunking=DEFAULT_CHUNKING,
    embedding=DEFAULT_EMBEDDING,
    search=DEFAULT_SEARCH,
    profiling=DEFAULT_PROFILING,
)


def get_defaults() -> MDRackConfig:
    """Return a fresh copy of the default configuration."""
    return MDRackConfig.model_validate(DEFAULT_CONFIG.model_dump())
