"""App-owned store-generation lifecycle, durable pointer, recovery, and rollback."""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import secrets
import sqlite3
import struct
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationFingerprint,
    GenerationRetention,
    GenerationState,
    RetentionMode,
    StoreGeneration,
    StoreGenerationContractError,
    assert_pointer_serves_generation,
    assert_transition_allowed,
    generation_database_filename,
    validate_generation_id,
)
from mdrack.storage.sqlite.migrations import (
    ACTIVE_MIGRATION_VERSION,
    EXPECTED_MIGRATION_MANIFEST,
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    get_migrations_dir,
)
from mdrack_sqlite.contract_v2 import (
    SQLITE_CATALOG_V2_SCHEMA_VERSION,
    SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
)

FailureHook = Callable[[str], None]
RebuildCallback = Callable[[sqlite3.Connection], None]
Clock = Callable[[], str]
IdFactory = Callable[[], str]

logger = logging.getLogger(__name__)

_RUNTIME_REASON_CODES = frozenset(
    {
        "candidate_checkpoint_busy",
        "candidate_adapter_readback_invalid",
        "candidate_confidence_invalid",
        "candidate_create_failed",
        "candidate_durability_failed",
        "candidate_fts_invalid",
        "candidate_fingerprint_mismatch",
        "candidate_graph_invalid",
        "candidate_integrity_failed",
        "candidate_manifest_mismatch",
        "candidate_migration_failed",
        "candidate_open_failed",
        "candidate_schema_missing",
        "candidate_transaction_open",
        "candidate_vector_invalid",
        "candidate_verification_failed",
        "generation_integrity_failed",
        "generation_open_failed",
        "generation_schema_mismatch",
    }
)


class StoreGenerationManagerError(RuntimeError):
    """A stable privacy-safe generation lifecycle failure."""


class GenerationRuntime(Protocol):
    """Persistence-specific candidate runtime supplied at the composition edge."""

    def create_candidate(self, database_path: Path) -> sqlite3.Connection: ...

    def migrate_candidate(self, connection: sqlite3.Connection) -> None: ...

    def verify_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        expected_fingerprints: tuple[str, ...] = (),
    ) -> Mapping[str, int]: ...

    def finalize_candidate(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
    ) -> None: ...

    def verify_database_path(
        self,
        database_path: Path,
        *,
        expected_version: str,
        expected_fingerprints: tuple[str, ...] = (),
    ) -> Mapping[str, int]: ...


@dataclasses.dataclass(frozen=True)
class GenerationStatus:
    """Privacy-safe status projection without filesystem paths or raw errors."""

    pointer_status: str
    active_generation_id: str | None
    active_contract_kind: str | None
    active_state: str | None
    generations_total: int
    building_total: int
    ready_total: int
    failed_total: int
    corrupt_metadata_total: int

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


