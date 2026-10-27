"""W2 typed planning and execution; provider preparation precedes owned reads."""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mdrack._observability import AppEventLogger
from mdrack.application._native_observation import NativeObservation, ObservedProvider
from mdrack.application.source_resolution import SourceResolver
from mdrack.application.vector_values import canonicalize_for_space
from mdrack.domain.embedding_inputs import AudioInput, EmbeddingResult, EncodingRole, ImageInput, TextInput, VideoInput
from mdrack.ingestion.image_preprocessing import ImageProcessPolicy
from mdrack.ingestion.images import (
    ExtractedImageText,
    ImageEmbeddingSpace,
    ImageIngestionResult,
    ImageIngestionService,
    _image_signature_probe,
)
from mdrack.ingestion.raw_source_provenance import RawInputBudget
from mdrack.ports.embeddings import EmbeddingError, EmbeddingHealth, PreparedEmbeddingProvider
from mdrack.ports.search_sessions import SearchReadSessionFactory
from mdrack_core import (
    BranchExecutionError,
    JSONValue,
    LexicalBranch,
    RankedCandidate,
    SearchRequest,
    SearchScope,
    VectorBranch,
)
from mdrack_core.application.retrieval import RetrievalService
from mdrack_core.domain.common import freeze_json_mapping, to_plain_json

logger = AppEventLogger(logging.getLogger(__name__))
_IMAGE_SOURCE_POLICY = ImageProcessPolicy()


@dataclass(frozen=True)
class StoredUnitInput:
    unit_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.unit_id, str) or not self.unit_id:
            raise ValueError("invalid_similarity_basis")


@dataclass(frozen=True)
class MediaSearchRequest:
    query: TextInput | ImageInput | AudioInput | VideoInput | StoredUnitInput
    scope: SearchScope = field(default_factory=SearchScope)
    target: str = "resource"
    mode: str = "hybrid"
    allow_partial: bool = False
    limit: int = 20
    candidate_budget: int = 100
    lexical_weight: float = 1.0
    vector_weight: float = 1.0
    evidence_limit: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.query, (TextInput, ImageInput, AudioInput, VideoInput, StoredUnitInput)):
            raise ValueError("invalid_media_query")
        if not isinstance(self.scope, SearchScope) or self.target not in {"unit", "resource"}:
            raise ValueError("invalid_media_scope")
        if self.mode not in {"text", "semantic", "hybrid"} or type(self.allow_partial) is not bool:
            raise ValueError("invalid_media_policy")
        for value in (self.limit, self.candidate_budget, self.evidence_limit):
            if type(value) is not int or not 1 <= value <= 10000:
                raise ValueError("invalid_media_budget")
        for weight in (self.lexical_weight, self.vector_weight):
            if type(weight) not in (float, int) or not math.isfinite(weight) or weight <= 0:
                raise ValueError("invalid_family_weight")
        if self.mode == "text" and not isinstance(self.query, TextInput):
            raise ValueError("no_applicable_search_branch")


@dataclass(frozen=True)
class MediaSearchResult:
    target: str
    requested_mode: str
    effective_mode: str
    results: tuple[Mapping[str, JSONValue], ...]
    degradations: tuple[Mapping[str, JSONValue], ...]
    space_id: str | None = None
    profile_fingerprint: str | None = None
    basis_unit_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(freeze_json_mapping(i, "media_result") for i in self.results))
        object.__setattr__(self, "degradations", tuple(
            freeze_json_mapping(i, "degradation") for i in self.degradations
        ))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "mdrack.media-search.v1", "target": self.target,
            "requested_mode": self.requested_mode, "effective_mode": self.effective_mode,
            "space_id": self.space_id, "profile_fingerprint": self.profile_fingerprint,
            "basis_unit_id": self.basis_unit_id,
            "score_kind": "rrf", "results": [to_plain_json(i) for i in self.results], "total_count": len(self.results),
            "degraded": bool(self.degradations), "degradations": [to_plain_json(i) for i in self.degradations],
        }


