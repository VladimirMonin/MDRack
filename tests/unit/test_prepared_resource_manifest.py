"""Contract tests for the bounded prepared-resource manifest v1 facade."""

from __future__ import annotations

import copy
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mdrack.application.manifest import (
    MANIFEST_CONTRACT,
    MANIFEST_VERSION,
    MAX_COLLECTION_ITEMS,
    MAX_JSON_DEPTH,
    MAX_MANIFEST_BYTES,
    MAX_METADATA_BYTES,
    MAX_TEXT_BYTES,
    MAX_VECTOR_DIMENSIONS,
    ManifestError,
    ManifestErrorCode,
    decode_prepared_resource_manifest,
    import_manifest,
)
from mdrack_core.domain import PreparedResourceBatch


class CatalogSpy:
    def __init__(self) -> None:
        self.calls: list[PreparedResourceBatch] = []
        self.transactions_opened = 0

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        self.transactions_opened += 1
        self.calls.append(batch)

    def delete_resource(self, resource_id: str) -> None:
        raise AssertionError(resource_id)


def _manifest() -> dict[str, Any]:
    return {
        "contract": MANIFEST_CONTRACT,
        "version": MANIFEST_VERSION,
        "resource": {
            "resource_id": "resource-1",
            "resource_kind": "document",
            "media_type": "text/plain",
            "source_namespace": "fixture",
            "locator": {
                "kind": "opaque",
                "payload": {"path": "/PRIVATE_SOURCE_SENTINEL.bin"},
            },
            "content_hash": "sha256:fixture",
            "title": "Fixture",
            "metadata": {"safe": True},
        },
        "representations": [
            {
                "representation_id": "representation-1",
                "resource_id": "resource-1",
                "representation_kind": "retrieval_text",
                "modality": "text",
                "text": "searchable text",
                "language": "en",
                "producer_fingerprint": "producer-v1",
                "token_count": 2,
                "token_count_kind": "exact",
                "metadata": {"source": "prepared"},
            }
        ],
        "units": [
            {
                "unit_id": "unit-1",
                "resource_id": "resource-1",
                "representation_id": "representation-1",
                "unit_kind": "text_chunk",
                "modality": "text",
                "text": "searchable text",
                "evidence_locator": {"kind": "span", "payload": {"start": 0, "end": 15}},
                "ordinal": 0,
                "token_count": 2,
                "token_count_kind": "exact",
                "metadata": {},
            }
        ],
        "spaces": [
            {
                "space_id": "space-1",
                "dimensions": 2,
                "metric": "dot",
                "fingerprint": "space-v1",
                "metadata": {},
            }
        ],
        "vectors": [{"unit_id": "unit-1", "space_id": "space-1", "vector": [1.0, -0.0]}],
        "facets": [
            {
                "resource_id": "resource-1",
                "facet": {"namespace": "tag", "value": "fixture"},
                "origin": "user",
                "producer_fingerprint": "facet-v1",
                "confidence": 1.0,
            }
        ],
    }


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _assert_error(payload: bytes, code: ManifestErrorCode) -> None:
    with pytest.raises(ManifestError) as caught:
        decode_prepared_resource_manifest(payload)
    assert caught.value.code is code
    assert str(caught.value) == code.value
    assert "PRIVATE" not in str(caught.value)


def test_valid_manifest_round_trips_to_typed_batch_and_one_adapter_call() -> None:
    catalog = CatalogSpy()

    batch = import_manifest(catalog, _encode(_manifest()))

    assert isinstance(batch, PreparedResourceBatch)
    assert batch.resource.resource_id == "resource-1"
    assert batch.resource.locator.payload["path"] == "/PRIVATE_SOURCE_SENTINEL.bin"
    assert len(catalog.calls) == 1
    assert catalog.calls[0] is batch
    assert catalog.transactions_opened == 1


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.update(extra="PRIVATE_UNKNOWN_SENTINEL"), ManifestErrorCode.UNKNOWN_FIELD),
        (lambda value: value.update(contract="other"), ManifestErrorCode.UNSUPPORTED_CONTRACT),
        (lambda value: value.update(version=2), ManifestErrorCode.UNSUPPORTED_VERSION),
        (
            lambda value: value["resource"].update(extra="PRIVATE_UNKNOWN_SENTINEL"),
            ManifestErrorCode.UNKNOWN_FIELD,
        ),
        (lambda value: value["resource"].pop("resource_id"), ManifestErrorCode.INVALID_MANIFEST),
        (lambda value: value["vectors"][0].update(vector=[]), ManifestErrorCode.INVALID_MANIFEST),
        (lambda value: value["spaces"][0].update(dimensions=True), ManifestErrorCode.INVALID_MANIFEST),
        (lambda value: value["facets"][0].update(confidence=2.0), ManifestErrorCode.INVALID_MANIFEST),
    ],
)
def test_closed_schema_and_typed_values_fail_with_safe_categories(
    mutate: Any,
    code: ManifestErrorCode,
) -> None:
    value = _manifest()
    mutate(value)

    _assert_error(_encode(value), code)


