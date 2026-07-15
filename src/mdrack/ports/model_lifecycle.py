"""Model lifecycle port kept separate from catalog discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LoadedModel:
    key: str
    instance_id: str | None
    state: str | None = None


@runtime_checkable
class ModelLifecycleProvider(Protocol):
    async def list_loaded_models(self) -> list[LoadedModel]: ...

    async def load_model(self, model_key: str) -> LoadedModel: ...

    async def unload_model(self, instance_id: str) -> None: ...
