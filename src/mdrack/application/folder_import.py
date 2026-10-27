"""One explicit mixed-folder action over existing text/image ingestion owners.

The selected roots are caller authority. Catalog locators never grant filesystem
access, and this module never uses the legacy relative-path-only change planner
across roots. Each file is routed to exactly one existing ingestion path and gets
one deterministic resource identity scoped by its portable namespace.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from mdrack.application.media_search import ingest_native_image
from mdrack.application.native_text import ingest_native_text, preflight_native_text_space
from mdrack.domain.embedding_inputs import EmbeddingResult, EncodingRole, PreparedInput
from mdrack.domain.identifiers import logical_id, safe_file_ref
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.indexing.scanner import _ALWAYS_IGNORED, _matches_any
from mdrack.ingestion.image_preprocessing import ImagePreparationError
from mdrack.ingestion.raw_source_provenance import (
    RawSourceError,
    RawSourceErrorCode,
    validate_source_ref,
)
from mdrack.ports.embeddings import EmbeddingError, PreparedEmbeddingProvider
from mdrack_core import PublicationPort, ResourceSnapshot

logger = logging.getLogger(__name__)

FOLDER_IMPORT_SCHEMA = "mdrack.folder-import.v1"
FOLDER_REGISTRY_SCHEMA = "mdrack.folder-roots.v1"
_REGISTRY_FILENAME = "folders.json"
_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TEXT_SUFFIXES: Mapping[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}
_IMAGE_SUFFIXES: Mapping[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_UNCHANGED_REASON = "unchanged"


class FolderImportError(RuntimeError):
    """A fixed, payload-free selection/registry/operation refusal."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class FolderFileResult:
    """One selected file's stable public outcome; never contains an absolute path."""

    namespace: str
    source_ref: str
    kind: str
    media_type: str | None
    status: str
    reason: str | None
    resource_id: str | None = None
    model_calls: int = 0
    source_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "source_ref": self.source_ref,
            "kind": self.kind,
            "media_type": self.media_type,
            "status": self.status,
            "reason": self.reason,
            "resource_id": self.resource_id,
            "model_calls": self.model_calls,
            "source_bytes": self.source_bytes,
        }


@dataclass(frozen=True)
class FolderRootSummary:
    """One namespace's selected-file counts without its local path."""

    namespace: str
    files: int
    added: int
    updated: int
    skipped: int
    failed: int
    unprocessed: int
    excluded: int

    def to_dict(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "files": self.files,
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "unprocessed": self.unprocessed,
            "excluded": self.excluded,
        }


@dataclass(frozen=True)
class FolderImportResult:
    """One completed mixed-folder attempt with honest per-file status."""

    status: Literal["success", "partial_success", "failed"]
    counts: Mapping[str, int]
    roots: tuple[FolderRootSummary, ...]
    files: tuple[FolderFileResult, ...]
    model_calls: int
    space_id: str
    profile_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": FOLDER_IMPORT_SCHEMA,
            "status": self.status,
            "space_id": self.space_id,
            "profile_fingerprint": self.profile_fingerprint,
            "counts": dict(self.counts),
            "roots": [root.to_dict() for root in self.roots],
            "files": [item.to_dict() for item in self.files],
            "model_calls": self.model_calls,
        }


@dataclass(frozen=True)
class _Candidate:
    namespace: str
    source_ref: str
    path: Path
    kind: Literal["document", "image"]
    media_type: str


@dataclass(frozen=True)
class _ScanOutcome:
    candidates: tuple[_Candidate, ...]
    results: tuple[FolderFileResult, ...]
    excluded: int


class _CountingProvider:
    """Count actual prepared-provider encode dispatches; never invent a count."""

    def __init__(self, delegate: PreparedEmbeddingProvider) -> None:
        self._delegate = delegate
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        self.calls += 1
        return await self._delegate.encode(value, role)


def _path_key(value: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(value))))


def _is_link_like(path: Path) -> bool:
    """Refuse symlinks and Windows junctions before following any target."""
    try:
        if path.is_symlink() or os.path.islink(path):
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        path_is_junction = getattr(os.path, "isjunction", None)
        if callable(path_is_junction) and path_is_junction(path):
            return True
        # Windows mount points and other reparse points must not escape the scope
        # merely because Python exposes no dedicated junction predicate for them.
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return False


def _validate_namespace(namespace: object) -> str:
    if not isinstance(namespace, str) or _NAMESPACE_PATTERN.fullmatch(namespace) is None:
        raise FolderImportError("namespace_invalid")
    return namespace


