"""Bounded caller-owned ready OCR files and one shared public publication use case.

The retained original JSON is owned by the caller, not ArtifactCache. Availability
and bytes are checked before preparation and again before publication. This is not
a host archival service; requested host durability is explicitly unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from mdrack._observability import AppEventLogger
from mdrack.application.enrichment import (
    DerivedArtifactRef,
    OCRPreparation,
    ReadyOCR,
    capture,
    prepare_ocr,
    publish,
)
from mdrack.application.source_resolution import SourceResolver
from mdrack_core.domain.common import to_plain_json
from mdrack_core.domain.resources import Locator
from mdrack_core.ports.publication import PublicationPort

MAX_READY_OCR_BYTES = 4 * 1024 * 1024
logger = AppEventLogger(logging.getLogger(__name__))


class ReadyOCRError(ValueError):
    """Content-free public refusal; never include paths or artifact text."""


def _read(path: Path) -> bytes:
    try:
        if path.is_symlink():
            raise ReadyOCRError('artifact_unavailable')
        with path.open('rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ReadyOCRError('artifact_unavailable')
            data = stream.read(MAX_READY_OCR_BYTES + 1)
        if len(data) > MAX_READY_OCR_BYTES:
            raise ReadyOCRError('artifact_unavailable')
        return data
    except (OSError, TypeError, ValueError):
        raise ReadyOCRError('artifact_unavailable') from None


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ReadyOCRError('artifact_invalid')
        result[key] = value
    return result


@dataclass(frozen=True)
class ReadyOCROperation:
    preparation: OCRPreparation
    # Private host input; never serialized into graph, results or diagnostics.
    path: Path
    file_sha256: str
    source_path: Path | None = None
    raw_source_revision: str | None = None


def _check_source(path: Path, expected: str) -> None:
    # Explicit caller authority only: never open a path learned from the catalog.
    with SourceResolver().image(namespace='ready-ocr-selected', source_ref='selected.png',
                                selected=path, expected_revision=expected):
        pass


class ReadyOCRService:
    def __init__(self, port: PublicationPort) -> None:
        self.port = port

    def binding(self, resource_id: str) -> dict[str, object]:
        """Explicit local artifact binding, not portable publication authority."""
        captured = capture(self.port.read_snapshot(resource_id), targets=(('ocr:whole', 'default'),))
        state = captured.snapshot.state
        assert state.source_locator is not None
        return {'resource_id': state.resource_id, 'lifecycle_id': state.lifecycle_id,
                'source_namespace': state.source_namespace,
                'source_locator': {'kind': state.source_locator.kind,
                                   'payload': to_plain_json(state.source_locator.payload)},
                'source_revision': captured.source_revision, 'purpose': 'ocr:whole'}

    def prepare(self, resource_id: str, path: Path, *, variant: str = 'default',
                lexical: bool = True, dense: bool = False, require_durable: bool = False,
                source_path: Path | None = None) -> ReadyOCROperation:
        # Authority is captured BEFORE even opening/stat-ing the caller artifact.
        captured = capture(self.port.read_snapshot(resource_id), targets=(('ocr:whole', variant),))
        if require_durable:
            raise ReadyOCRError('durable_artifact_owner_unavailable')
        raw_revision: str | None = None
        if source_path is not None:
            from collections.abc import Mapping
            assert captured.snapshot.batch is not None
            provenance = captured.snapshot.batch.resource.metadata.get('provenance')
            candidate = provenance.get('raw_source_sha256') if isinstance(provenance, Mapping) else None
            if not isinstance(candidate, str) or not candidate:
                raise ReadyOCRError('source_evidence_unavailable')
            raw_revision = candidate
            _check_source(source_path, raw_revision)
        data = _read(Path(path))
        try:
            value = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs)
            fields = {'contract', 'version', 'reference', 'resource_id', 'lifecycle_id', 'source_namespace',
                      'source_locator', 'source_revision', 'artifact_revision', 'producer', 'recipe', 'purpose',
                      'text_sha256', 'text', 'manual'}
            if (type(value) is not dict or set(value) != fields or value.pop('contract') != 'mdrack.ready-ocr'
                    or type(value['version']) is not int or value.pop('version') != 1):
                raise ValueError
            text = value.pop('text')
            loc = value.pop('source_locator')
            if type(loc) is not dict or set(loc) != {'kind', 'payload'}:
                raise ValueError
            reference = DerivedArtifactRef(**value, source_locator=Locator(**loc), retention='caller_owned')
            artifact = ReadyOCR(reference, text)
            result = prepare_ocr(captured, artifact, lexical=lexical, dense=dense)
        except (TypeError, ValueError, KeyError, RecursionError):
            raise ReadyOCRError('artifact_invalid_or_stale') from None
        return ReadyOCROperation(result, Path(path), hashlib.sha256(data).hexdigest(), source_path, raw_revision)

    def publish(self, operation: ReadyOCROperation, *, max_retries: int = 1) -> dict[str, object]:
        # Do not promise caller-owned retention from a metadata string alone.
        if hashlib.sha256(_read(operation.path)).hexdigest() != operation.file_sha256:
            raise ReadyOCRError('artifact_changed')
        if operation.source_path is not None and operation.raw_source_revision is not None:
            _check_source(operation.source_path, operation.raw_source_revision)
        preparation = operation.preparation
        if preparation.publication is None:
            status, attempts = 'not_requested', 0
        else:
            result = publish(self.port, preparation.publication, max_retries=max_retries)
            status, attempts = result.status, result.attempts
        return {'resource_id': preparation.artifact.reference.resource_id,
                'publication_status': status, 'artifact_status': preparation.status,
                'artifact_reference': preparation.artifact.reference.reference,
                'retention': 'caller_owned_verified', 'attempt_count': attempts,
                'embedding_calls': 0, 'ocr_calls': 0,
                'source_validation': 'selected_source_and_catalog' if operation.source_path is not None
                                     else 'catalog_binding_only'}

    def enrich(self, resource_id: str, path: Path, *, variant: str = 'default', lexical: bool = True,
               dense: bool = False, require_durable: bool = False, max_retries: int = 1,
               source_path: Path | None = None) -> dict[str, object]:
        logger.info('enrichment.started', extra={'provider_call_count': 0, 'extraction_call_count': 0})
        try:
            operation = self.prepare(resource_id, path, variant=variant, lexical=lexical,
                                     dense=dense, require_durable=require_durable, source_path=source_path)
            result = self.publish(operation, max_retries=max_retries)
        except Exception:
            logger.error('enrichment.failed', extra={'reason': 'operation_failed'})
            raise
        logger.info('enrichment.completed', extra={'publication_status': result['publication_status'],
                    'attempt_count': result['attempt_count'], 'provider_call_count': 0, 'extraction_call_count': 0})
        return result
