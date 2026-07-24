"""Pure store-generation identity, readiness, retention, and pointer contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

_GENERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SCHEMA_VERSION_PATTERN = re.compile(r"^\d{4}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_POINTER_VERSION = 1


class StoreGenerationContractError(ValueError):
    """A stable failure in generation metadata or readiness validation."""


class GenerationContractKind(StrEnum):
    """Persistent data contract represented by a store generation."""

    LEGACY_V0_2 = "legacy_v0_2"
    RESOURCE_CORE_V1 = "resource_core_v1"
    RESOURCE_CORE_V2 = "resource_core_v2"


class GenerationState(StrEnum):
    """Readiness state independent from a generation's schema version."""

    LEGACY_ONLY = "legacy_only"
    REBUILD_REQUIRED = "rebuild_required"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class RetentionMode(StrEnum):
    """Non-destructive retention marker; this contract never authorizes cleanup."""

    CURRENT = "current"
    RETAINED_READ_ONLY = "retained_read_only"


class GenerationOperation(StrEnum):
    """Operations guarded by generation readiness."""

    PRODUCTION_READ = "production_read"
    PRODUCTION_WRITE = "production_write"
    CANDIDATE_REBUILD_WRITE = "candidate_rebuild_write"


@dataclass(frozen=True, order=True)
class GenerationFingerprint:
    """Named producer or embedding-space fingerprint."""

    name: str
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "fingerprint name")
        _require_non_empty(self.value, "fingerprint value")


@dataclass(frozen=True)
class GenerationRetention:
    """Retention metadata without automatic expiry or deletion semantics."""

    mode: RetentionMode = RetentionMode.CURRENT
    retain_through_release: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, RetentionMode):
            raise StoreGenerationContractError("retention mode is invalid")
        if self.mode is RetentionMode.RETAINED_READ_ONLY:
            _require_non_empty(self.retain_through_release, "retention release")
        elif self.retain_through_release is not None:
            raise StoreGenerationContractError("current generation cannot declare retention release")


