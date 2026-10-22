"""Optional publication capability, independent of the minimal catalog ports."""

from typing import Protocol, runtime_checkable

from ..domain.batches import PreparedResourceBatch
from ..domain.publication import ExpectedPublication, PublicationResult, ResourceSnapshot


@runtime_checkable
class PublicationPort(Protocol):
    def read_snapshot(self, resource_id: str) -> ResourceSnapshot: ...

    def compare_and_publish(self, batch: PreparedResourceBatch, expected: ExpectedPublication) -> PublicationResult: ...

    def withdraw_if_current(self, resource_id: str, expected: ExpectedPublication) -> PublicationResult: ...

    def delete_if_current(self, resource_id: str, expected: ExpectedPublication) -> PublicationResult: ...