def _validate_selections(
    folders: Mapping[str, Path] | Sequence[tuple[str, Path]],
) -> tuple[tuple[str, Path], ...]:
    items = tuple(folders.items()) if isinstance(folders, Mapping) else tuple(folders)
    if not items:
        raise FolderImportError("folder_selection_required")
    selected: list[tuple[str, Path]] = []
    namespaces: set[str] = set()
    roots: dict[str, str] = {}
    for raw_namespace, raw_path in items:
        namespace = _validate_namespace(raw_namespace)
        if namespace in namespaces:
            raise FolderImportError("namespace_duplicate")
        if not isinstance(raw_path, Path):
            raise FolderImportError("source_root_invalid")
        if _is_link_like(raw_path):
            raise FolderImportError("source_root_link_refused")
        try:
            resolved = raw_path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise FolderImportError("source_root_unavailable") from None
        if not resolved.is_dir():
            raise FolderImportError("source_root_not_directory")
        key = _path_key(resolved)
        if key in roots:
            raise FolderImportError("source_root_duplicate")
        for other_key in roots.values():
            other = Path(other_key)
            if resolved.is_relative_to(other) or other.is_relative_to(resolved):
                raise FolderImportError("source_roots_overlap")
        namespaces.add(namespace)
        roots[key] = key
        selected.append((namespace, resolved))
    return tuple(selected)


def _physical_owner_path(owner: Path, reason: str) -> Path:
    """Resolve an existing junction/symlink ancestry even when the leaf is absent.

    The lexical ``_path_key`` intentionally stays scoped to registry/source
    bookkeeping. Derived owners need physical containment, otherwise a junction
    in an existing ancestor can redirect a not-yet-created store/cache into a
    selected source. Unknown/unresolvable topology refuses with the same safe
    overlap reason rather than guessing.
    """
    try:
        return owner.resolve(strict=False)
    except (OSError, RuntimeError):
        raise FolderImportError(reason) from None


def _validate_storage_owners(
    selections: tuple[tuple[str, Path], ...],
    *,
    store_dir: Path,
    cache_dir: Path,
) -> None:
    owners = (
        (_physical_owner_path(store_dir, "source_overlaps_store"), "source_overlaps_store"),
        (_physical_owner_path(cache_dir, "source_overlaps_cache"), "source_overlaps_cache"),
    )
    for physical_owner, reason in owners:
        for _namespace, root in selections:
            if root.is_relative_to(physical_owner) or physical_owner.is_relative_to(root):
                raise FolderImportError(reason)


def _load_registry(path: Path) -> tuple[tuple[str, str], ...]:
    if not path.exists():
        return ()
    if _is_link_like(path):
        raise FolderImportError("folder_registry_link_refused")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise FolderImportError("folder_registry_invalid") from None
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema", "roots"}
        or raw.get("schema") != FOLDER_REGISTRY_SCHEMA
        or not isinstance(raw.get("roots"), list)
    ):
        raise FolderImportError("folder_registry_invalid")
    entries: list[tuple[str, str]] = []
    namespaces: set[str] = set()
    roots: set[str] = set()
    for item in cast(list[object], raw["roots"]):
        if not isinstance(item, dict) or set(item) != {"namespace", "path"}:
            raise FolderImportError("folder_registry_invalid")
        try:
            namespace = _validate_namespace(item.get("namespace"))
        except FolderImportError:
            raise FolderImportError("folder_registry_invalid") from None
        value = item.get("path")
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise FolderImportError("folder_registry_invalid")
        key = _path_key(value)
        if namespace in namespaces or key in roots:
            raise FolderImportError("folder_registry_invalid")
        namespaces.add(namespace)
        roots.add(key)
        entries.append((namespace, value))
    return tuple(entries)


def _bind_registry(
    entries: tuple[tuple[str, str], ...],
    selections: tuple[tuple[str, Path], ...],
) -> tuple[tuple[str, str], ...]:
    bound = {namespace: value for namespace, value in entries}
    by_root = {_path_key(value): namespace for namespace, value in entries}
    changed = False
    for namespace, root in selections:
        key = _path_key(root)
        existing = bound.get(namespace)
        if existing is not None and _path_key(existing) != key:
            raise FolderImportError("namespace_root_conflict")
        existing_namespace = by_root.get(key)
        if existing_namespace is not None and existing_namespace != namespace:
            raise FolderImportError("root_namespace_conflict")
        if existing is None:
            bound[namespace] = str(root)
            by_root[key] = namespace
            changed = True
    if not changed:
        return entries
    return tuple(sorted(bound.items()))