@dataclass(frozen=True)
class StoreGeneration:
    """Immutable generation identity and its persisted readiness metadata."""

    generation_id: str
    contract_kind: GenerationContractKind
    migration_manifest_digest: str
    schema_version: str
    state: GenerationState
    created_at: str
    fingerprints: tuple[GenerationFingerprint, ...] = ()
    verified_at: str | None = None
    failure_reason_code: str | None = None
    retention: GenerationRetention = GenerationRetention()

    def __post_init__(self) -> None:
        validate_generation_id(self.generation_id)
        if not isinstance(self.contract_kind, GenerationContractKind):
            raise StoreGenerationContractError("generation contract kind is invalid")
        if not _SHA256_PATTERN.fullmatch(self.migration_manifest_digest):
            raise StoreGenerationContractError("migration manifest digest is invalid")
        if not _SCHEMA_VERSION_PATTERN.fullmatch(self.schema_version):
            raise StoreGenerationContractError("schema version is invalid")
        if not isinstance(self.state, GenerationState):
            raise StoreGenerationContractError("generation state is invalid")
        _require_non_empty(self.created_at, "created timestamp")
        if not isinstance(self.fingerprints, tuple) or any(
            not isinstance(item, GenerationFingerprint) for item in self.fingerprints
        ):
            raise StoreGenerationContractError("fingerprints must be an immutable tuple")
        if self.fingerprints != tuple(sorted(self.fingerprints)):
            raise StoreGenerationContractError("fingerprints must use deterministic order")
        names = [item.name for item in self.fingerprints]
        if len(names) != len(set(names)):
            raise StoreGenerationContractError("fingerprint names must be unique")
        if not isinstance(self.retention, GenerationRetention):
            raise StoreGenerationContractError("generation retention is invalid")

        if self.verified_at is not None:
            _require_non_empty(self.verified_at, "verified timestamp")
        if self.state is GenerationState.READY and self.verified_at is None:
            raise StoreGenerationContractError("ready generation requires verification timestamp")
        if self.failure_reason_code is not None and not _REASON_CODE_PATTERN.fullmatch(self.failure_reason_code):
            raise StoreGenerationContractError("failure reason code is invalid")
        if self.state is GenerationState.FAILED:
            if self.failure_reason_code is None:
                raise StoreGenerationContractError("failed generation requires failure reason code")
        elif self.failure_reason_code is not None:
            raise StoreGenerationContractError("failure reason code is valid only for failed generation")

    def to_bytes(self) -> bytes:
        """Return canonical UTF-8 JSON for durable app-owned metadata."""
        return json.dumps(
            {
                "contract_kind": self.contract_kind.value,
                "created_at": self.created_at,
                "failure_reason_code": self.failure_reason_code,
                "fingerprints": [
                    {"name": item.name, "value": item.value} for item in self.fingerprints
                ],
                "generation_id": self.generation_id,
                "migration_manifest_digest": self.migration_manifest_digest,
                "retention": {
                    "mode": self.retention.mode.value,
                    "retain_through_release": self.retention.retain_through_release,
                },
                "schema_version": self.schema_version,
                "state": self.state.value,
                "verified_at": self.verified_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes) -> StoreGeneration:
        """Parse one exact canonical metadata record without filesystem access."""
        if not isinstance(payload, bytes):
            raise StoreGenerationContractError("generation metadata payload must be bytes")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreGenerationContractError("generation metadata payload is invalid") from exc
        expected_fields = {
            "contract_kind",
            "created_at",
            "failure_reason_code",
            "fingerprints",
            "generation_id",
            "migration_manifest_digest",
            "retention",
            "schema_version",
            "state",
            "verified_at",
        }
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise StoreGenerationContractError("generation metadata fields are invalid")
        fingerprints = value["fingerprints"]
        retention = value["retention"]
        if (
            not isinstance(fingerprints, list)
            or any(not isinstance(item, dict) or set(item) != {"name", "value"} for item in fingerprints)
            or not isinstance(retention, dict)
            or set(retention) != {"mode", "retain_through_release"}
        ):
            raise StoreGenerationContractError("generation metadata fields are invalid")
        try:
            generation = cls(
                generation_id=value["generation_id"],
                contract_kind=GenerationContractKind(value["contract_kind"]),
                migration_manifest_digest=value["migration_manifest_digest"],
                schema_version=value["schema_version"],
                state=GenerationState(value["state"]),
                created_at=value["created_at"],
                fingerprints=tuple(
                    GenerationFingerprint(item["name"], item["value"])
                    for item in fingerprints
                ),
                verified_at=value["verified_at"],
                failure_reason_code=value["failure_reason_code"],
                retention=GenerationRetention(
                    mode=RetentionMode(retention["mode"]),
                    retain_through_release=retention["retain_through_release"],
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreGenerationContractError("generation metadata payload is invalid") from exc
        if generation.to_bytes() != payload:
            raise StoreGenerationContractError("generation metadata payload is not canonical")
        return generation


@dataclass(frozen=True)
class ActiveGenerationPointer:
    """Versioned relative pointer record containing no filesystem path."""

    generation_id: str
    contract_kind: GenerationContractKind
    version: int = _POINTER_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != _POINTER_VERSION:
            raise StoreGenerationContractError("active pointer version is unsupported")
        validate_generation_id(self.generation_id)
        if not isinstance(self.contract_kind, GenerationContractKind):
            raise StoreGenerationContractError("active pointer contract kind is invalid")

    def to_bytes(self) -> bytes:
        """Return canonical UTF-8 JSON for durable pointer persistence by S5d."""
        return json.dumps(
            {
                "contract_kind": self.contract_kind.value,
                "generation_id": self.generation_id,
                "version": self.version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes) -> ActiveGenerationPointer:
        """Parse one exact canonical pointer record without filesystem access."""
        if not isinstance(payload, bytes):
            raise StoreGenerationContractError("active pointer payload must be bytes")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreGenerationContractError("active pointer payload is invalid") from exc
        if not isinstance(value, dict) or set(value) != {"contract_kind", "generation_id", "version"}:
            raise StoreGenerationContractError("active pointer fields are invalid")
        if type(value["version"]) is not int or not isinstance(value["generation_id"], str):
            raise StoreGenerationContractError("active pointer field types are invalid")
        try:
            contract_kind = GenerationContractKind(value["contract_kind"])
        except (TypeError, ValueError) as exc:
            raise StoreGenerationContractError("active pointer contract kind is invalid") from exc
        pointer = cls(
            generation_id=value["generation_id"],
            contract_kind=contract_kind,
            version=value["version"],
        )
        if pointer.to_bytes() != payload:
            raise StoreGenerationContractError("active pointer payload is not canonical")
        return pointer


def validate_generation_id(generation_id: object) -> str:
    """Validate one opaque ID that can safely derive a single relative filename."""
    if not isinstance(generation_id, str) or not _GENERATION_ID_PATTERN.fullmatch(generation_id):
        raise StoreGenerationContractError("generation id is invalid")
    if generation_id in {".", ".."} or "/" in generation_id or "\\" in generation_id:
        raise StoreGenerationContractError("generation id is invalid")
    return generation_id


def generation_database_filename(generation_id: object) -> str:
    """Derive the immutable app-owned database filename without touching disk."""
    return f"generation-{validate_generation_id(generation_id)}.sqlite3"


def assert_transition_allowed(current: GenerationState, target: GenerationState) -> None:
    """Fail closed unless a state transition is part of the frozen lifecycle."""
    if not isinstance(current, GenerationState) or not isinstance(target, GenerationState):
        raise StoreGenerationContractError("generation transition state is invalid")
    allowed = {
        GenerationState.LEGACY_ONLY: frozenset({GenerationState.REBUILD_REQUIRED}),
        GenerationState.REBUILD_REQUIRED: frozenset({GenerationState.BUILDING}),
        GenerationState.BUILDING: frozenset({GenerationState.READY, GenerationState.FAILED}),
        GenerationState.READY: frozenset(),
        GenerationState.FAILED: frozenset(),
    }
    if target not in allowed[current]:
        raise StoreGenerationContractError("generation state transition is not allowed")


def assert_operation_allowed(generation: StoreGeneration, operation: GenerationOperation) -> None:
    """Enforce readiness and retained-read-only operation authorization."""
    if not isinstance(generation, StoreGeneration) or not isinstance(operation, GenerationOperation):
        raise StoreGenerationContractError("generation operation is invalid")
    allowed = {
        GenerationState.READY: {
            GenerationOperation.PRODUCTION_READ,
            GenerationOperation.PRODUCTION_WRITE,
        },
        GenerationState.BUILDING: {GenerationOperation.CANDIDATE_REBUILD_WRITE},
    }
    if generation.retention.mode is RetentionMode.RETAINED_READ_ONLY:
        allowed = {
            GenerationState.READY: {GenerationOperation.PRODUCTION_READ},
        }
    if operation not in allowed.get(generation.state, set()):
        raise StoreGenerationContractError("generation state does not allow operation")


def assert_pointer_serves_generation(
    pointer: ActiveGenerationPointer,
    generation: StoreGeneration,
    *,
    expected_manifest_digest: str,
    expected_schema_version: str,
) -> None:
    """Validate pointer identity and readiness before production serving."""
    if not isinstance(pointer, ActiveGenerationPointer) or not isinstance(generation, StoreGeneration):
        raise StoreGenerationContractError("active generation identity is invalid")
    if not _SHA256_PATTERN.fullmatch(expected_manifest_digest):
        raise StoreGenerationContractError("expected migration manifest digest is invalid")
    if not _SCHEMA_VERSION_PATTERN.fullmatch(expected_schema_version):
        raise StoreGenerationContractError("expected schema version is invalid")
    if (
        pointer.generation_id != generation.generation_id
        or pointer.contract_kind is not generation.contract_kind
        or generation.migration_manifest_digest != expected_manifest_digest
        or generation.schema_version != expected_schema_version
    ):
        raise StoreGenerationContractError("active generation identity does not match")
    assert_operation_allowed(generation, GenerationOperation.PRODUCTION_READ)


def _require_non_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoreGenerationContractError(f"{field} must be a non-empty string")
    try:
        str.encode(value, "utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise StoreGenerationContractError(f"{field} must be UTF-8 encodable") from exc
    return value