def test_strict_json_rejects_duplicate_keys_utf8_constants_and_syntax() -> None:
    _assert_error(b'{"contract":"a","contract":"b"}', ManifestErrorCode.DUPLICATE_KEY)
    _assert_error(b"\xff", ManifestErrorCode.INVALID_ENCODING)
    _assert_error(b'{"value":NaN}', ManifestErrorCode.NON_FINITE_NUMBER)
    _assert_error(b'{"value":Infinity}', ManifestErrorCode.NON_FINITE_NUMBER)
    _assert_error(b'{"value":-Infinity}', ManifestErrorCode.NON_FINITE_NUMBER)
    _assert_error(b"{", ManifestErrorCode.INVALID_JSON)
    _assert_error(b'{"value":"\\ud800"}', ManifestErrorCode.INVALID_ENCODING)


def test_raw_byte_limit_accepts_exact_boundary_and_rejects_max_plus_one() -> None:
    encoded = _encode(_manifest())
    exact = encoded + (b" " * (MAX_MANIFEST_BYTES - len(encoded)))

    assert decode_prepared_resource_manifest(exact).resource.resource_id == "resource-1"
    _assert_error(exact + b" ", ManifestErrorCode.PAYLOAD_TOO_LARGE)


def _nested_value(mapping_levels: int) -> object:
    value: object = "leaf"
    for _ in range(mapping_levels):
        value = {"safe": value}
    return value


def test_depth_limit_accepts_limit_then_rejects_max_plus_one_before_schema() -> None:
    at_limit = _manifest()
    at_limit["private_extension"] = _nested_value(MAX_JSON_DEPTH - 2)
    _assert_error(_encode(at_limit), ManifestErrorCode.UNKNOWN_FIELD)

    over_limit = _manifest()
    over_limit["private_extension"] = _nested_value(MAX_JSON_DEPTH - 1)
    _assert_error(_encode(over_limit), ManifestErrorCode.DEPTH_LIMIT_EXCEEDED)


def test_collection_limit_accepts_limit_then_rejects_max_plus_one() -> None:
    at_limit = _manifest()
    at_limit["facets"] = [{}] * MAX_COLLECTION_ITEMS
    _assert_error(_encode(at_limit), ManifestErrorCode.INVALID_MANIFEST)

    over_limit = _manifest()
    over_limit["facets"] = [{}] * (MAX_COLLECTION_ITEMS + 1)
    _assert_error(_encode(over_limit), ManifestErrorCode.COLLECTION_LIMIT_EXCEEDED)


def test_vector_dimension_limit_accepts_limit_then_rejects_max_plus_one() -> None:
    at_limit = _manifest()
    at_limit["spaces"][0]["dimensions"] = MAX_VECTOR_DIMENSIONS
    at_limit["vectors"][0]["vector"] = [1.0] + [0.0] * (MAX_VECTOR_DIMENSIONS - 1)
    assert len(decode_prepared_resource_manifest(_encode(at_limit)).vectors[0].vector) == MAX_VECTOR_DIMENSIONS

    over_limit = copy.deepcopy(at_limit)
    over_limit["spaces"][0]["dimensions"] = MAX_VECTOR_DIMENSIONS + 1
    over_limit["vectors"][0]["vector"].append(0.0)
    _assert_error(_encode(over_limit), ManifestErrorCode.VECTOR_LIMIT_EXCEEDED)


def _metadata_with_encoded_size(size: int) -> dict[str, str]:
    empty_size = len(_encode({"v": ""}))
    return {"v": "m" * (size - empty_size)}


