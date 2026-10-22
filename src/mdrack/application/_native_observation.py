"""Bounded native-operation observations using the existing closed app/core sink."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import uuid4

from mdrack._observability import AppEventLogger
from mdrack.domain.embedding_inputs import EmbeddingResult, EncodingRole, PreparedInput, RouteCapabilities
from mdrack.ingestion.raw_source_provenance import RawSourceError
from mdrack.ports.embeddings import EmbeddingError, PreparedEmbeddingProvider
from mdrack_core import EmbeddingSpaceRecord
from mdrack_core.domain.common import normalize_optional_request_id
from mdrack_core.observability import SafeFingerprint, safe_fingerprint


def _profile_fingerprint(value: str) -> SafeFingerprint:
    digest = value.removeprefix('sha256:')
    if len(digest) == 64 and all(c in '0123456789abcdef' for c in digest):
        return SafeFingerprint('sha256:' + digest)
    # Only the declared embedding-profile identity, never a query/path/source hash.
    return safe_fingerprint(value)


def _reason(error: BaseException) -> str:
    if isinstance(error, RawSourceError):
        return error.code.value
    if isinstance(error, asyncio.CancelledError):
        return 'cancelled'
    if isinstance(error, EmbeddingError):
        return 'embedding_provider_error'
    if isinstance(error, ValueError):
        if error.args == ('unsupported_embedding_route',):
            return 'unsupported_embedding_route'
        return 'validation_failed'
    return 'operation_failed'


class NativeObservation:
    """Two aggregate records per call; no record per input/unit/vector/handler."""

    def __init__(self, logger: AppEventLogger, operation: Literal['search', 'ingest'], *,
                 modality: str, role: EncodingRole, fingerprint: str, mode: str | None = None,
                 target: str = 'resource') -> None:
        self.logger = logger
        self.operation = operation
        self.started = time.perf_counter()
        self._branch_outcomes: dict[str, bool | None] = {}
        self.fields: dict[str, object] = {
            'schema_version': 1, 'request_id': normalize_optional_request_id(str(uuid4())),
            'attempt_count': 1, 'query_modality': modality, 'encoding_role': role.value,
            'profile_fingerprint': _profile_fingerprint(fingerprint), 'mode': mode, 'target': target,
            'effective_mode': None, 'source_bytes': None, 'prepared_input_bytes': 0,
            'provider_call_count': 0, 'extraction_call_count': 0,
            'source_resolution_ms': None, 'provider_duration_ms': 0.0, 'read_duration_ms': 0.0,
            'publication_duration_ms': None, 'preparation_duration_ms': None,
            'publication_status': 'not_requested', 'artifact_status': 'not_requested',
            'artifact_requested_count': 0, 'artifact_available_count': 0,
            'applicable_branch_count': None, 'executed_branch_count': None, 'failed_branch_count': None,
            'unique_unit_count': None, 'unique_resource_count': None, 'result_count': None,
            'degraded': False, 'degraded_branch_count': 0,
        }

    def plan_branches(self, *branch_ids: str) -> None:
        """Admitted branches, not encoder calls; pending is neither success nor failure."""
        self._branch_outcomes = dict.fromkeys(branch_ids)
        self.fields.update(applicable_branch_count=len(branch_ids), executed_branch_count=0, failed_branch_count=0)

    def branch_outcome(self, branch_id: str, *, succeeded: bool) -> None:
        self._branch_outcomes[branch_id] = succeeded
        self.fields.update(
            executed_branch_count=sum(value is True for value in self._branch_outcomes.values()),
            failed_branch_count=sum(value is False for value in self._branch_outcomes.values()),
        )

    @property
    def request_id(self) -> str:
        value = self.fields['request_id']
        assert isinstance(value, str)
        return value

    def start(self, **fields: object) -> None:
        self.fields.update(fields)
        keys = ('schema_version', 'request_id', 'attempt_count', 'query_modality', 'encoding_role',
                'profile_fingerprint', 'mode', 'target', 'candidate_limit_total', 'limit')
        self.logger.info(f'media.{self.operation}.started', extra={k: self.fields[k] for k in keys if k in self.fields})

    @contextmanager
    def stage(self, key: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.fields[key] = (time.perf_counter() - started) * 1000

    def publication(self, milliseconds: float, succeeded: bool) -> None:
        self.fields['publication_duration_ms'] = milliseconds
        self.fields['publication_status'] = 'published' if succeeded else 'failed'

    def finish(self, error: BaseException | None = None) -> None:
        elapsed = (time.perf_counter() - self.started) * 1000
        measured = 0.0
        for key in ('source_resolution_ms', 'provider_duration_ms', 'read_duration_ms', 'publication_duration_ms'):
            value = self.fields[key]
            if isinstance(value, (int, float)):
                measured += value
        self.fields.update(duration_ms=elapsed, preparation_duration_ms=max(0.0, elapsed - measured),
                           reason=None if error is None else _reason(error))
        event = f'media.{self.operation}.completed' if error is None else f'media.{self.operation}.failed'
        if error is None:
            self.logger.info(event, extra=self.fields)
        else:
            self.logger.warning(event, extra=self.fields)


class ObservedProvider:
    """Aggregate actual attempted encoder input bytes/calls/time, including failures."""

    def __init__(self, provider: PreparedEmbeddingProvider, observation: NativeObservation) -> None:
        self.provider = provider
        self.observation = observation
        self.call_count = 0
        self.input_bytes = 0
        self.duration_ms = 0.0

    @property
    def space(self) -> EmbeddingSpaceRecord:
        return self.provider.space

    @property
    def capabilities(self) -> RouteCapabilities:
        return self.provider.capabilities

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        fields = self.observation.fields
        self.call_count += 1
        self.input_bytes += value.byte_size
        fields.update(provider_call_count=self.call_count, prepared_input_bytes=self.input_bytes)
        started = time.perf_counter()
        try:
            return await self.provider.encode(value, role)
        finally:
            self.duration_ms += (time.perf_counter() - started) * 1000
            fields['provider_duration_ms'] = self.duration_ms
