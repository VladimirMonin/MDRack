"""Unit tests for pure store-generation and active-pointer contracts."""

from __future__ import annotations

import json

import pytest

from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationFingerprint,
    GenerationOperation,
    GenerationRetention,
    GenerationState,
    RetentionMode,
    StoreGeneration,
    StoreGenerationContractError,
    assert_operation_allowed,
    assert_pointer_serves_generation,
    assert_transition_allowed,
    generation_database_filename,
    validate_generation_id,
)

_MANIFEST = "b" * 64


def _generation(
    *,
    state: GenerationState = GenerationState.READY,
    generation_id: str = "g-20260718-001",
    contract_kind: GenerationContractKind = GenerationContractKind.RESOURCE_CORE_V1,
    manifest: str = _MANIFEST,
    schema_version: str = "0007",
    retention: GenerationRetention = GenerationRetention(),
) -> StoreGeneration:
    return StoreGeneration(
        generation_id=generation_id,
        contract_kind=contract_kind,
        migration_manifest_digest=manifest,
        schema_version=schema_version,
        state=state,
        created_at="2026-07-18T00:00:00Z",
        verified_at="2026-07-18T00:01:00Z" if state is GenerationState.READY else None,
        failure_reason_code="verification_failed" if state is GenerationState.FAILED else None,
        fingerprints=(
            GenerationFingerprint("extractor", "sha256:extractor"),
            GenerationFingerprint("text-space", "sha256:text-space"),
        ),
        retention=retention,
    )


def test_generation_state_and_contract_kind_inventory_is_exact() -> None:
    assert [state.value for state in GenerationState] == [
        "legacy_only",
        "rebuild_required",
        "building",
        "ready",
        "failed",
    ]
    assert [kind.value for kind in GenerationContractKind] == [
        "legacy_v0_2",
        "resource_core_v1",
        "resource_core_v2",
    ]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (GenerationState.LEGACY_ONLY, GenerationState.REBUILD_REQUIRED),
        (GenerationState.REBUILD_REQUIRED, GenerationState.BUILDING),
        (GenerationState.BUILDING, GenerationState.READY),
        (GenerationState.BUILDING, GenerationState.FAILED),
    ],
)
def test_allowed_generation_transitions(current: GenerationState, target: GenerationState) -> None:
    assert_transition_allowed(current, target)


@pytest.mark.parametrize("current", list(GenerationState))
@pytest.mark.parametrize("target", list(GenerationState))
def test_all_other_generation_transitions_fail_closed(current: GenerationState, target: GenerationState) -> None:
    allowed = {
        (GenerationState.LEGACY_ONLY, GenerationState.REBUILD_REQUIRED),
        (GenerationState.REBUILD_REQUIRED, GenerationState.BUILDING),
        (GenerationState.BUILDING, GenerationState.READY),
        (GenerationState.BUILDING, GenerationState.FAILED),
    }
    if (current, target) in allowed:
        assert_transition_allowed(current, target)
    else:
        with pytest.raises(StoreGenerationContractError, match="transition"):
            assert_transition_allowed(current, target)


@pytest.mark.parametrize("retention_mode", list(RetentionMode))
@pytest.mark.parametrize("state", list(GenerationState))
@pytest.mark.parametrize("operation", list(GenerationOperation))
def test_generation_operation_matrix(
    retention_mode: RetentionMode,
    state: GenerationState,
    operation: GenerationOperation,
) -> None:
    retention = (
        GenerationRetention()
        if retention_mode is RetentionMode.CURRENT
        else GenerationRetention(
            mode=RetentionMode.RETAINED_READ_ONLY,
            retain_through_release="v0.3-compatibility",
        )
    )
    generation = _generation(state=state, retention=retention)
    allowed = {
        (RetentionMode.CURRENT, GenerationState.READY, GenerationOperation.PRODUCTION_READ),
        (RetentionMode.CURRENT, GenerationState.READY, GenerationOperation.PRODUCTION_WRITE),
        (RetentionMode.CURRENT, GenerationState.BUILDING, GenerationOperation.CANDIDATE_REBUILD_WRITE),
        (RetentionMode.RETAINED_READ_ONLY, GenerationState.READY, GenerationOperation.PRODUCTION_READ),
    }
    if (retention_mode, state, operation) in allowed:
        assert_operation_allowed(generation, operation)
    else:
        with pytest.raises(StoreGenerationContractError, match="does not allow"):
            assert_operation_allowed(generation, operation)


