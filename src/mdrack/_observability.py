"""Closed app event DTOs; core retains ownership of shared safe values and sinks."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from mdrack_core.observability import REDACTED, SAFE_FIELD_NAMES, SafeEvent, _log_safely, _sanitize_value

# Exact event -> field schemas. This is not a general-purpose logging adapter.
_SCHEMAS: dict[str, frozenset[str]] = {}


def _schema(names: str, fields: str) -> None:
    for name in names.split():
        _SCHEMAS[name] = frozenset(fields.split())


_schema("cli.config.failed cli.status.failed cli.doctor.failed cli.storage_analyze.failed "
        "cli.cache.failed cli.ingest.raw_audio.failed cli.ingest.raw_video.failed "
        "cli.ingest.raw_text.failed cli.scan.failed cli.ingest.video.failed cli.ingest.transcript.failed",
        "status reason")
_schema("cli.benchmark.failed cli.metadata.failed cli.find_similar.failed "
        "cli.resources.duplicates.failed cli.resources.similar.failed cli.resources.search.failed "
        "cli.resources.facets.failed cli.image.ingest.failed cli.image.search.failed cli.image.delete.failed "
        "image.embedding.cleanup_failed image.ingest.provider_failed image.ingest.extraction_failed "
        "image.search.degraded video.provider.close_failed transcript.provider.close_failed "
        "embedding.provider.close_failed", "reason")
_schema("cli.image.ingest.started cli.image.ingest.completed", "representation_count")
_schema("cli.image.search.started", "mode limit")
_schema("cli.image.search.completed", "mode result_count")
_schema("cli.image.delete.completed", "status")
_schema("image.ingest.started image.ingest.completed", "representation_count unit_count vector_count byte_size")
_schema("transcript.ingest.started transcript.ingest.completed", "representation_count unit_count vector_count")
_schema("video.compose.started video.compose.completed",
        "representation_count transcript_unit_count frame_unit_count vector_count")
_schema("cli.ingest.video.completed", "status transcript_unit_count frame_unit_count vector_count")
_schema("cli.ingest.transcript.completed", "status unit_count vector_count")
_schema("cli.metadata.show.completed cli.metadata.facets.completed", "facet_count")
_schema("cli.metadata.projection_check.completed", "lexical_path_count facet_path_count diagnostic_count")
_schema("cli.find_similar.completed cli.resources.similar.completed", "result_count degraded")
_schema("cli.resources.duplicates.completed", "result_count")
_schema("config.load.started", "config_ref config_present")
_schema("config.load.finished", "config_ref status section_count config_present")
_schema("config.load.failed", "config_ref status reason")
_schema("config.override.applied", "override_source section field field_known value_present value_kind")
_schema("index.run.started", "run_id")
_schema("index.run.finished", "run_id status file_count files_indexed files_failed chunk_count")
_schema("index.run.failed", "run_id status reason")
_schema("index.file.started", "run_id file_ref")
_schema("index.file.finished", "run_id file_ref chunk_count status")
_schema("index.file.deleted", "run_id file_ref status")
_schema("index.file.failed", "run_id file_ref status reason")
_schema("index.file.delete_failed", "run_id file_ref reason")
_schema("markdown.parse.finished", "run_id file_ref block_count parser status")
_schema("chunk.build.finished", "run_id file_ref block_count chunk_count chunk_strategy status")
for _operation in ("import", "export", "inspect", "delete"):
    _schema(f"cli.resource.{_operation}.failed", "reason")
    _schema(f"cli.resource.{_operation}.completed", "")

_NATIVE_FIELDS = (
    "schema_version request_id attempt_count query_modality encoding_role profile_fingerprint mode target "
    "effective_mode source_bytes prepared_input_bytes provider_call_count extraction_call_count "
    "source_resolution_ms provider_duration_ms read_duration_ms publication_duration_ms preparation_duration_ms "
    "publication_status artifact_status artifact_requested_count artifact_available_count "
    "applicable_branch_count executed_branch_count failed_branch_count unique_unit_count unique_resource_count "
    "result_count degraded degraded_branch_count duration_ms reason candidate_limit_total limit"
)
_schema("media.search.started media.search.completed media.search.failed "
        "media.ingest.started media.ingest.completed media.ingest.failed", _NATIVE_FIELDS)

# App-owned categorical fields admit exact built-in strings only. Unknown labels
# use the shared redaction marker, never interpolated or stringified.
_LABELS = {
    "status": frozenset("started completed success partial_success failed missing".split()),
    "mode": frozenset(("text", "semantic", "hybrid")),
    "effective_mode": frozenset(("text", "semantic", "hybrid")),
    "query_modality": frozenset(("text", "image", "stored_unit")),
    "encoding_role": frozenset(("document", "query", "similarity")),
    "target": frozenset(("unit", "resource")),
    "publication_status": frozenset(("not_requested", "published", "failed")),
    "artifact_status": frozenset(("not_requested", "caller_prepared")),
    "reason": frozenset("""
        cancelled validation_failed unsupported_embedding_route
        config_invalid status_unavailable diagnostics_unavailable storage_analysis_failed
        catalog_benchmark_failed parse_or_io_error operation_failed internal_error
        image_ingest_error image_search_error image_delete_error cleanup_error
        embedding_provider_error visual_embedding_provider_error image_extraction_error semantic_search_error
        similarity_lookup_error scope_invalid resource_duplicate_error resource_similarity_error
        resource_search_error facet_lookup_error verification_unavailable confirmation_missing purge_failed
        source_ref_invalid source_unavailable source_not_regular source_empty source_too_large
        signature_unsupported mime_signature_mismatch duration_limit_exceeded selected_frame_limit_exceeded
        prepared_text_limit_exceeded prepared_evidence_invalid source_changed
        FILE_DECODE_ERROR FILE_IO_ERROR FILE_DELETE_ERROR FILE_INDEX_ERROR CORPUS_SCAN_FAILED
        metadata_show_error metadata_facets_error metadata_projection_error
        metadata_show_failed metadata_facets_failed metadata_projection_check_failed
        invalid_encoding payload_too_large invalid_json duplicate_key non_finite_number depth_limit_exceeded
        unsupported_contract unsupported_version unknown_field collection_limit_exceeded vector_limit_exceeded
        metadata_limit_exceeded text_limit_exceeded invalid_manifest invalid_graph catalog_not_clean
        manifest_unavailable manifest_output_unavailable resource_not_found manifest_too_large
        transcript_unavailable transcript_too_large config_unavailable resource_generation_unavailable
        media_manifest_invalid unknown_format unsupported_schema missing_atoms missing_segments invalid_subtitle
        invalid_root invalid_timing invalid_atom empty_text overlapping_interval
    """.split()),
    "override_source": frozenset(("environment", "cli")),
    "section": frozenset("paths scan parsing metadata cache chunking embedding search profiling unknown".split()),
    "value_kind": frozenset("none boolean number sequence string other".split()),
}
_NUMBERS = frozenset(
    "representation_count unit_count vector_count facet_count result_count "
    "schema_version attempt_count source_bytes prepared_input_bytes provider_call_count extraction_call_count "
    "source_resolution_ms provider_duration_ms read_duration_ms publication_duration_ms preparation_duration_ms "
    "artifact_requested_count artifact_available_count applicable_branch_count "
    "executed_branch_count failed_branch_count "
    "byte_size limit duration_ms transcript_unit_count frame_unit_count lexical_path_count facet_path_count "
    "diagnostic_count section_count file_count files_indexed files_failed chunk_count block_count".split()
)
_BOOLEANS = frozenset("degraded config_present field_known value_present".split())
_OPAQUE = frozenset("config_ref file_ref field parser chunk_strategy".split())


@dataclass(frozen=True)
class AppEvent:
    name: str
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.name) is not str or self.name not in _SCHEMAS:
            raise ValueError("unknown app event")
        if not isinstance(self.fields, Mapping) or set(self.fields) - _SCHEMAS[self.name]:
            raise ValueError("fields outside app event schema")
        core: dict[str, object] = {}
        app: dict[str, object] = {}
        for key, value in self.fields.items():
            if key in _LABELS:
                app[key] = value if value is None or (type(value) is str and value in _LABELS[key]) else REDACTED
            elif key == "profile_fingerprint":
                app[key] = _sanitize_value(value)
            elif key in _NUMBERS:
                if value is not None and type(value) not in (int, float):
                    raise ValueError("app event count must be numeric or unknown")
                # Reuse core finite-number validation rather than a second sanitizer.
                app[key] = _sanitize_value(value)
            elif key in _BOOLEANS:
                if value is not None and type(value) is not bool:
                    raise ValueError("app event flag must be boolean or unknown")
                app[key] = value
            elif key in _OPAQUE:
                # These legacy fields are not licensed as arbitrary public strings.
                app[key] = _sanitize_value(value)
            elif key in SAFE_FIELD_NAMES:
                core[key] = value
            else:
                raise ValueError("app field has no value schema")
        app.update(SafeEvent("core.lifecycle.completed", core).fields)
        object.__setattr__(self, "fields", MappingProxyType(app))

    def to_log_message(self) -> str:
        return self.name + " " + json.dumps(dict(self.fields), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class AppEventLogger:
    """Internal emitter for the finite existing app event vocabulary."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(self, level: int, name: str, args: tuple[object, ...], extra: Mapping[str, object] | None) -> None:
        if args:
            if (
                name not in ("cli.resource.%s.failed", "cli.resource.%s.completed")
                or len(args) != 1
                or type(args[0]) is not str
                or args[0] not in ("import", "export", "inspect", "delete")
            ):
                raise ValueError("unknown app event template")
            name = name % args
        event = AppEvent(name, extra if extra is not None else {})
        _log_safely(self._logger, level, event.to_log_message(), extra=event.fields)

    def info(self, name: str, *args: object, extra: Mapping[str, object] | None = None) -> None:
        self._emit(logging.INFO, name, args, extra)

    def error(self, name: str, *args: object, extra: Mapping[str, object] | None = None) -> None:
        self._emit(logging.ERROR, name, args, extra)

    def warning(self, name: str, *args: object, extra: Mapping[str, object] | None = None) -> None:
        self._emit(logging.WARNING, name, args, extra)

    def debug(self, name: str, *args: object, extra: Mapping[str, object] | None = None) -> None:
        self._emit(logging.DEBUG, name, args, extra)
