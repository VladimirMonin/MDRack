"""Fresh compact v2 candidate rebuilds from immutable Markdown source files."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.compatibility import prepared_file_to_resource_batch
from mdrack.application.generation_manager import StoreGenerationManager
from mdrack.application.indexing import IndexingService
from mdrack.application.metadata_projection import metadata_projection_policy_from_config
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationFingerprint,
    StoreGeneration,
)
from mdrack.application.vector_values import (
    FLOAT32_VALUE_POLICY,
    apply_vector_value_policy,
    value_policy_from_space_metadata,
)
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.indexing.scanner import CorpusScanError, scan_markdown_files
from mdrack.ports.storage import IndexStorage
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.domain import PreparedResourceBatch


class FreshCompactReindexError(RuntimeError):
    """A privacy-safe fresh-rebuild failure before or during candidate creation."""


@dataclass(frozen=True)
class FreshCompactCandidate:
    """A verified inactive v2 candidate and the number of source documents used."""

    generation: StoreGeneration
    source_count: int


class ExplicitFreshSource(Protocol):
    """Caller-owned immutable non-Markdown source supplied for a fresh rebuild."""

    def source_digest(self) -> str:
        """Return the exact immutable-source SHA-256 digest before or after rebuilding."""
        ...

    def prepare_batches(self) -> Sequence[PreparedResourceBatch]:
        """Re-read the source and return one complete core graph per resource."""
        ...


class _FreshSourceIdentityReader:
    """Deliberately empty identity lookup used only by IndexingService preparation."""

    def get_file_by_path(self, relative_path: str) -> None:
        del relative_path
        return None


class FreshCompactReindexService:
    """Build a clean f32/builtin v2 candidate without touching an old generation database."""

    def __init__(
        self,
        *,
        root: Path,
        config: Any,
        provider: object,
        manager: StoreGenerationManager,
        profile_name: str = "default",
        root_id: str = "default",
        source_inputs: Sequence[ExplicitFreshSource] = (),
    ) -> None:
        self.root = Path(root).resolve()
        self.config = config
        self.provider = provider
        self.manager = manager
        self.profile_name = profile_name
        self.root_id = root_id
        self.source_inputs = tuple(source_inputs)

    def rebuild(self) -> FreshCompactCandidate:
        """Reparse source Markdown into a new verified, inactive v2 generation."""
        source_snapshot = self._source_snapshot()
        explicit_snapshot = self._explicit_source_snapshot()
        profile = embedding_profile_from_config(self.config, self.provider, self.profile_name)
        if profile.vector_value_policy != FLOAT32_VALUE_POLICY:
            raise FreshCompactReindexError("fresh_rebuild_requires_float32")
        run_id = f"fresh-{uuid.uuid4().hex}"
        metadata_policy = metadata_projection_policy_from_config(self.config.metadata)

        def rebuild(connection: sqlite3.Connection) -> None:
            source_preparer = IndexingService(
                self.root,
                self.config,
                cast(IndexStorage, _FreshSourceIdentityReader()),
                provider=self.provider,
                profile=self.profile_name,
                root_id=self.root_id,
            )
            core_indexer = CoreIndexingService(SQLiteResourceStore(connection))
            expected_resource_count = len(source_snapshot)
            for relative_path in (Path(value) for value in sorted(source_snapshot)):
                prepared = source_preparer._prepare_file(relative_path, run_id)
                batch = prepared_file_to_resource_batch(
                    prepared,
                    metadata_policy=metadata_policy,
                )
                if any(space.fingerprint != profile.fingerprint for space in batch.spaces):
                    raise FreshCompactReindexError("fresh_embedding_profile_mismatch")
                core_indexer.index(batch)
            for source_input in self.source_inputs:
                batches = tuple(source_input.prepare_batches())
                if not batches:
                    raise FreshCompactReindexError("explicit_source_empty")
                for batch in batches:
                    core_indexer.index(apply_vector_value_policy(batch, FLOAT32_VALUE_POLICY))
                expected_resource_count += len(batches)
            self._assert_candidate_identity(
                connection,
                expected_resource_count=expected_resource_count,
            )
            if (
                self._source_snapshot() != source_snapshot
                or self._explicit_source_snapshot() != explicit_snapshot
            ):
                raise FreshCompactReindexError("source_changed_during_rebuild")

        generation = self.manager.build_candidate(
            rebuild,
            fingerprint_supplier=self._candidate_embedding_space_fingerprints,
        )
        return FreshCompactCandidate(
            generation=generation,
            source_count=len(source_snapshot) + len(explicit_snapshot),
        )

    def activate(self, generation_id: str) -> ActiveGenerationPointer:
        """Perform the explicit one-way cutover after candidate verification."""
        return self.manager.activate_candidate_one_way(generation_id)

    def verify(self, generation_id: str) -> dict[str, int]:
        """Reopen and verify a ready v2 candidate without changing the active pointer."""
        generation = self.manager.load_generation(generation_id)
        if generation.state.value != "ready" or generation.contract_kind.value != "resource_core_v2":
            raise FreshCompactReindexError("fresh_candidate_not_ready")
        return dict(self.manager.verify_generation(generation_id))

    def _source_snapshot(self) -> dict[str, str]:
        try:
            paths = scan_markdown_files(
                self.root,
                self.config.scan.include,
                self.config.scan.exclude,
            )
            return {
                relative_path.as_posix(): hashlib.sha256(
                    (self.root / relative_path).read_bytes()
                ).hexdigest()
                for relative_path in paths
            }
        except (CorpusScanError, OSError) as exc:
            raise FreshCompactReindexError("fresh_source_discovery_failed") from exc

    def _explicit_source_snapshot(self) -> tuple[str, ...]:
        digests: list[str] = []
        for source_input in self.source_inputs:
            digest = source_input.source_digest()
            if not isinstance(digest, str) or len(digest) != 64:
                raise FreshCompactReindexError("explicit_source_digest_invalid")
            try:
                int(digest, 16)
            except ValueError as exc:
                raise FreshCompactReindexError("explicit_source_digest_invalid") from exc
            digests.append(digest)
        return tuple(digests)

    @staticmethod
    def _assert_candidate_identity(
        connection: sqlite3.Connection,
        *,
        expected_resource_count: int,
    ) -> None:
        resource_count = connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0]
        if resource_count != expected_resource_count:
            raise FreshCompactReindexError("fresh_resource_count_mismatch")
        for _fingerprint, metadata_json in connection.execute(
            "SELECT fingerprint,metadata_json FROM core_embedding_spaces"
        ).fetchall():
            if value_policy_from_space_metadata(json.loads(metadata_json)) != FLOAT32_VALUE_POLICY:
                raise FreshCompactReindexError("fresh_vector_policy_mismatch")

    @staticmethod
    def _candidate_embedding_space_fingerprints(
        connection: sqlite3.Connection,
    ) -> tuple[GenerationFingerprint, ...]:
        """Bind generation metadata to the exact core embedding-space table order."""
        return tuple(
            GenerationFingerprint(f"embedding_space:{index:04d}", row[0])
            for index, row in enumerate(
                connection.execute(
                    "SELECT fingerprint FROM core_embedding_spaces ORDER BY space_id"
                ).fetchall()
            )
        )


__all__ = [
    "FreshCompactCandidate",
    "FreshCompactReindexError",
    "FreshCompactReindexService",
    "ExplicitFreshSource",
]