def test_retained_ready_generation_rejects_original_production_write_reproduction() -> None:
    generation = _generation(
        retention=GenerationRetention(
            mode=RetentionMode.RETAINED_READ_ONLY,
            retain_through_release="v0.3-compatibility",
        )
    )

    with pytest.raises(StoreGenerationContractError, match="does not allow"):
        assert_operation_allowed(generation, GenerationOperation.PRODUCTION_WRITE)


def test_retained_building_generation_rejects_contradictory_candidate_write_metadata() -> None:
    generation = _generation(
        state=GenerationState.BUILDING,
        retention=GenerationRetention(
            mode=RetentionMode.RETAINED_READ_ONLY,
            retain_through_release="v0.3-compatibility",
        ),
    )

    with pytest.raises(StoreGenerationContractError, match="does not allow"):
        assert_operation_allowed(generation, GenerationOperation.CANDIDATE_REBUILD_WRITE)


@pytest.mark.parametrize(
    "generation_id",
    [
        "",
        ".",
        "..",
        "/absolute",
        "../escape",
        "nested/path",
        "nested\\path",
        "C:\\private",
        "contains space",
        "x" * 129,
        "é",
    ],
)
def test_generation_id_rejects_paths_traversal_and_non_portable_values(generation_id: str) -> None:
    with pytest.raises(StoreGenerationContractError, match="generation id"):
        validate_generation_id(generation_id)


def test_generation_id_derives_one_relative_private_path_free_filename() -> None:
    assert validate_generation_id("gen_01.alpha-beta") == "gen_01.alpha-beta"
    assert generation_database_filename("gen_01.alpha-beta") == "generation-gen_01.alpha-beta.sqlite3"


@pytest.mark.parametrize(
    "change",
    ["bad_digest", "bad_schema", "unordered_fingerprints", "duplicate_fingerprints", "ready_unverified", "bad_failure"],
)
def test_generation_identity_validation_fails_closed(change: str) -> None:
    values: dict[str, object] = {
        "generation_id": "gen-1",
        "contract_kind": GenerationContractKind.RESOURCE_CORE_V1,
        "migration_manifest_digest": _MANIFEST,
        "schema_version": "0007",
        "state": GenerationState.BUILDING,
        "created_at": "2026-07-18T00:00:00Z",
        "fingerprints": (
            GenerationFingerprint("a", "one"),
            GenerationFingerprint("b", "two"),
        ),
    }
    if change == "bad_digest":
        values["migration_manifest_digest"] = "sha256:not-a-digest"
    elif change == "bad_schema":
        values["schema_version"] = "7"
    elif change == "unordered_fingerprints":
        values["fingerprints"] = tuple(reversed(values["fingerprints"]))  # type: ignore[arg-type]
    elif change == "duplicate_fingerprints":
        values["fingerprints"] = (
            GenerationFingerprint("a", "one"),
            GenerationFingerprint("a", "two"),
        )
    elif change == "ready_unverified":
        values["state"] = GenerationState.READY
    else:
        values["state"] = GenerationState.FAILED
        values["failure_reason_code"] = "raw failure/path"
    with pytest.raises(StoreGenerationContractError):
        StoreGeneration(**values)  # type: ignore[arg-type]


def test_retention_is_explicit_read_only_and_never_implies_cleanup() -> None:
    retention = GenerationRetention(
        mode=RetentionMode.RETAINED_READ_ONLY,
        retain_through_release="v0.3-compatibility",
    )
    generation = StoreGeneration(
        generation_id="legacy-1",
        contract_kind=GenerationContractKind.LEGACY_V0_2,
        migration_manifest_digest=_MANIFEST,
        schema_version="0006",
        state=GenerationState.LEGACY_ONLY,
        created_at="2026-07-18T00:00:00Z",
        retention=retention,
    )

    assert generation.retention == retention
    assert not hasattr(generation.retention, "cleanup_authorized")
    with pytest.raises(StoreGenerationContractError, match="retention release"):
        GenerationRetention(mode=RetentionMode.RETAINED_READ_ONLY)