def test_metadata_limit_accepts_limit_then_rejects_max_plus_one() -> None:
    at_limit = _manifest()
    at_limit["resource"]["metadata"] = _metadata_with_encoded_size(MAX_METADATA_BYTES)
    assert decode_prepared_resource_manifest(_encode(at_limit)).resource.metadata["v"]

    over_limit = copy.deepcopy(at_limit)
    over_limit["resource"]["metadata"] = _metadata_with_encoded_size(MAX_METADATA_BYTES + 1)
    _assert_error(_encode(over_limit), ManifestErrorCode.METADATA_LIMIT_EXCEEDED)


def test_text_limit_accepts_limit_then_rejects_max_plus_one() -> None:
    at_limit = _manifest()
    at_limit["representations"][0]["text"] = "t" * MAX_TEXT_BYTES
    assert len(decode_prepared_resource_manifest(_encode(at_limit)).representations[0].text or "") == MAX_TEXT_BYTES

    over_limit = _manifest()
    over_limit["representations"][0]["text"] = "t" * (MAX_TEXT_BYTES + 1)
    _assert_error(_encode(over_limit), ManifestErrorCode.TEXT_LIMIT_EXCEEDED)


def test_invalid_graph_is_rejected_before_adapter_transaction() -> None:
    value = _manifest()
    value["units"][0]["representation_id"] = "missing-representation"
    catalog = CatalogSpy()

    with pytest.raises(ManifestError) as caught:
        import_manifest(catalog, _encode(value))

    assert caught.value.code is ManifestErrorCode.INVALID_GRAPH
    assert catalog.calls == []
    assert catalog.transactions_opened == 0


def test_duplicate_graph_ids_are_rejected_before_adapter_transaction() -> None:
    value = _manifest()
    value["representations"].append(copy.deepcopy(value["representations"][0]))
    catalog = CatalogSpy()

    with pytest.raises(ManifestError) as caught:
        import_manifest(catalog, _encode(value))

    assert caught.value.code is ManifestErrorCode.INVALID_GRAPH
    assert catalog.transactions_opened == 0


def test_invalid_vector_graph_is_rejected_before_adapter_transaction() -> None:
    value = _manifest()
    value["spaces"][0]["dimensions"] = 3
    catalog = CatalogSpy()

    with pytest.raises(ManifestError) as caught:
        import_manifest(catalog, _encode(value))

    assert caught.value.code is ManifestErrorCode.INVALID_GRAPH
    assert catalog.transactions_opened == 0


def test_import_never_opens_locator_binary_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external access attempted")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    catalog = CatalogSpy()

    import_manifest(catalog, _encode(_manifest()))

    assert catalog.transactions_opened == 1


def test_manifest_errors_never_echo_untrusted_values() -> None:
    sentinel = "PRIVATE_TEXT_PATH_URL_VECTOR_METADATA_FACET_SENTINEL"
    value = _manifest()
    value[sentinel] = sentinel

    with pytest.raises(ManifestError) as caught:
        decode_prepared_resource_manifest(_encode(value))

    assert sentinel not in str(caught.value)
    assert sentinel not in repr(caught.value)


def test_schema_fixture_matches_runtime_contract_and_limits() -> None:
    schema_path = Path("docs/contracts/prepared-resource-manifest-v1.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["contract"]["const"] == MANIFEST_CONTRACT
    assert schema["properties"]["version"]["const"] == MANIFEST_VERSION
    assert schema["additionalProperties"] is False
    assert schema["x-mdrack-limits"] == {
        "rawBytes": MAX_MANIFEST_BYTES,
        "jsonDepth": MAX_JSON_DEPTH,
        "metadataBytesPerRecord": MAX_METADATA_BYTES,
        "textBytesPerField": MAX_TEXT_BYTES,
        "vectorDimensions": MAX_VECTOR_DIMENSIONS,
        "policy": "reject-not-truncate",
    }
    for name in ("representations", "units", "spaces", "vectors", "facets"):
        assert schema["properties"][name]["maxItems"] == MAX_COLLECTION_ITEMS


def test_application_manifest_module_is_click_free() -> None:
    source = Path("src/mdrack/application/manifest.py").read_text(encoding="utf-8")

    assert "import click" not in source
    assert "mdrack.cli" not in source
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import mdrack.application.manifest; "
            "assert 'click' not in sys.modules; "
            "assert not any(n == 'mdrack.cli' or n.startswith('mdrack.cli.') for n in sys.modules)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