class _ObservedRetrievalService(RetrievalService):
    """Observe core-validated outcomes; core remains the sole executor and fusion owner.

    These app plans have no branch scope overrides, so every submitted branch goes
    through the existing core execution seam, including empty successful results.
    """

    def __init__(self, catalog: Any, observation: NativeObservation) -> None:
        super().__init__(catalog)
        self._observation = observation

    def _execute_branch(
        self, request: SearchRequest, branch: LexicalBranch | VectorBranch,
        operation: Callable[[], list[RankedCandidate]],
    ) -> tuple[RankedCandidate, ...] | BranchExecutionError:
        result = super()._execute_branch(request, branch, operation)
        self._observation.branch_outcome(branch.branch_id, succeeded=not isinstance(result, BranchExecutionError))
        return result


class MediaSearchService:
    def __init__(self, provider: PreparedEmbeddingProvider, *, read_sessions: SearchReadSessionFactory | None,
                 catalog: Any = None) -> None:
        self.provider = provider
        self.read_sessions = read_sessions
        self.catalog = catalog

    def _candidates(self, request: MediaSearchRequest, modality: str) -> tuple[str, ...]:
        # Compatibility belongs to this role/input pair, not to the whole space
        # or unrelated capability rows. Cross-modal targets remain explicit.
        role = EncodingRole.SIMILARITY if isinstance(request.query, StoredUnitInput) else EncodingRole.QUERY
        capability = self.provider.capabilities.require(role, modality, request.scope.modalities)
        return request.scope.modalities or capability.candidate_modalities

    async def search(self, request: MediaSearchRequest) -> MediaSearchResult:
        observation = NativeObservation(
            logger, 'search',
            modality='stored_unit' if isinstance(request.query, StoredUnitInput) else request.query.modality,
            role=EncodingRole.SIMILARITY if isinstance(request.query, StoredUnitInput) else EncodingRole.QUERY,
            fingerprint=self.provider.space.fingerprint, mode=request.mode, target=request.target,
        )
        observation.start(limit=request.limit, candidate_limit_total=request.candidate_budget * (
            int(request.mode != 'semantic' and isinstance(request.query, TextInput)) + int(request.mode != 'text')))
        try:
            result = await self._search(request, observation)
        except BaseException as error:
            observation.finish(error)
            raise
        observation.fields.update(effective_mode=result.effective_mode, result_count=len(result.results),
                                  degraded=bool(result.degradations), degraded_branch_count=len(result.degradations))
        observation.finish()
        return result

    async def _search(self, request: MediaSearchRequest, observation: NativeObservation) -> MediaSearchResult:
        provider = ObservedProvider(self.provider, observation)
        request_id = observation.request_id
        prepared: EmbeddingResult | None = None
        preparation_failure = False
        query = request.query
        # Pure lexical search needs no embedding route. Stored similarity resolves
        # its input modality inside the same read session as its vector and results.
        candidates = (() if request.mode == 'text' or isinstance(query, StoredUnitInput)
                      else self._candidates(request, query.modality))
        if isinstance(query, ImageInput):
            signature = _image_signature_probe(query.content)
            if signature.verified_media_type != query.media_type:
                raise ValueError("mime_signature_mismatch")
            prepare_image = getattr(provider, "prepare_image", None)
            if prepare_image is not None and request.mode != "text":
                provider.capabilities.require(EncodingRole.QUERY, "image", candidates)
                query = prepare_image(query.content, query.media_type, query.identity)
        if request.mode == 'text':
            observation.plan_branches('lexical')
        if isinstance(query, StoredUnitInput) and not provider.capabilities.reuse_document_vector:
            raise ValueError("similarity_role_unsupported")
        if request.mode != "text" and not isinstance(query, StoredUnitInput):
            # Unsupported policy is a refusal, not a partial provider failure.
            provider.capabilities.validate(query, EncodingRole.QUERY, candidates)
            observation.plan_branches(*(
                ('lexical', 'vector') if isinstance(query, TextInput) and request.mode == 'hybrid' else ('vector',)
            ))
            try:
                prepared = await provider.encode(query, EncodingRole.QUERY)
                if prepared.space != provider.space:
                    raise ValueError("incompatible_embedding_space")
            except Exception:
                observation.branch_outcome('vector', succeeded=False)
                if not request.allow_partial or not isinstance(query, TextInput) or request.mode != "hybrid":
                    raise EmbeddingError("embedding_provider_error") from None
                preparation_failure = True
        context = self.read_sessions.open() if self.read_sessions is not None else nullcontext(None)
        with observation.stage('read_duration_ms'), context as session:
            catalog = session.catalog if session is not None else self.catalog
            if isinstance(query, StoredUnitInput):
                if not self.provider.capabilities.reuse_document_vector:
                    raise ValueError("similarity_role_unsupported")
                unit = catalog.read_unit(query.unit_id)
                if unit is None:
                    raise ValueError("similarity_basis_unavailable")
                candidates = self._candidates(request, unit.modality)
                provider.capabilities.require(EncodingRole.SIMILARITY, unit.modality, candidates)
                observation.plan_branches('vector')
                vector = catalog.read_vector(query.unit_id, provider.space.space_id)
                if vector is None:
                    raise ValueError("similarity_basis_unavailable")
                prepared = EmbeddingResult(vector.vector, provider.space)
            lexical: tuple[LexicalBranch, ...] = ()
            vectors: tuple[VectorBranch, ...] = ()
            if isinstance(query, TextInput) and request.mode != "semantic":
                lexical = (LexicalBranch("lexical", query.text, request.lexical_weight, request.candidate_budget),)
            if prepared is not None:
                spaces = catalog.resolve_embedding_spaces(
                    fingerprint=prepared.space.fingerprint, dimensions=prepared.space.dimensions,
                )
                if not any(space == prepared.space for space in spaces):
                    observation.branch_outcome('vector', succeeded=False)
                    if not request.allow_partial or not lexical:
                        raise ValueError("embedding_space_unavailable")
                    preparation_failure = True
                else:
                    vector = canonicalize_for_space(prepared.vector, prepared.space)
                    vectors = (VectorBranch("vector", prepared.space.space_id, vector, request.vector_weight,
                                            request.candidate_budget, prepared.space.fingerprint),)
            if not lexical and not vectors:
                raise ValueError("no_applicable_search_branch")
            # Enforce the admitted route in Core/SQLite before candidate limits,
            # retaining every other caller filter. Lexical-only policy is unchanged.
            scope = (replace(request.scope, modalities=candidates)
                     if request.mode != 'text' and not request.scope.modalities else request.scope)
            plan = SearchRequest(lexical, vectors, scope, request.target, request.limit,
                                 evidence_limit_per_resource=request.evidence_limit,
                                 allow_partial=request.allow_partial, request_id=request_id)
            result = _ObservedRetrievalService(catalog, observation).search(plan)
            degradations = tuple({"branch_id": d.branch_id, "category": d.category.value} for d in result.degradations)
            if preparation_failure:
                degradations += ({"branch_id": "vector", "category": "embedding_provider_error"},)
            failed = {d["branch_id"] for d in degradations}
            observation.fields.update(
                unique_resource_count=len({item.resource_id for item in result.items}),
                unique_unit_count=len({e.unit_id for item in result.items for e in item.evidence}),
            )
            effective = "hybrid" if lexical and vectors and not failed else (
                "text" if lexical and "lexical" not in failed else "semantic"
            )
            items: list[Mapping[str, JSONValue]] = []
            for item in result.items:
                evidence: list[JSONValue] = []
                for candidate in item.evidence:
                    unit = catalog.read_unit(candidate.unit_id)
                    resource = catalog.read_resource(candidate.resource_id)
                    if unit is None or resource is None:
                        raise ValueError("media_hydration_failed")
                    provenance = resource.metadata.get("provenance", {})
                    revision = provenance.get("raw_source_sha256") if isinstance(provenance, Mapping) else None
                    evidence.append({
                        "resource_id": resource.resource_id, "unit_id": unit.unit_id,
                        "representation_id": unit.representation_id,
                        "representation_kind": candidate.metadata.get(
                            "representation_kind", unit.metadata.get("representation_kind"),
                        ),
                        "modality": unit.modality, "text": unit.text,
                        "locator": {"kind": unit.evidence_locator.kind, "payload": dict(unit.evidence_locator.payload)},
                        "source_revision": revision,
                    })
                items.append({"resource_id": item.resource_id, "unit_id": item.unit_id,
                              "rank": item.rank, "score": item.score, "evidence": tuple(evidence)})
            vector_executed = bool(vectors) and "vector" not in failed
            return MediaSearchResult(
                request.target, request.mode, effective, tuple(items), degradations,
                prepared.space.space_id if prepared is not None and vector_executed else None,
                prepared.space.fingerprint if prepared is not None and vector_executed else None,
                query.unit_id if isinstance(query, StoredUnitInput) else None,
            )


