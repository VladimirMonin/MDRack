"""Configuration loader with precedence: Defaults → TOML → Env → CLI overrides."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import toml
from pydantic import BaseModel

from mdrack.config.defaults import get_defaults
from mdrack.config.models import (
    ChunkingConfig,
    EmbeddingConfig,
    MDRackConfig,
    PathsConfig,
    ProfilingConfig,
    ScanConfig,
    SearchConfig,
)

logger = logging.getLogger(__name__)

_ENV_PREFIX = "MDRACK_"
_SECTION_MAP: dict[str, type[BaseModel]] = {
    "paths": PathsConfig,
    "scan": ScanConfig,
    "chunking": ChunkingConfig,
    "embedding": EmbeddingConfig,
    "search": SearchConfig,
    "profiling": ProfilingConfig,
}


def _read_toml(path: Path) -> dict[str, Any]:
    """Read and parse a TOML file, return raw dict."""
    if not path.is_file():
        logger.debug("TOML config file not found: %s", path)
        return {}
    logger.debug("Reading TOML config from %s", path)
    with open(path, encoding="utf-8") as f:
        return toml.load(f)  # type: ignore[no-any-return]


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Overlay environment variables (MDRACK_SECTION_FIELD) onto raw dict.

    Supported formats:
        MDRACK_CHUNKING_MIN_CHUNK_CHARS=800
        MDRACK_EMBEDDING_DIMENSIONS=1024
    """
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        rest = key[len(_ENV_PREFIX) :]
        parts = rest.split("_", maxsplit=1)
        if len(parts) != 2:
            continue
        section_name, field_name = parts[0].lower(), parts[1].lower()
        if section_name not in _SECTION_MAP:
            continue
        section_raw = raw.setdefault(section_name, {})
        if not isinstance(section_raw, dict):
            section_raw = {}
            raw[section_name] = section_raw
        # Attempt type coercion based on model field hints
        coerced = _coerce_env_value(section_name, field_name, value)
        # If the existing value is a list and the raw string contains no commas,
        # treat it as a single-element list override
        existing = section_raw.get(field_name)
        if isinstance(existing, list) and isinstance(coerced, str) and "," not in coerced:
            coerced = [coerced]
        section_raw[field_name] = coerced
        logger.debug(
            "Env override applied: %s -> %s.%s = %r",
            key,
            section_name,
            field_name,
            coerced,
        )
    return raw


def _coerce_env_value(section: str, field: str, raw_value: str) -> Any:
    """Coerce an environment variable string to the appropriate Python type."""
    model_cls = _SECTION_MAP.get(section)
    if model_cls is None:
        return raw_value
    fields = model_cls.model_fields
    if field not in fields:
        return raw_value
    field_info = fields[field]
    annotation = field_info.annotation
    # Handle Literal types (e.g. Literal["text", "semantic", "hybrid"])
    if hasattr(annotation, "__origin__"):
        return raw_value
    if annotation is bool:
        return raw_value.lower() in ("1", "true", "yes")
    if annotation is int:
        try:
            return int(raw_value)
        except ValueError:
            return raw_value
    if annotation is float:
        try:
            return float(raw_value)
        except ValueError:
            return raw_value
    return raw_value


def _merge_section(
    defaults: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge two dicts; overrides take precedence."""
    merged = dict(defaults)
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_section(merged[key], value)
        else:
            merged[key] = value
    return merged


def _build_config(raw: dict[str, Any]) -> MDRackConfig:
    """Build MDRackConfig from a flat dict of sections."""
    return MDRackConfig(
        paths=PathsConfig(**raw.get("paths", {})),  # type: ignore[arg-type]
        scan=ScanConfig(**raw.get("scan", {})),  # type: ignore[arg-type]
        chunking=ChunkingConfig(**raw.get("chunking", {})),  # type: ignore[arg-type]
        embedding=EmbeddingConfig(**raw.get("embedding", {})),  # type: ignore[arg-type]
        search=SearchConfig(**raw.get("search", {})),  # type: ignore[arg-type]
        profiling=ProfilingConfig(**raw.get("profiling", {})),  # type: ignore[arg-type]
    )


def resolve_config_path(root: Path | None = None, toml_path: Path | None = None) -> Path:
    """Resolve the effective TOML config path for a project root."""
    defaults = get_defaults().model_dump()
    resolved_root = root.resolve() if root is not None else None
    candidate = toml_path or Path(defaults["paths"]["config_file"])
    if resolved_root is not None and not candidate.is_absolute():
        candidate = resolved_root / candidate
    return candidate


def write_config(config: MDRackConfig, toml_path: Path) -> None:
    """Persist config to TOML using an atomic replace in the target directory."""
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = toml_path.with_suffix(f"{toml_path.suffix}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        toml.dump(config.model_dump(mode="python"), handle)
    tmp_path.replace(toml_path)


def load_config(
    toml_path: Path | None = None,
    root: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> MDRackConfig:
    """Load configuration with precedence:
    Defaults → TOML file → Environment variables → CLI overrides.

    Args:
        toml_path: Path to a TOML config file. If None, uses default location.
        root: Project root used to resolve relative config paths.
        cli_overrides: Flat dict of CLI overrides keyed by "section.field".

    Returns:
        Merged MDRackConfig instance.
    """
    defaults = get_defaults().model_dump()

    # Layer 1: TOML file
    toml_path = resolve_config_path(root=root, toml_path=toml_path)
    toml_raw = _read_toml(toml_path)

    merged = _merge_section(defaults, toml_raw)

    # Layer 2: Environment variables
    merged = _apply_env_overrides(merged)

    # Layer 3: CLI overrides
    if cli_overrides:
        for dotted_key, value in cli_overrides.items():
            parts = dotted_key.split(".", maxsplit=1)
            if len(parts) == 2:
                section, field = parts
                section_raw = merged.setdefault(section, {})
                if isinstance(section_raw, dict):
                    section_raw[field] = value
                    logger.debug("CLI override: %s.%s = %r", section, field, value)

    return _build_config(merged)
