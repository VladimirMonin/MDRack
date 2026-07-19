"""Adversarial tests for the immutable public evaluation fixture contracts."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import string
from pathlib import Path
from typing import Any

import pytest
from contract_validator import (
    ContractError,
    contains_private_locator,
    document_digest,
    seal_document,
    validate_contracts,
)

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus-v1" / "manifest.json"
QUERIES = ROOT / "queries-v1" / "queries.json"
BENCHMARK = ROOT / "benchmark-v1" / "manifest.json"
SCHEMAS = ROOT / "schemas"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    for directory in ("corpus-v1", "queries-v1", "benchmark-v1"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    return (
        tmp_path / "corpus-v1" / "manifest.json",
        tmp_path / "queries-v1" / "queries.json",
        tmp_path / "benchmark-v1" / "manifest.json",
    )


def write_contract_chain(
    corpus_path: Path,
    queries_path: Path,
    benchmark_path: Path,
    corpus: dict[str, Any],
) -> None:
    write(corpus_path, seal_document(corpus))
    queries = load(queries_path)
    queries["corpus_ref"] = corpus["contract_digest"]
    write(queries_path, seal_document(queries))
    benchmark = load(benchmark_path)
    benchmark["corpus_ref"] = corpus["contract_digest"]
    benchmark["query_ref"] = queries["contract_digest"]
    write(benchmark_path, seal_document(benchmark))


def test_public_bundle_meets_roadmap_scale_and_has_stable_digests() -> None:
    corpus, queries, benchmark = validate_contracts(CORPUS, QUERIES, BENCHMARK)

    assert corpus["roadmap_scale"]["actual"] == {
        "audio": 10,
        "document": 20,
        "image": 10,
        "video": 10,
        "videos_with_frames": 5,
    }
    assert queries["roadmap_scale"]["actual"] == {
        "hybrid": 30,
        "lexical": 50,
        "resource_similarity": 20,
        "semantic": 50,
        "timestamp": 20,
    }
    assert corpus["contract_digest"] == document_digest(corpus)
    assert queries["contract_digest"] == document_digest(queries)
    assert benchmark["contract_digest"] == document_digest(benchmark)
    assert benchmark["materialization"] == {
        "artifact_policy": "generated-no-binaries",
        "gate": "W5-B13",
        "seed": 20260719,
        "status": "gated_manifest",
    }
    assert len(benchmark["cells"]) == 12


def test_published_json_schemas_are_strict_and_versioned() -> None:
    expected = {
        "corpus-v1.schema.json": "mdrack.evaluation-corpus",
        "queries-v1.schema.json": "mdrack.evaluation-queries",
        "benchmark-v1.schema.json": "mdrack.evaluation-benchmark",
    }
    for filename, contract in expected.items():
        schema = load(SCHEMAS / filename)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["contract"] == {"const": contract}
        assert schema["properties"]["schema_version"] == {"const": 1}

    corpus_schema = load(SCHEMAS / "corpus-v1.schema.json")
    license_pattern = corpus_schema["$defs"]["provenance"]["properties"]["license_spdx"]["pattern"]
    assert "CC0-1" in license_pattern
    assert "LicenseRef" not in license_pattern

    query_schema = load(SCHEMAS / "queries-v1.schema.json")
    case_schema = query_schema["$defs"]["case"]
    assert len(case_schema["allOf"]) == 7
    assert case_schema["properties"]["basis"]["enum"] == [
        "document_text",
        "ocr_text",
        "caption_text",
        "transcript_text",
        "frame_caption_text",
    ]


def test_missing_required_schema_field_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    del corpus["policy_refs"]
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="missing required fields"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_duplicate_json_key_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    original = corpus_path.read_text(encoding="utf-8")
    corpus_path.write_text(
        original.replace(
            "{\n",
            '{\n  "contract": "mdrack.evaluation-corpus",\n',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="duplicate key"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_contract_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    corpus["corpus_version"] = "tampered"
    write(corpus_path, corpus)

    with pytest.raises(ContractError, match="contract digest mismatch"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_duplicate_resource_id_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    corpus["resources"][1]["resource_id"] = corpus["resources"][0]["resource_id"]
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="resource IDs must be unique"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_duplicate_query_id_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    queries = load(queries_path)
    queries["cases"][1]["query_id"] = queries["cases"][0]["query_id"]
    write(queries_path, seal_document(queries))

    with pytest.raises(ContractError, match="query IDs must be unique"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_missing_artifact_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    artifact = corpus_path.parent / corpus["resources"][0]["artifact_ref"]
    artifact.unlink()

    with pytest.raises(ContractError, match="artifact is missing"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_artifact_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    artifact = corpus_path.parent / corpus["resources"][0]["artifact_ref"]
    artifact.write_text(artifact.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    with pytest.raises(ContractError, match="artifact digest mismatch"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_zero_gold_query_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    queries = load(queries_path)
    queries["cases"][0]["judgments"] = []
    write(queries_path, seal_document(queries))

    with pytest.raises(ContractError, match="zero gold"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_invalid_timed_judgment_interval_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    queries = load(queries_path)
    timed = next(
        case
        for case in queries["cases"]
        if case["case_kind"] == "timestamp" and case["judgments"][0]["evidence"]["kind"] == "time_interval"
    )
    timed["judgments"][0]["evidence"]["end_ms"] += 1
    write(queries_path, seal_document(queries))

    with pytest.raises(ContractError, match="does not match its timed unit"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


@pytest.mark.parametrize(
    "license_expression",
    [
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "MIT OR Apache-2.0",
        "CC-BY-4.0 AND (MIT OR BSD-3-Clause)",
    ],
)
def test_approved_publication_license_matrix_passes(
    tmp_path: Path,
    license_expression: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    corpus["resources"][0]["provenance"]["license_spdx"] = license_expression
    write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)

    validate_contracts(corpus_path, queries_path, benchmark_path)


@pytest.mark.parametrize(
    "license_expression",
    [
        "NONE",
        "NOASSERTION",
        "Proprietary",
        "LicenseRef-Proprietary",
        "Fake-Public-1.0",
        "GPL-3.0-only",
        "MIT WITH Classpath-exception-2.0",
        "MIT OR Proprietary",
        "MIT AND",
    ],
)
def test_unapproved_publication_license_matrix_fails_closed(
    tmp_path: Path,
    license_expression: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    corpus["resources"][0]["provenance"]["license_spdx"] = license_expression
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="approved publication policy"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_private_or_absolute_locator_fails_without_echoing_value(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    private_value = "/home/example/private-note.md"
    corpus["resources"][0]["artifact_ref"] = private_value
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError) as error:
        validate_contracts(corpus_path, queries_path, benchmark_path)

    assert str(error.value) == "Published value contains a prohibited locator"
    assert private_value not in str(error.value)


@pytest.mark.parametrize(
    "locator",
    [
        "/etc/shadow",
        "/var/lib/private.db",
        "/приват/заметки.md",
        "/",
        "C:/Users/example/private.txt",
        "D:\\private\\notes.md",
        "\\\\server\\share\\private.txt",
        "//server/share/private.txt",
        "\\\\?\\C:\\private\\notes.md",
        "\\\\.\\PIPE\\private",
        "../private.txt",
        "safe/../../private.txt",
        "ssh://host/private",
        "ftp://host/private",
        "file:///private/path",
        "http://host/private",
        "custom+safe.v1://host/private",
        "urn:private:value",
        "custom:",
    ],
)
def test_private_locator_classifier_rejects_complete_generated_family(locator: str) -> None:
    assert contains_private_locator(locator)


@pytest.mark.parametrize(
    "value",
    [
        "ordinary public prose with punctuation: safe text follows",
        "artifacts/document-01.md",
        "queries-v1/queries.json",
        "application/vnd.mdrack.synthetic-video-text+json",
        "resource:video",
        "representation:frame_caption",
        "mode:hybrid",
        "unit:time_segment",
        "language:en",
        "length:short",
        "sha256:" + "a" * 64,
        "2026-07-19T06:30:00Z",
        "06:30:00",
        "CC-BY-4.0 AND (MIT OR BSD-3-Clause)",
        "C: is a drive label in ordinary prose",
    ],
)
def test_private_locator_classifier_preserves_public_non_locators(value: str) -> None:
    assert not contains_private_locator(value)


def test_private_locator_classifier_generated_property_oracle() -> None:
    rng = random.Random(20260719)
    scheme_tail = string.ascii_letters + string.digits + "+.-"
    segment_chars = string.ascii_lowercase + string.digits + "_-"

    for _ in range(100):
        scheme = rng.choice(string.ascii_letters) + "".join(
            rng.choice(scheme_tail) for _ in range(rng.randint(0, 12))
        )
        segment = rng.choice(string.ascii_lowercase) + "".join(
            rng.choice(segment_chars) for _ in range(rng.randint(0, 15))
        )
        drive = rng.choice(string.ascii_letters)
        separator = rng.choice(("/", "\\"))
        generated_locators = (
            f"/{segment}",
            f"{drive}:{separator}{segment}",
            f"../{segment}",
            f"safe/../{segment}",
            f"//server/{segment}",
            f"\\\\server\\{segment}",
            f"{scheme}://host/{segment}",
            f"{scheme}:{segment}",
        )
        assert all(contains_private_locator(value) for value in generated_locators)

        generated_non_locators = (
            f"relative/{segment}/artifact.txt",
            f"ordinary section {segment}: public text follows",
            f"{rng.randrange(24):02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}",
            f"language:{segment}",
            f"length:{segment}",
        )
        assert not any(contains_private_locator(value) for value in generated_non_locators)


def test_private_locator_in_nested_mapping_key_fails_without_echo(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    benchmark = load(benchmark_path)
    locator = "/etc/private-key"
    benchmark[locator] = "synthetic"
    write(benchmark_path, seal_document(benchmark))

    with pytest.raises(
        ContractError,
        match="^Published value contains a prohibited locator$",
    ) as error:
        validate_contracts(corpus_path, queries_path, benchmark_path)

    assert locator not in str(error.value)


def mutate_locator_surface(
    corpus_path: Path,
    queries_path: Path,
    benchmark_path: Path,
    surface: str,
    locator: str,
) -> None:
    corpus = load(corpus_path)
    if surface == "corpus":
        corpus["resources"][0]["provenance"]["origin"] = locator
        write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)
    elif surface == "query":
        queries = load(queries_path)
        queries["cases"][0]["query_text"] = locator
        write(queries_path, seal_document(queries))
        benchmark = load(benchmark_path)
        benchmark["query_ref"] = queries["contract_digest"]
        write(benchmark_path, seal_document(benchmark))
    elif surface == "benchmark":
        benchmark = load(benchmark_path)
        benchmark["non_claims"][0] = locator
        write(benchmark_path, seal_document(benchmark))
    else:
        resource = corpus["resources"][0]
        artifact = corpus_path.parent / resource["artifact_ref"]
        artifact.write_text(
            artifact.read_text(encoding="utf-8") + f"\n{locator}\n",
            encoding="utf-8",
        )
        digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
        resource["artifact_sha256"] = digest
        resource["content_sha256"] = digest
        write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)


@pytest.mark.parametrize("resource_kind", ["image", "audio", "video"])
@pytest.mark.parametrize("position", ["key", "value"])
@pytest.mark.parametrize("locator", ["/", "custom:"])
def test_json_artifact_locator_tokens_fail_for_every_role_and_position_without_echo(
    tmp_path: Path,
    resource_kind: str,
    position: str,
    locator: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    resource = next(item for item in corpus["resources"] if item["resource_kind"] == resource_kind)
    nested = {"outer": [{locator: "synthetic"}]} if position == "key" else {"outer": [{"text": locator}]}
    artifact_bytes = (json.dumps(nested, ensure_ascii=False) + "\n").encode()
    artifact = corpus_path.parent / resource["artifact_ref"]
    artifact.write_bytes(artifact_bytes)
    digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
    resource["artifact_sha256"] = digest
    resource["content_sha256"] = digest
    write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)

    with pytest.raises(
        ContractError,
        match="^Published value contains a prohibited locator$",
    ) as error:
        validate_contracts(corpus_path, queries_path, benchmark_path)

    assert locator not in str(error.value)


@pytest.mark.parametrize(
    "artifact_text",
    [
        '{"outer":[{"\\/\\u043f\\u0440\\u0438\\u0432\\u0430\\u0442":"synthetic"}]}\n',
        '{"outer":[{"text":"custom\\u003a"}]}\n',
    ],
)
def test_json_artifact_escaped_locator_tokens_fail_without_echo(
    tmp_path: Path,
    artifact_text: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    resource = next(item for item in corpus["resources"] if item["resource_kind"] == "image")
    artifact_bytes = artifact_text.encode()
    artifact = corpus_path.parent / resource["artifact_ref"]
    artifact.write_bytes(artifact_bytes)
    digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
    resource["artifact_sha256"] = digest
    resource["content_sha256"] = digest
    write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)

    with pytest.raises(
        ContractError,
        match="^Published value contains a prohibited locator$",
    ):
        validate_contracts(corpus_path, queries_path, benchmark_path)


@pytest.mark.parametrize("resource_kind", ["image", "audio", "video"])
def test_declared_json_artifact_rejects_malformed_json(
    tmp_path: Path,
    resource_kind: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    resource = next(item for item in corpus["resources"] if item["resource_kind"] == resource_kind)
    artifact_bytes = b'{"text": '
    artifact = corpus_path.parent / resource["artifact_ref"]
    artifact.write_bytes(artifact_bytes)
    digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
    resource["artifact_sha256"] = digest
    resource["content_sha256"] = digest
    write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)

    with pytest.raises(ContractError, match="^Published JSON artifact is invalid$"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_strictly_parseable_json_artifact_uses_decoded_token_scanner(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    resource = corpus["resources"][0]
    artifact_bytes = b'{"outer":[{"text":"custom\\u003a"}]}\n'
    artifact = corpus_path.parent / resource["artifact_ref"]
    artifact.write_bytes(artifact_bytes)
    digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
    resource["artifact_sha256"] = digest
    resource["content_sha256"] = digest
    write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)

    with pytest.raises(
        ContractError,
        match="^Published value contains a prohibited locator$",
    ):
        validate_contracts(corpus_path, queries_path, benchmark_path)


@pytest.mark.parametrize("media_type", ["text/markdown", "text/plain"])
@pytest.mark.parametrize("locator", ["/", "custom:"])
def test_non_json_text_artifact_retains_boundary_aware_locator_scanner(
    tmp_path: Path,
    media_type: str,
    locator: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    resource = corpus["resources"][0]
    resource["media_type"] = media_type
    artifact_bytes = f"Synthetic public text.\n{locator}\n".encode()
    artifact = corpus_path.parent / resource["artifact_ref"]
    artifact.write_bytes(artifact_bytes)
    digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
    resource["artifact_sha256"] = digest
    resource["content_sha256"] = digest
    write_contract_chain(corpus_path, queries_path, benchmark_path, corpus)

    with pytest.raises(
        ContractError,
        match="^Published value contains a prohibited locator$",
    ):
        validate_contracts(corpus_path, queries_path, benchmark_path)


@pytest.mark.parametrize("surface", ["corpus", "query", "benchmark", "artifact"])
@pytest.mark.parametrize(
    "locator",
    [
        "/etc/shadow",
        "C:\\private\\notes.md",
        "\\\\server\\share\\private.txt",
        "../private.txt",
        "ssh://host/private",
    ],
)
def test_resealed_private_locator_fails_on_every_publishable_surface_without_echo(
    tmp_path: Path,
    surface: str,
    locator: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    mutate_locator_surface(corpus_path, queries_path, benchmark_path, surface, locator)

    with pytest.raises(
        ContractError,
        match="^Published value contains a prohibited locator$",
    ) as error:
        validate_contracts(corpus_path, queries_path, benchmark_path)

    assert locator not in str(error.value)


@pytest.mark.parametrize("surface", ["provenance", "query", "artifact"])
def test_privacy_sentinel_fails_on_every_publishable_surface_without_echo(
    tmp_path: Path,
    surface: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    sentinel = "jane@example.com"
    corpus = load(corpus_path)
    if surface == "provenance":
        corpus["resources"][0]["provenance"]["origin"] = sentinel
        write(corpus_path, seal_document(corpus))
    elif surface == "query":
        queries = load(queries_path)
        queries["cases"][0]["query_text"] = sentinel
        write(queries_path, seal_document(queries))
    else:
        resource = corpus["resources"][0]
        artifact = corpus_path.parent / resource["artifact_ref"]
        artifact.write_text(artifact.read_text(encoding="utf-8") + sentinel, encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
        resource["artifact_sha256"] = digest
        resource["content_sha256"] = digest
        write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="prohibited privacy data") as error:
        validate_contracts(corpus_path, queries_path, benchmark_path)

    assert sentinel not in str(error.value)


def test_duplicate_artifact_reference_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    first, second = corpus["resources"][:2]
    second["artifact_ref"] = first["artifact_ref"]
    second["artifact_sha256"] = first["artifact_sha256"]
    second["content_sha256"] = first["content_sha256"]
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="artifact references must be unique"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_duplicate_artifact_digest_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    first, second = corpus["resources"][:2]
    first_artifact = corpus_path.parent / first["artifact_ref"]
    second_artifact = corpus_path.parent / second["artifact_ref"]
    second_artifact.write_bytes(first_artifact.read_bytes())
    second["artifact_sha256"] = first["artifact_sha256"]
    second["content_sha256"] = first["content_sha256"]
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="artifact digests must be unique"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_duplicate_resource_ordinal_fails_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    corpus["resources"][0]["units"][1]["ordinal"] = 0
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="unit ordinals must be unique, ordered, and contiguous"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_overlapping_timed_units_fail_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    timed_resource = next(
        resource
        for resource in corpus["resources"]
        if resource["units"][0]["unit_kind"] == "time_segment"
    )
    timed_resource["units"][1]["start_ms"] = timed_resource["units"][0]["start_ms"]
    write(corpus_path, seal_document(corpus))

    with pytest.raises(ContractError, match="timed units must be ordered and non-overlapping"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


@pytest.mark.parametrize(
    ("case_kind", "mode", "target"),
    [
        ("lexical", "semantic", "unit"),
        ("semantic", "text", "unit"),
        ("hybrid", "hybrid", "resource"),
        ("resource_similarity", "similarity", "unit"),
        ("timestamp", "text", "unit"),
    ],
)
def test_case_kind_mode_target_matrix_fails_closed(
    tmp_path: Path,
    case_kind: str,
    mode: str,
    target: str,
) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    queries = load(queries_path)
    query_case = next(item for item in queries["cases"] if item["case_kind"] == case_kind)
    query_case["mode"] = mode
    query_case["target"] = target
    write(queries_path, seal_document(queries))

    with pytest.raises(ContractError, match="frozen matrix"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_unit_target_requires_unit_shaped_judgments(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    queries = load(queries_path)
    del queries["cases"][0]["judgments"][0]["unit_id"]
    write(queries_path, seal_document(queries))

    with pytest.raises(ContractError, match="unit-target judgment requires unit_id"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_resource_target_rejects_unit_shaped_judgments(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    corpus = load(corpus_path)
    queries = load(queries_path)
    query_case = next(item for item in queries["cases"] if item["case_kind"] == "resource_similarity")
    judgment = query_case["judgments"][0]
    judged_resource = next(
        resource for resource in corpus["resources"] if resource["resource_id"] == judgment["resource_id"]
    )
    judgment["unit_id"] = judged_resource["units"][0]["unit_id"]
    write(queries_path, seal_document(queries))

    with pytest.raises(ContractError, match="resource-target judgment cannot contain unit"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


@pytest.mark.parametrize("surface", ["case", "judgment"])
def test_basis_coherence_fails_closed(tmp_path: Path, surface: str) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    queries = load(queries_path)
    query_case = queries["cases"][0]
    if surface == "case":
        query_case["basis"] = "caption_text"
    else:
        query_case["judgments"][0]["basis"] = "caption_text"
    write(queries_path, seal_document(queries))

    with pytest.raises(ContractError, match="basis"):
        validate_contracts(corpus_path, queries_path, benchmark_path)


def test_benchmark_digest_and_matrix_fail_closed(tmp_path: Path) -> None:
    corpus_path, queries_path, benchmark_path = copy_bundle(tmp_path)
    benchmark = load(benchmark_path)
    benchmark["cells"].pop()
    write(benchmark_path, seal_document(benchmark))

    with pytest.raises(ContractError, match="complete roadmap matrix"):
        validate_contracts(corpus_path, queries_path, benchmark_path)