def _write_registry(path: Path, entries: tuple[tuple[str, str], ...]) -> None:
    payload = {
        "schema": FOLDER_REGISTRY_SCHEMA,
        "roots": [
            {"namespace": namespace, "path": value}
            for namespace, value in entries
        ],
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix="folders-", suffix=".tmp", dir=path.parent, delete=False
        )
        temp_path = Path(handle.name)
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise FolderImportError("folder_registry_write_failed") from None


def _classify(name: str) -> tuple[Literal["document", "image"], str] | None:
    suffix = Path(name).suffix.casefold()
    text_media_type = _TEXT_SUFFIXES.get(suffix)
    if text_media_type is not None:
        return "document", text_media_type
    image_media_type = _IMAGE_SUFFIXES.get(suffix)
    if image_media_type is not None:
        return "image", image_media_type
    return None


def _scan_root(
    namespace: str,
    root: Path,
    *,
    exclude: Sequence[str],
) -> _ScanOutcome:
    candidates: list[_Candidate] = []
    results: list[FolderFileResult] = []
    excluded = 0
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, rel_directory = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: (item.name.casefold(), item.name))
        except OSError:
            results.append(FolderFileResult(
                namespace,
                rel_directory or ".",
                "other",
                None,
                "unprocessed",
                "directory_unavailable",
            ))
            continue
        for entry in entries:
            name = entry.name
            rel = f"{rel_directory}/{name}" if rel_directory else name
            path = Path(entry.path)
            if _is_link_like(path):
                results.append(FolderFileResult(
                    namespace, rel, "other", None, "skipped", "link_not_followed",
                ))
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name in _ALWAYS_IGNORED:
                        continue
                    stack.append((path, rel))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    results.append(FolderFileResult(
                        namespace, rel, "other", None, "skipped", "not_regular_file",
                    ))
                    continue
            except OSError:
                results.append(FolderFileResult(
                    namespace, rel, "other", None, "unprocessed", "source_unavailable",
                ))
                continue
            try:
                validate_source_ref(rel)
            except RawSourceError:
                results.append(FolderFileResult(
                    namespace, rel, "other", None, "skipped", "source_ref_invalid",
                ))
                continue
            if _matches_any(rel, list(exclude)):
                excluded += 1
                continue
            classification = _classify(name)
            if classification is None:
                results.append(FolderFileResult(
                    namespace, rel, "other", None, "skipped", "unsupported_media_type",
                ))
                continue
            kind, media_type = classification
            candidates.append(_Candidate(namespace, rel, path, kind, media_type))
    candidates.sort(key=lambda item: (item.namespace, item.source_ref))
    results.sort(key=lambda item: (item.namespace, item.source_ref, item.reason or ""))
    return _ScanOutcome(tuple(candidates), tuple(results), excluded)


def _derived_resource_id(candidate: _Candidate) -> str:
    kind = "doc" if candidate.kind == "document" else "image"
    return logical_id(kind, candidate.namespace, candidate.source_ref)


def _image_uses_incompatible_space(
    snapshot: ResourceSnapshot,
    space: object,
) -> bool:
    batch = snapshot.batch
    if batch is None:
        return False
    image_unit_ids = {
        unit.unit_id for unit in batch.units if unit.modality == "image"
    }
    used_spaces = {
        vector.space_id for vector in batch.vectors if vector.unit_id in image_unit_ids
    }
    if not used_spaces:
        return False
    return any(
        record.space_id in used_spaces and record != space
        for record in batch.spaces
    )


def _reason_for_exception(error: Exception, kind: str) -> str:
    if isinstance(error, RawSourceError):
        if error.code is RawSourceErrorCode.SIGNATURE_UNSUPPORTED:
            return "source_not_utf8" if kind == "document" else "unsupported_image_format"
        return error.code.value
    if isinstance(error, IncompatibleEmbeddingProfileError):
        return "incompatible_embedding_profile"
    if isinstance(error, ImagePreparationError):
        return error.code
    if isinstance(error, EmbeddingError):
        return "embedding_provider_error"
    if isinstance(error, UnicodeError):
        return "source_not_utf8" if kind == "document" else "source_decode_failed"
    if isinstance(error, OSError):
        return "source_unavailable"
    if isinstance(error, ValueError) and len(error.args) == 1 and isinstance(error.args[0], str):
        stable = {
            "embedding_input_too_large",
            "embedding_space_unavailable",
            "image_publication_conflict",
            "image_rescan_conflict",
            "image_rescan_stale",
            "incompatible_embedding_space",
            "native_text_publication_conflict",
            "native_text_resource_kind_conflict",
            "producer_resource_mismatch",
            "source_changed_without_new_generation",
            "unsupported_embedding_route",
        }
        if error.args[0] in stable:
            return error.args[0]
    return "ingest_failed"