@pytest.mark.parametrize(
    "values",
    [
        {"mode": RetentionMode.CURRENT, "retain_through_release": "v0.3-compatibility"},
        {"mode": "retained_read_only", "retain_through_release": "v0.3-compatibility"},
    ],
)
def test_contradictory_retention_metadata_fails_closed(values: dict[str, object]) -> None:
    with pytest.raises(StoreGenerationContractError, match="retention"):
        GenerationRetention(**values)  # type: ignore[arg-type]


def test_pointer_serialization_is_exact_canonical_and_relative() -> None:
    pointer = ActiveGenerationPointer(
        generation_id="gen-1",
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
    )

    payload = pointer.to_bytes()

    assert payload == b'{"contract_kind":"resource_core_v1","generation_id":"gen-1","version":1}'
    assert b"/" not in payload
    assert ActiveGenerationPointer.from_bytes(payload) == pointer


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b'{"contract_kind":"resource_core_v1","generation_id":"../escape","version":1}',
        b'{"contract_kind":"resource_core_v1","generation_id":"gen-1","version":2}',
        b'{"contract_kind":"resource_core_v1","generation_id":"gen-1","version":true}',
        b'{"contract_kind":"unknown","generation_id":"gen-1","version":1}',
        b'{"contract_kind":"resource_core_v1","generation_id":"gen-1","path":"/private","version":1}',
        b'{"version":1,"generation_id":"gen-1","contract_kind":"resource_core_v1"}',
    ],
)
def test_pointer_rejects_corruption_paths_unknown_versions_and_noncanonical_bytes(payload: bytes) -> None:
    with pytest.raises(StoreGenerationContractError):
        ActiveGenerationPointer.from_bytes(payload)


def test_pointer_serves_only_matching_ready_generation() -> None:
    generation = _generation()
    pointer = ActiveGenerationPointer(generation.generation_id, generation.contract_kind)

    assert_pointer_serves_generation(
        pointer,
        generation,
        expected_manifest_digest=_MANIFEST,
        expected_schema_version="0007",
    )


def test_pointer_explicitly_serves_permitted_retained_production_read() -> None:
    generation = _generation(
        retention=GenerationRetention(
            mode=RetentionMode.RETAINED_READ_ONLY,
            retain_through_release="v0.3-compatibility",
        )
    )
    pointer = ActiveGenerationPointer(generation.generation_id, generation.contract_kind)

    assert_pointer_serves_generation(
        pointer,
        generation,
        expected_manifest_digest=_MANIFEST,
        expected_schema_version="0007",
    )


@pytest.mark.parametrize("mismatch", ["id", "kind", "manifest", "schema", "state"])
def test_pointer_generation_mismatch_or_non_ready_state_fails_closed(mismatch: str) -> None:
    generation = _generation(state=GenerationState.BUILDING if mismatch == "state" else GenerationState.READY)
    pointer = ActiveGenerationPointer(
        "other" if mismatch == "id" else generation.generation_id,
        GenerationContractKind.LEGACY_V0_2 if mismatch == "kind" else generation.contract_kind,
    )
    expected_manifest = "c" * 64 if mismatch == "manifest" else _MANIFEST
    expected_schema = "0006" if mismatch == "schema" else "0007"

    with pytest.raises(StoreGenerationContractError):
        assert_pointer_serves_generation(
            pointer,
            generation,
            expected_manifest_digest=expected_manifest,
            expected_schema_version=expected_schema,
        )


def test_pointer_payload_contains_only_public_contract_fields() -> None:
    payload = ActiveGenerationPointer(
        generation_id="gen-1",
        contract_kind=GenerationContractKind.LEGACY_V0_2,
    ).to_bytes()

    assert set(json.loads(payload)) == {"version", "generation_id", "contract_kind"}


def test_generation_metadata_serialization_is_canonical_and_round_trips() -> None:
    generation = _generation()

    payload = generation.to_bytes()

    assert StoreGeneration.from_bytes(payload) == generation
    assert payload == json.dumps(
        json.loads(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert set(json.loads(payload)) == {
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


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"{}",
        b'{"generation_id":"../private"}',
    ],
)
def test_generation_metadata_rejects_corrupt_noncanonical_and_private_payloads(
    payload: bytes,
) -> None:
    with pytest.raises(StoreGenerationContractError):
        StoreGeneration.from_bytes(payload)

    canonical = _generation().to_bytes()
    noncanonical = json.dumps(json.loads(canonical), indent=2).encode("utf-8")
    with pytest.raises(StoreGenerationContractError, match="not canonical"):
        StoreGeneration.from_bytes(noncanonical)