def _image_preparation(provider: PreparedEmbeddingProvider) -> dict[str, JSONValue] | None:
    """Read the optional image-specific readiness hook without changing generic providers."""
    value: object = getattr(provider, "image_preparation", None)
    if callable(value):
        value = value()
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    preprocessor = getattr(provider, "preprocessor", None)
    identity = getattr(preprocessor, "identity", None)
    if callable(identity):
        result = identity()
        if isinstance(result, Mapping):
            return {str(key): item for key, item in result.items()}
    return None


class _VisualAdapter:
    def __init__(self, provider: PreparedEmbeddingProvider) -> None:
        self.provider = provider

    async def embed_image(self, content: bytes, *, profile: str = "default") -> tuple[float, ...]:
        from mdrack.ingestion.images import _image_signature_probe
        from mdrack.ingestion.raw_source_provenance import sha256_digest
        media_type = _image_signature_probe(content).verified_media_type
        revision = sha256_digest(content)
        prepare_image = getattr(self.provider, "prepare_image", None)
        value = (prepare_image(content, media_type, revision) if prepare_image is not None
                 else ImageInput(content, media_type, revision))
        self.provider.capabilities.validate(value, EncodingRole.DOCUMENT)
        result = await self.provider.encode(value, EncodingRole.DOCUMENT)
        if result.space != self.provider.space:
            raise ValueError("incompatible_embedding_space")
        return result.vector