def _overall_status(files: Sequence[FolderFileResult]) -> Literal["success", "partial_success", "failed"]:
    hard = [item for item in files if item.status in {"failed", "unprocessed"}]
    usable = any(
        item.status in {"added", "updated"}
        or (item.status == "skipped" and item.reason == _UNCHANGED_REASON)
        for item in files
    )
    incomplete_skip = any(
        item.status == "skipped" and item.reason != _UNCHANGED_REASON
        for item in files
    )
    if hard:
        return "partial_success" if usable else "failed"
    if incomplete_skip:
        return "partial_success"
    return "success"


def _counts(files: Sequence[FolderFileResult], excluded: int) -> dict[str, int]:
    counts = {
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "unprocessed": 0,
        "excluded": excluded,
    }
    for item in files:
        if item.status in counts:
            counts[item.status] += 1
    counts["total"] = len(files) + excluded
    return counts


def _root_summaries(
    selections: Sequence[tuple[str, Path]],
    files: Sequence[FolderFileResult],
    excluded_by_namespace: Mapping[str, int],
) -> tuple[FolderRootSummary, ...]:
    summaries: list[FolderRootSummary] = []
    for namespace, _root in selections:
        selected = [item for item in files if item.namespace == namespace]
        summaries.append(FolderRootSummary(
            namespace=namespace,
            files=len(selected),
            added=sum(item.status == "added" for item in selected),
            updated=sum(item.status == "updated" for item in selected),
            skipped=sum(item.status == "skipped" for item in selected),
            failed=sum(item.status == "failed" for item in selected),
            unprocessed=sum(item.status == "unprocessed" for item in selected),
            excluded=excluded_by_namespace.get(namespace, 0),
        ))
    return tuple(summaries)


