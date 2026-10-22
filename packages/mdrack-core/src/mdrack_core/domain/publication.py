"""Neutral conditional publication records; tokens are local opaque authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .batches import PreparedResourceBatch
from .common import require_non_empty
from .resources import Locator


@dataclass(frozen=True)
class PublicationState:
    resource_id: str
    status: Literal["never_created", "published", "live_without_projections", "deleted"]
    lifecycle_id: str | None = None
    state_token: str | None = None
    source_namespace: str | None = None
    source_locator: Locator | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.resource_id, "resource_id")
        if self.status not in {"never_created", "published", "live_without_projections", "deleted"}:
            raise ValueError("invalid publication status")
        if self.status == "never_created":
            if any(
                v is not None for v in (self.lifecycle_id, self.state_token, self.source_namespace, self.source_locator)
            ):
                raise ValueError("never-created state has no binding or authority")
        else:
            for value in (self.lifecycle_id, self.state_token, self.source_namespace):
                require_non_empty(value, "publication identity")
            if not isinstance(self.source_locator, Locator):
                raise TypeError("source_locator must be Locator")

    @property
    def live(self) -> bool:
        return self.status in {"published", "live_without_projections"}


@dataclass(frozen=True)
class ExpectedPublication:
    state: PublicationState
    create: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, PublicationState) or type(self.create) is not bool:
            raise TypeError("invalid expected publication")
        if self.create == self.state.live:
            raise ValueError("creation must explicitly expect absent or deleted state")


@dataclass(frozen=True)
class ResourceSnapshot:
    state: PublicationState
    batch: PreparedResourceBatch | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, PublicationState):
            raise TypeError("state must be PublicationState")
        if (self.batch is not None) != (self.state.status == "published"):
            raise ValueError("graph and publication state differ")
        if self.batch is not None and self.batch.resource.resource_id != self.state.resource_id:
            raise ValueError("graph belongs to another resource")


@dataclass(frozen=True)
class PublicationResult:
    applied: bool
    state: PublicationState

    def __post_init__(self) -> None:
        if type(self.applied) is not bool or not isinstance(self.state, PublicationState):
            raise TypeError("invalid publication result")