class _PreparedTextAdapter:
    def __init__(self, provider: PreparedEmbeddingProvider) -> None:
        self.provider = provider

    @property
    def dimensions(self) -> int:
        return self.provider.space.dimensions

    async def _encode(self, text: str, role: EncodingRole) -> list[float]:
        from mdrack.ingestion.raw_source_provenance import sha256_digest
        value = TextInput(text, sha256_digest(text))
        self.provider.capabilities.validate(value, role)
        result = await self.provider.encode(value, role)
        if result.space != self.provider.space:
            raise ValueError("incompatible_embedding_space")
        return list(result.vector)

    async def embed(self, texts: list[str], profile: str = "default") -> list[list[float]]:
        return [await self._encode(text, EncodingRole.DOCUMENT) for text in texts]

    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        return await self._encode(text, EncodingRole.QUERY)

    async def health(self) -> EmbeddingHealth:
        raise NotImplementedError("prepared_provider_health_not_available")


async def _ingest_native_image(provider: PreparedEmbeddingProvider, catalog_factory: Callable[[], object],
                              path: Path | None, *,
                              resource_id: str, source_namespace: str, source_ref: str,
                              expected_revision: str | None = None,
                              resolver: SourceResolver | None = None,
                              derived_text: tuple[ExtractedImageText, ...] = (),
                              derived_lexical: bool = True, derived_dense: bool = False,
                              _observation: NativeObservation) -> ImageIngestionResult:
    # This must precede both source resolution and catalog creation.
    provider.capabilities.require(EncodingRole.DOCUMENT, "image")
    if type(derived_lexical) is not bool or type(derived_dense) is not bool:
        raise ValueError("invalid_derived_text_policy")
    if type(derived_text) is not tuple or any(not isinstance(i, ExtractedImageText) for i in derived_text):
        raise ValueError("invalid_prepared_text")
    _observation.fields.update(
        artifact_requested_count=len(derived_text), artifact_available_count=len(derived_text),
        artifact_status='caller_prepared' if derived_text else 'not_requested')
    if derived_dense:
        for artifact in derived_text:
            provider.capabilities.validate(TextInput(artifact.text, "prepared-artifact"), EncodingRole.DOCUMENT)
    budget = RawInputBudget(max_source_bytes=_IMAGE_SOURCE_POLICY.max_source_bytes)
    resolver = resolver or SourceResolver()
    # Time entry separately; do not include provider/publication time in source resolution.
    from contextlib import ExitStack
    with ExitStack() as stack:
        with _observation.stage('source_resolution_ms'):
            selected, snapshot, value = stack.enter_context(resolver.image(
                namespace=source_namespace, source_ref=source_ref, selected=path,
                expected_revision=expected_revision, budget=budget))
        _observation.fields['source_bytes'] = snapshot.provenance.byte_size
        if getattr(provider, 'prepare_image', None) is None:
            provider.capabilities.validate(value, EncodingRole.DOCUMENT)
        record = provider.space
        space = ImageEmbeddingSpace(record.space_id, record.dimensions, record.fingerprint,
                                    record.metric, canonical_record=record)
        service = ImageIngestionService(
            catalog_factory(), visual_embedding_provider=_VisualAdapter(provider), visual_space=space,
            text_embedding_provider=_PreparedTextAdapter(provider) if derived_dense else None,
            text_space=space if derived_dense else None, prepared_texts=derived_text,
            derived_lexical=derived_lexical, derived_dense=derived_dense,
            max_image_bytes=_IMAGE_SOURCE_POLICY.max_source_bytes,
            visual_preparation=_image_preparation(provider),
            conditional_publication=True,
            _publication_observer=_observation.publication,
        )
        return await service.ingest(selected, resource_id=resource_id, source_namespace=source_namespace,
                                    source_ref=source_ref, _snapshot=snapshot)


async def ingest_native_image(provider: PreparedEmbeddingProvider, catalog_factory: Callable[[], object],
                              path: Path | None, *, resource_id: str, source_namespace: str, source_ref: str,
                              expected_revision: str | None = None, resolver: SourceResolver | None = None,
                              derived_text: tuple[ExtractedImageText, ...] = (),
                              derived_lexical: bool = True, derived_dense: bool = False) -> ImageIngestionResult:
    observation = NativeObservation(logger, 'ingest', modality='image', role=EncodingRole.DOCUMENT,
                                    fingerprint=provider.space.fingerprint)
    observation.start()
    try:
        result = await _ingest_native_image(
            ObservedProvider(provider, observation), catalog_factory, path, resource_id=resource_id,
            source_namespace=source_namespace, source_ref=source_ref, expected_revision=expected_revision,
            resolver=resolver, derived_text=derived_text, derived_lexical=derived_lexical,
            derived_dense=derived_dense, _observation=observation)
    except BaseException as error:
        observation.finish(error)
        raise
    observation.fields.update(result_count=1, unique_resource_count=1, unique_unit_count=len(result.unit_ids))
    observation.finish()
    return result