class FolderImportService:
    """Route one or more explicitly selected roots through existing owners."""

    def __init__(
        self,
        provider: PreparedEmbeddingProvider,
        storage: object,
        *,
        config: Any,
        store_dir: Path,
        cache_dir: Path,
    ) -> None:
        self._counter = _CountingProvider(provider)
        self._provider = cast(PreparedEmbeddingProvider, self._counter)
        self._storage = storage
        self._config = config
        self._store_dir = store_dir
        self._cache_dir = cache_dir
        port = getattr(storage, "resource_store", None)
        if not isinstance(port, PublicationPort):
            raise FolderImportError("publication_capability_required")
        self._port = port
        self.registry_path = store_dir / _REGISTRY_FILENAME

    @staticmethod
    def validate_selection(
        folders: Mapping[str, Path] | Sequence[tuple[str, Path]],
        *,
        store_dir: Path,
        cache_dir: Path,
    ) -> tuple[tuple[str, Path], ...]:
        """Validate caller roots before any catalog/store creation or provider work."""
        selections = _validate_selections(folders)
        _validate_storage_owners(
            selections,
            store_dir=store_dir,
            cache_dir=cache_dir,
        )
        return selections

    async def run(
        self,
        folders: Mapping[str, Path] | Sequence[tuple[str, Path]],
        *,
        exclude: Sequence[str] = (),
    ) -> FolderImportResult:
        selections = self.validate_selection(
            folders,
            store_dir=self._store_dir,
            cache_dir=self._cache_dir,
        )
        registry_entries = _load_registry(self.registry_path)
        bound_entries = _bind_registry(registry_entries, selections)
        if bound_entries != registry_entries:
            _write_registry(self.registry_path, bound_entries)

        configured_exclude = tuple(getattr(getattr(self._config, "scan", None), "exclude", ()))
        excluded_patterns = (*configured_exclude, *tuple(exclude))
        candidates: list[_Candidate] = []
        results: list[FolderFileResult] = []
        excluded_by_namespace: dict[str, int] = {}
        for namespace, root in selections:
            scanned = _scan_root(namespace, root, exclude=excluded_patterns)
            candidates.extend(scanned.candidates)
            results.extend(scanned.results)
            excluded_by_namespace[namespace] = scanned.excluded

        text_candidates = [item for item in candidates if item.kind == "document"]
        image_candidates = [item for item in candidates if item.kind == "image"]
        if text_candidates:
            self._provider.capabilities.require(EncodingRole.DOCUMENT, "text")
            preflight_native_text_space(self._storage, self._provider.space)
        if image_candidates:
            self._provider.capabilities.require(EncodingRole.DOCUMENT, "image")

        logger.info(
            "folder.import.started roots=%d candidates=%d skipped=%d",
            len(selections),
            len(candidates),
            sum(item.status == "skipped" for item in results),
        )
        for candidate in candidates:
            result = await self._process_candidate(candidate)
            results.append(result)
            logger.info(
                "folder.file.finished file_ref=%s kind=%s status=%s reason=%s model_calls=%d",
                safe_file_ref(candidate.namespace, candidate.source_ref),
                candidate.kind,
                result.status,
                result.reason,
                result.model_calls,
            )

        results.sort(key=lambda item: (item.namespace, item.source_ref))
        files = tuple(results)
        counts = _counts(files, sum(excluded_by_namespace.values()))
        status = _overall_status(files)
        model_calls = sum(item.model_calls for item in files)
        logger.info(
            "folder.import.finished status=%s added=%d updated=%d skipped=%d failed=%d unprocessed=%d model_calls=%d",
            status,
            counts["added"],
            counts["updated"],
            counts["skipped"],
            counts["failed"],
            counts["unprocessed"],
            model_calls,
        )
        return FolderImportResult(
            status=status,
            counts=counts,
            roots=_root_summaries(selections, files, excluded_by_namespace),
            files=files,
            model_calls=model_calls,
            space_id=self._provider.space.space_id,
            profile_fingerprint=self._provider.space.fingerprint,
        )

    async def _process_candidate(self, candidate: _Candidate) -> FolderFileResult:
        resource_id = _derived_resource_id(candidate)
        try:
            before = self._port.read_snapshot(resource_id)
        except Exception:
            raise FolderImportError("catalog_read_failed") from None
        if before.state.status == "deleted":
            return FolderFileResult(
                candidate.namespace,
                candidate.source_ref,
                candidate.kind,
                candidate.media_type,
                "failed",
                "resource_deleted",
            )
        if candidate.kind == "image" and _image_uses_incompatible_space(
            before, self._provider.space
        ):
            return FolderFileResult(
                candidate.namespace,
                candidate.source_ref,
                candidate.kind,
                candidate.media_type,
                "failed",
                "incompatible_embedding_space",
            )

        calls_before = self._counter.calls
        try:
            if candidate.kind == "document":
                result = await ingest_native_text(
                    self._provider,
                    self._storage,
                    candidate.path,
                    config=self._config,
                    resource_id=resource_id,
                    source_namespace=candidate.namespace,
                    source_ref=candidate.source_ref,
                    media_type=candidate.media_type,
                )
                model_calls = self._counter.calls - calls_before
                if result.unchanged:
                    status, reason = "skipped", _UNCHANGED_REASON
                else:
                    status = "added" if before.state.status == "never_created" else "updated"
                    reason = None
                return FolderFileResult(
                    candidate.namespace,
                    candidate.source_ref,
                    candidate.kind,
                    candidate.media_type,
                    status,
                    reason,
                    resource_id=result.resource_id,
                    model_calls=model_calls,
                    source_bytes=result.source_bytes,
                )
            image = await ingest_native_image(
                self._provider,
                lambda: self._port,
                candidate.path,
                resource_id=resource_id,
                source_namespace=candidate.namespace,
                source_ref=candidate.source_ref,
            )
            model_calls = self._counter.calls - calls_before
            if model_calls == 0 and before.batch is not None:
                status, reason = "skipped", _UNCHANGED_REASON
            else:
                status = "added" if before.state.status == "never_created" else "updated"
                reason = None
            return FolderFileResult(
                candidate.namespace,
                candidate.source_ref,
                candidate.kind,
                image.media_type,
                status,
                reason,
                resource_id=image.resource_id,
                model_calls=model_calls,
                source_bytes=image.byte_size,
            )
        except Exception as error:
            return FolderFileResult(
                candidate.namespace,
                candidate.source_ref,
                candidate.kind,
                candidate.media_type,
                "failed",
                _reason_for_exception(error, candidate.kind),
                model_calls=self._counter.calls - calls_before,
            )


__all__ = [
    "FOLDER_IMPORT_SCHEMA",
    "FOLDER_REGISTRY_SCHEMA",
    "FolderFileResult",
    "FolderImportError",
    "FolderImportResult",
    "FolderImportService",
    "FolderRootSummary",
]
