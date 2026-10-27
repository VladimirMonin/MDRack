"""Shared, provider-free admission before changing the app's textual profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mdrack.adapters.sqlite.canonical_catalog import ApplicationSearchReadSessions, ApplicationStoreError
from mdrack.application.compatibility import assert_text_embedding_profile_compatible
from mdrack.embeddings.runtime import create_embedding_provider, embedding_profile_from_config
from mdrack.ports.storage import IndexStorage


def preflight_text_embedding_profile(
    root: Path,
    config: Any,
    *,
    provider: object | None = None,
    provider_name: str | None = None,
    profile_name: str = "default",
    storage: IndexStorage | None = None,
) -> bool:
    """Refuse an unproven replacement; return whether active TEXT bindings exist.

    No source enumeration, provider dispatch or catalog creation occurs here.
    CLI callers can check a configured target before constructing external edges;
    the service checks the actual injected provider/storage before starting a run.
    An injected storage must expose authoritative metadata, never a disk fallback.
    """
    identity_provider = provider
    if provider_name == "fake":
        # Reuse the existing offline provider's identity, not a second fake recipe.
        identity_provider = create_embedding_provider("fake", config)
    elif provider_name not in {None, "lmstudio"}:
        raise ValueError("unsupported_embedding_provider")
    profile = embedding_profile_from_config(config, identity_provider, profile_name)
    if storage is not None:
        return assert_text_embedding_profile_compatible(storage, profile)
    try:
        with ApplicationSearchReadSessions(root.resolve(), config).open() as session:
            return assert_text_embedding_profile_compatible(session.storage, profile)
    except ApplicationStoreError as exc:
        # Only the lifecycle owner's verified missing-catalog state is unbound.
        # A legacy/mixed/damaged topology must not dispatch a model first.
        if str(exc) == "catalog_missing":
            return False
        raise