class StoreGenerationManager:
    """The sole production owner for generation creation, switching, and recovery."""

    def __init__(
        self,
        store_dir: Path,
        *,
        runtime: GenerationRuntime,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        failure_hook: FailureHook | None = None,
    ) -> None:
        self.store_dir = Path(store_dir)
        self.generations_dir = self.store_dir / "generations"
        self.pointer_path = self.store_dir / "active-generation.json"
        self.lock_path = self.store_dir / ".generation-manager.lock"
        self.runtime = runtime
        self._clock = clock or _utc_now
        self._id_factory = id_factory or (lambda: f"g-{secrets.token_hex(16)}")
        self._failure_hook = failure_hook

    def set_failure_hook(self, hook: FailureHook | None) -> None:
        self._failure_hook = hook

    def database_path(self, generation_id: str) -> Path:
        return self.generations_dir / generation_database_filename(generation_id)

    def metadata_path(self, generation_id: str) -> Path:
        return self.generations_dir / f"generation-{validate_generation_id(generation_id)}.json"

    def build_candidate(
        self,
        rebuild: RebuildCallback,
        *,
        fingerprints: tuple[GenerationFingerprint, ...] = (),
    ) -> StoreGeneration:
        """Build, verify, durably finalize, and mark one inactive candidate ready."""
        with self._writer_lease():
            generation_id = validate_generation_id(self._id_factory())
            database_path = self.database_path(generation_id)
            if self.metadata_path(generation_id).exists():
                raise StoreGenerationManagerError("candidate_identity_exists")
            generation = StoreGeneration(
                generation_id=generation_id,
                contract_kind=GenerationContractKind.RESOURCE_CORE_V2,
                migration_manifest_digest=SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
                schema_version=SQLITE_CATALOG_V2_SCHEMA_VERSION,
                state=GenerationState.BUILDING,
                created_at=self._clock(),
                fingerprints=tuple(sorted(fingerprints)),
            )
            logger.info("store.generation.candidate.started")
            connection = None
            try:
                connection = self.runtime.create_candidate(database_path)
                self._persist_generation(generation)
                self._fail("after_building_metadata")
                self.runtime.migrate_candidate(connection)
                rebuild(connection)
                self._fail("after_rebuild")
                self.runtime.verify_candidate(
                    connection,
                    expected_fingerprints=tuple(item.value for item in generation.fingerprints),
                )
                self.runtime.finalize_candidate(connection, database_path)
                connection = None
                ready = self._transition(
                    generation,
                    GenerationState.READY,
                    verified_at=self._clock(),
                )
                self._persist_generation(ready, prefix="ready_metadata")
                self._fail("after_ready_metadata")
                logger.info("store.generation.candidate.ready")
                return ready
            except Exception as exc:
                if connection is not None:
                    connection.close()
                if self._is_durable_ready(generation_id):
                    logger.warning("store.generation.candidate.interrupted")
                    raise StoreGenerationManagerError("candidate_build_interrupted") from exc
                try:
                    if self.metadata_path(generation_id).exists():
                        failed = self._transition(
                            generation,
                            GenerationState.FAILED,
                            failure_reason_code=_failure_reason(exc),
                        )
                        self._persist_generation(failed)
                except Exception:
                    pass
                logger.warning(
                    "store.generation.candidate.failed reason=%s",
                    _failure_reason(exc),
                )
                if isinstance(exc, StoreGenerationManagerError):
                    raise
                raise StoreGenerationManagerError("candidate_build_failed") from exc

    def register_legacy_generation(
        self,
        generation_id: str,
        *,
        retain_through_release: str,
    ) -> StoreGeneration:
        """Record an already-retained untouched 0006 generation without modifying it."""
        with self._writer_lease():
            database_path = self.database_path(generation_id)
            self.runtime.verify_database_path(
                database_path,
                expected_version=ACTIVE_MIGRATION_VERSION,
            )
            generation = StoreGeneration(
                generation_id=generation_id,
                contract_kind=GenerationContractKind.LEGACY_V0_2,
                migration_manifest_digest=_legacy_manifest_digest(),
                schema_version=ACTIVE_MIGRATION_VERSION,
                state=GenerationState.LEGACY_ONLY,
                created_at=self._clock(),
                retention=GenerationRetention(
                    mode=RetentionMode.RETAINED_READ_ONLY,
                    retain_through_release=retain_through_release,
                ),
            )
            self._persist_generation(generation, exclusive=True)
            return generation

    def initialize_legacy_pointer(self, generation_id: str) -> ActiveGenerationPointer:
        """Create the first pointer only for a verified retained legacy generation."""
        with self._writer_lease():
            if self.pointer_path.exists():
                raise StoreGenerationManagerError("active_pointer_exists")
            generation = self.load_generation(generation_id)
            self._verify_rollback_target(generation)
            pointer = ActiveGenerationPointer(generation_id, GenerationContractKind.LEGACY_V0_2)
            self._persist_pointer(pointer)
            return pointer

    def activate_candidate(self, generation_id: str) -> ActiveGenerationPointer:
        """Atomically switch from a valid current pointer to a verified ready candidate."""
        with self._writer_lease():
            self.resolve_active()
            generation = self.load_generation(generation_id)
            self._verify_ready_generation(generation)
            _manifest_digest, schema_version = _core_contract_expectations(generation.contract_kind)
            self.runtime.verify_database_path(
                self.database_path(generation_id),
                expected_version=schema_version,
                expected_fingerprints=tuple(item.value for item in generation.fingerprints),
            )
            pointer = ActiveGenerationPointer(generation_id, generation.contract_kind)
            self._persist_pointer(pointer)
            logger.info("store.generation.candidate.activated")
            return pointer

    def rollback(self, legacy_generation_id: str) -> ActiveGenerationPointer:
        """Reject runtime pointer rollback; retained stores are preservation-only."""
        del legacy_generation_id
        raise StoreGenerationManagerError("rollback_unsupported")

    def resolve_active(self) -> tuple[ActiveGenerationPointer, StoreGeneration, Path]:
        """Recover active truth from the durable pointer and fail closed on contradiction."""
        try:
            pointer = ActiveGenerationPointer.from_bytes(self.pointer_path.read_bytes())
            generation = self.load_generation(pointer.generation_id)
            database_path = self.database_path(pointer.generation_id)
            if pointer.contract_kind in {
                GenerationContractKind.RESOURCE_CORE_V1,
                GenerationContractKind.RESOURCE_CORE_V2,
            }:
                manifest_digest, schema_version = _core_contract_expectations(pointer.contract_kind)
                assert_pointer_serves_generation(
                    pointer,
                    generation,
                    expected_manifest_digest=manifest_digest,
                    expected_schema_version=schema_version,
                )
                self.runtime.verify_database_path(
                    database_path,
                    expected_version=schema_version,
                    expected_fingerprints=tuple(item.value for item in generation.fingerprints),
                )
            elif pointer.contract_kind is GenerationContractKind.LEGACY_V0_2:
                self._verify_rollback_target(generation)
            else:
                raise StoreGenerationManagerError("active_generation_invalid")
            return pointer, generation, database_path
        except Exception as exc:
            if isinstance(exc, StoreGenerationManagerError):
                raise
            raise StoreGenerationManagerError("active_generation_invalid") from exc

    def load_generation(self, generation_id: str) -> StoreGeneration:
        try:
            generation = StoreGeneration.from_bytes(
                self.metadata_path(generation_id).read_bytes()
            )
        except Exception as exc:
            raise StoreGenerationManagerError("generation_metadata_invalid") from exc
        if generation.generation_id != generation_id:
            raise StoreGenerationManagerError("generation_metadata_invalid")
        return generation

    def _is_durable_ready(self, generation_id: str) -> bool:
        try:
            return self.load_generation(generation_id).state is GenerationState.READY
        except StoreGenerationManagerError:
            return False

    def status(self) -> GenerationStatus:
        """Return a privacy-safe snapshot, including corrupt metadata as counts only."""
        states: list[GenerationState] = []
        corrupt = 0
        metadata_ids: set[str] = set()
        if self.generations_dir.is_dir():
            for path in self.generations_dir.glob("generation-*.json"):
                try:
                    generation = StoreGeneration.from_bytes(path.read_bytes())
                    metadata_ids.add(generation.generation_id)
                    states.append(generation.state)
                except Exception:
                    corrupt += 1
            for path in self.generations_dir.glob("generation-*.sqlite3"):
                generation_id = path.name.removeprefix("generation-").removesuffix(".sqlite3")
                if generation_id not in metadata_ids:
                    corrupt += 1
        pointer_status = "missing"
        active_id: str | None = None
        active_kind: str | None = None
        active_state: str | None = None
        if self.pointer_path.exists():
            try:
                pointer, generation, _path = self.resolve_active()
                pointer_status = "valid"
                active_id = pointer.generation_id
                active_kind = pointer.contract_kind.value
                active_state = generation.state.value
            except StoreGenerationManagerError:
                pointer_status = "invalid"
        return GenerationStatus(
            pointer_status=pointer_status,
            active_generation_id=active_id,
            active_contract_kind=active_kind,
            active_state=active_state,
            generations_total=len(states),
            building_total=states.count(GenerationState.BUILDING),
            ready_total=states.count(GenerationState.READY),
            failed_total=states.count(GenerationState.FAILED),
            corrupt_metadata_total=corrupt,
        )

    def _verify_ready_generation(self, generation: StoreGeneration) -> None:
        pointer = ActiveGenerationPointer(generation.generation_id, generation.contract_kind)
        try:
            manifest_digest, schema_version = _core_contract_expectations(generation.contract_kind)
            assert_pointer_serves_generation(
                pointer,
                generation,
                expected_manifest_digest=manifest_digest,
                expected_schema_version=schema_version,
            )
        except (StoreGenerationContractError, StoreGenerationManagerError) as exc:
            raise StoreGenerationManagerError("candidate_not_ready") from exc

    def _verify_rollback_target(self, generation: StoreGeneration) -> None:
        if (
            generation.contract_kind is not GenerationContractKind.LEGACY_V0_2
            or generation.schema_version != ACTIVE_MIGRATION_VERSION
            or generation.migration_manifest_digest != _legacy_manifest_digest()
            or generation.retention.mode is not RetentionMode.RETAINED_READ_ONLY
            or generation.state not in {GenerationState.LEGACY_ONLY, GenerationState.READY}
        ):
            raise StoreGenerationManagerError("rollback_target_invalid")
        try:
            self.runtime.verify_database_path(
                self.database_path(generation.generation_id),
                expected_version=ACTIVE_MIGRATION_VERSION,
            )
        except Exception as exc:
            raise StoreGenerationManagerError("rollback_target_invalid") from exc

    def _transition(
        self,
        generation: StoreGeneration,
        state: GenerationState,
        *,
        verified_at: str | None = None,
        failure_reason_code: str | None = None,
    ) -> StoreGeneration:
        assert_transition_allowed(generation.state, state)
        return dataclasses.replace(
            generation,
            state=state,
            verified_at=verified_at,
            failure_reason_code=failure_reason_code,
        )

    def _persist_generation(
        self,
        generation: StoreGeneration,
        *,
        exclusive: bool = False,
        prefix: str = "metadata",
    ) -> None:
        path = self.metadata_path(generation.generation_id)
        if exclusive and path.exists():
            raise StoreGenerationManagerError("generation_metadata_exists")
        _atomic_write(path, generation.to_bytes(), self._fail, prefix)

    def _persist_pointer(self, pointer: ActiveGenerationPointer) -> None:
        _atomic_write(self.pointer_path, pointer.to_bytes(), self._fail, "pointer")

    @contextmanager
    def _writer_lease(self) -> Iterator[None]:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _lock_descriptor(descriptor)
        except OSError as exc:
            os.close(descriptor)
            raise StoreGenerationManagerError("generation_manager_busy") from exc
        try:
            yield
        finally:
            _unlock_descriptor(descriptor)
            os.close(descriptor)

    def _fail(self, point: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(point)


def _atomic_write(
    path: Path,
    payload: bytes,
    failure_hook: FailureHook,
    prefix: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    replaced = False
    try:
        failure_hook(f"before_{prefix}_temporary_create")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        _write_all(descriptor, payload)
        failure_hook(f"after_{prefix}_temporary_write")
        os.fsync(descriptor)
        failure_hook(f"after_{prefix}_temporary_fsync")
        os.close(descriptor)
        descriptor = None
        failure_hook(f"before_{prefix}_replace")
        os.replace(temporary, path)
        replaced = True
        failure_hook(f"after_{prefix}_replace")
        _fsync_directory(path.parent)
        failure_hook(f"after_{prefix}_directory_fsync")
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if not replaced:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        raise


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _core_contract_expectations(contract_kind: GenerationContractKind) -> tuple[str, str]:
    if contract_kind is GenerationContractKind.RESOURCE_CORE_V2:
        return SQLITE_V2_MIGRATION_MANIFEST_DIGEST, SQLITE_CATALOG_V2_SCHEMA_VERSION
    if contract_kind is GenerationContractKind.RESOURCE_CORE_V1:
        return EXPECTED_MIGRATION_MANIFEST_DIGEST, EXPECTED_MIGRATION_VERSION
    raise StoreGenerationManagerError("candidate_contract_unsupported")


def _legacy_manifest_digest() -> str:
    digest = hashlib.sha256()
    migrations_dir = get_migrations_dir()
    for filename, _expected_hash in EXPECTED_MIGRATION_MANIFEST:
        if filename.startswith("0007_"):
            break
        name = filename.encode("utf-8")
        content = (migrations_dir / filename).read_bytes()
        digest.update(struct.pack(">Q", len(name)))
        digest.update(name)
        digest.update(struct.pack(">Q", len(content)))
        digest.update(content)
    return digest.hexdigest()


def _failure_reason(exc: Exception) -> str:
    reason = str(exc)
    if reason in _RUNTIME_REASON_CODES:
        return reason
    return "rebuild_failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
