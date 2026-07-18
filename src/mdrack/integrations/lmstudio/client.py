"""LM Studio embedding provider and model control client.

This module provides the OpenAI-compatible embeddings provider and a
small native LM Studio control layer for model lifecycle requests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from mdrack.domain.profiles import CapabilityStatus
from mdrack.ports.embeddings import EmbeddingError, EmbeddingHealth

logger = logging.getLogger(__name__)

_KNOWN_API_SUFFIXES = (
    "/v1/embeddings",
    "/v1/models",
    "/v1",
    "/api/v1/models/download/status",
    "/api/v1/models/download",
    "/api/v1/models/load",
    "/api/v1/models/unload",
    "/api/v1/models",
    "/api/v1",
)


@dataclass(frozen=True)
class LMStudioModelInfo:
    """Summary of one model known to LM Studio."""

    key: str
    state: str | None
    loaded: bool
    display_name: str | None = None
    model_type: str | None = None
    publisher: str | None = None
    selected_variant: str | None = None
    variants: tuple[str, ...] = ()
    instance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LMStudioLoadedModelInfo:
    """Summary of one loaded LM Studio model instance."""

    key: str
    instance_id: str | None
    state: str | None


@dataclass(frozen=True)
class LMStudioDownloadInfo:
    """Summary of one LM Studio model download task."""

    key: str | None
    status: str | None
    progress: float | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class LMStudioLoadResult:
    """Result returned after requesting a model load."""

    key: str
    state: str | None
    instance_id: str | None = None


@dataclass(frozen=True)
class LMStudioDownloadRequest:
    """Result returned after requesting a model download."""

    key: str
    status: str | None
    download_id: str | None = None


class LMStudioControlError(EmbeddingError):
    """Raised when LM Studio model management requests fail."""


def _log_event(level: int, event: str, **fields: object) -> None:
    message = event
    values: list[object] = []
    for key, value in fields.items():
        message += f" {key}=%s"
        values.append(value)
    logger.log(level, message, *values)


def _strip_known_api_suffix(endpoint: str) -> str:
    raw = endpoint.rstrip("/")
    for suffix in _KNOWN_API_SUFFIXES:
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def _normalize_endpoints(endpoint: str) -> tuple[str, str, str]:
    """Return canonical OpenAI and native LM Studio API endpoints."""
    raw = _strip_known_api_suffix(endpoint)

    parts = urlsplit(raw)
    base_path = parts.path.rstrip("/")
    openai_path = f"{base_path}/v1" if base_path else "/v1"
    control_path = f"{base_path}/api/v1" if base_path else "/api/v1"
    openai_base = urlunsplit((parts.scheme, parts.netloc, openai_path, "", ""))
    control_base = urlunsplit((parts.scheme, parts.netloc, control_path, "", ""))
    return openai_base, f"{openai_base}/embeddings", control_base


def _normalize_endpoint(endpoint: str) -> tuple[str, str]:
    """Return canonical LM Studio API base and embeddings URL."""
    api_base, embeddings_url, _ = _normalize_endpoints(endpoint)
    return api_base, embeddings_url


def _first_string(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "loaded", "active", "running"}:
            return True
        if lowered in {"false", "0", "no", "not-loaded", "idle", "unloaded"}:
            return False
    return None


def _extract_model_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or payload.get("items")
    else:
        items = None

    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise LMStudioControlError("Invalid LM Studio model list response")

    return items


def _extract_download_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if "downloads" in payload:
            items = payload.get("downloads")
        elif "data" in payload:
            items = payload.get("data")
        else:
            items = [payload]
    else:
        items = None

    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise LMStudioControlError("Invalid LM Studio download status response")

    return items


def _extract_instance_ids(model_payload: dict[str, Any]) -> tuple[str, ...]:
    candidates = (
        model_payload.get("instances"),
        model_payload.get("loaded_instances"),
        model_payload.get("model_instances"),
    )
    for value in candidates:
        if not isinstance(value, list):
            continue
        instance_ids: list[str] = []
        for item in value:
            if isinstance(item, str) and item:
                instance_ids.append(item)
                continue
            if isinstance(item, dict):
                instance_id = _first_string(item, "instance_id", "model_instance_id", "id")
                if instance_id is not None:
                    instance_ids.append(instance_id)
        if instance_ids:
            return tuple(instance_ids)

    instance_id = _first_string(model_payload, "instance_id", "model_instance_id")
    if instance_id is not None:
        return (instance_id,)

    return ()


def _parse_model_info(model_payload: dict[str, Any]) -> LMStudioModelInfo:
    key = _first_string(model_payload, "key", "id", "model", "model_id", "identifier")
    if key is None:
        raise LMStudioControlError("Invalid LM Studio model list item: missing model identifier")

    state = _first_string(model_payload, "state", "status")
    instance_ids = _extract_instance_ids(model_payload)
    loaded_flag = _coerce_bool(model_payload.get("loaded"))
    loaded = bool(instance_ids) or loaded_flag is True or state in {"loaded", "active", "running"}

    variants_raw = model_payload.get("variants")
    variants = tuple(item for item in variants_raw if isinstance(item, str)) if isinstance(variants_raw, list) else ()

    return LMStudioModelInfo(
        key=key,
        state=state,
        loaded=loaded,
        display_name=_first_string(model_payload, "display_name", "name"),
        model_type=_first_string(model_payload, "type"),
        publisher=_first_string(model_payload, "publisher", "owned_by"),
        selected_variant=_first_string(model_payload, "selected_variant"),
        variants=variants,
        instance_ids=instance_ids,
    )


def _parse_download_info(download_payload: dict[str, Any]) -> LMStudioDownloadInfo:
    return LMStudioDownloadInfo(
        key=_first_string(download_payload, "model", "model_id", "id", "identifier"),
        status=_first_string(download_payload, "status", "state"),
        progress=_coerce_float(
            download_payload.get("progress")
            or download_payload.get("progress_percent")
            or download_payload.get("progress_percentage")
        ),
        downloaded_bytes=_coerce_int(
            download_payload.get("downloaded_bytes") or download_payload.get("downloaded")
        ),
        total_bytes=_coerce_int(download_payload.get("total_bytes") or download_payload.get("total")),
        error=_first_string(download_payload, "error", "message"),
    )


class LMStudioControlClient:
    """Native LM Studio control API client for model lifecycle requests."""

    def __init__(self, endpoint: str, timeout: int = 30) -> None:
        self._provider_name = "lmstudio"
        self._openai_endpoint, self._embeddings_url, self._endpoint = _normalize_endpoints(endpoint)
        self._timeout = timeout

    async def close(self) -> None:
        """Close control client resources.

        The client creates short-lived HTTP clients per request, so there is
        nothing persistent to close here.
        """
        return None

    async def list_models(self) -> list[LMStudioModelInfo]:
        """Return models known to LM Studio."""
        payload = await self._request_json(
            method="GET",
            path="/models",
            operation="list_models",
        )
        return [_parse_model_info(item) for item in _extract_model_items(payload)]

    async def list_loaded_models(self) -> list[LMStudioLoadedModelInfo]:
        """Return loaded LM Studio model instances."""
        loaded_models: list[LMStudioLoadedModelInfo] = []
        for model in await self.list_models():
            if not model.loaded:
                continue
            if model.instance_ids:
                loaded_models.extend(
                    LMStudioLoadedModelInfo(
                        key=model.key,
                        instance_id=instance_id,
                        state=model.state,
                    )
                    for instance_id in model.instance_ids
                )
                continue
            loaded_models.append(
                LMStudioLoadedModelInfo(
                    key=model.key,
                    instance_id=None,
                    state=model.state,
                )
            )
        return loaded_models

    async def download_model(self, model: str) -> LMStudioDownloadRequest:
        """Request an LM Studio model download."""
        payload = await self._request_json(
            method="POST",
            path="/models/download",
            operation="download_model",
            payload={"model": model},
            model=model,
        )
        response_payload = payload if isinstance(payload, dict) else {}
        return LMStudioDownloadRequest(
            key=_first_string(response_payload, "model", "model_id", "id", "identifier") or model,
            status=_first_string(response_payload, "status", "state"),
            download_id=_first_string(response_payload, "download_id", "id"),
        )

    async def get_download_status(self) -> list[LMStudioDownloadInfo]:
        """Return current LM Studio model download status."""
        payload = await self._request_json(
            method="GET",
            path="/models/download/status",
            operation="download_status",
        )
        return [_parse_download_info(item) for item in _extract_download_items(payload)]

    async def load_model(self, model: str) -> LMStudioLoadResult:
        """Request an LM Studio model load."""
        payload = await self._request_json(
            method="POST",
            path="/models/load",
            operation="load_model",
            payload={"model": model},
            model=model,
        )
        response_payload = payload if isinstance(payload, dict) else {}
        return LMStudioLoadResult(
            key=_first_string(response_payload, "model", "model_id", "id", "identifier") or model,
            state=_first_string(response_payload, "state", "status") or "loaded",
            instance_id=_first_string(response_payload, "instance_id", "model_instance_id"),
        )

    async def unload_model(self, instance_id: str) -> None:
        """Request an LM Studio model unload by instance id."""
        await self._request_json(
            method="POST",
            path="/models/unload",
            operation="unload_model",
            payload={"instance_id": instance_id},
            expect_json=False,
            instance_id_present=True,
        )

    async def probe_embedding_dimensions(self, model: str, text: str = "health check") -> int:
        """Probe an embedding model and return the actual vector dimension."""
        payload = await self._request_json(
            method="POST",
            path="/embeddings",
            operation="probe_embedding_dimensions",
            payload={"model": model, "input": [text]},
            model=model,
            use_openai_api=True,
        )
        if not isinstance(payload, dict) or "data" not in payload:
            raise LMStudioControlError("Invalid LM Studio probe response")
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise LMStudioControlError("Invalid LM Studio probe response")
        vector = data[0].get("embedding")
        if not isinstance(vector, list):
            raise LMStudioControlError("Invalid LM Studio probe embedding payload")
        return len(vector)

    @property
    def endpoint(self) -> str:
        """Return the canonical LM Studio native API base URL."""
        return self._endpoint

    @property
    def openai_endpoint(self) -> str:
        """Return the canonical OpenAI-compatible API base URL."""
        return self._openai_endpoint

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        expect_json: bool = True,
        use_openai_api: bool = False,
        **safe_fields: object,
    ) -> Any:
        base_url = self._openai_endpoint if use_openai_api else self._endpoint
        url = f"{base_url}{path}"
        started_at = perf_counter()
        _log_event(
            logging.INFO,
            "llm.control.request.started",
            provider=self._provider_name,
            operation=operation,
            method=method,
            **safe_fields,
        )

        request_kwargs: dict[str, Any] = {}
        if payload is not None:
            request_kwargs["json"] = payload

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if method == "GET":
                    response = await client.get(url, **request_kwargs)
                else:
                    response = await client.post(url, **request_kwargs)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            _log_event(
                logging.ERROR,
                "llm.control.request.failed",
                provider=self._provider_name,
                operation=operation,
                method=method,
                reason="timeout",
                elapsed_ms=int((perf_counter() - started_at) * 1000),
                **safe_fields,
            )
            raise LMStudioControlError(f"Timeout calling LM Studio {operation} endpoint") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            _log_event(
                logging.ERROR,
                "llm.control.request.failed",
                provider=self._provider_name,
                operation=operation,
                method=method,
                reason="http_status",
                status_code=status_code,
                elapsed_ms=int((perf_counter() - started_at) * 1000),
                **safe_fields,
            )
            raise LMStudioControlError(
                f"LM Studio returned HTTP {status_code} for {operation} request"
            ) from exc
        except httpx.RequestError as exc:
            _log_event(
                logging.ERROR,
                "llm.control.request.failed",
                provider=self._provider_name,
                operation=operation,
                method=method,
                reason="request_error",
                error_type=type(exc).__name__,
                elapsed_ms=int((perf_counter() - started_at) * 1000),
                **safe_fields,
            )
            raise LMStudioControlError(
                f"Failed to reach LM Studio {operation} endpoint"
            ) from exc

        _log_event(
            logging.INFO,
            "llm.control.request.finished",
            provider=self._provider_name,
            operation=operation,
            method=method,
            status_code=response.status_code,
            elapsed_ms=int((perf_counter() - started_at) * 1000),
            **safe_fields,
        )

        if not expect_json:
            return None

        try:
            return response.json()
        except Exception as exc:
            _log_event(
                logging.ERROR,
                "llm.control.response.failed",
                provider=self._provider_name,
                operation=operation,
                method=method,
                reason="invalid_json",
                status_code=response.status_code,
                elapsed_ms=int((perf_counter() - started_at) * 1000),
                **safe_fields,
            )
            raise LMStudioControlError(
                f"Failed to parse LM Studio {operation} response"
            ) from exc


class LMStudioProvider:
    """Embedding provider that calls LM Studio HTTP API.

    Implements the EmbeddingProvider protocol using the OpenAI-compatible
    /v1/embeddings endpoint exposed by LM Studio.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        dimensions: int,
        timeout: int = 30,
        *,
        requested_dimensions: int | None = None,
        dimensions_capability: CapabilityStatus = "not_tested",
        native_dimensions: int | None = None,
    ) -> None:
        """Initialize the LM Studio provider.

        Args:
            endpoint: Base URL of the LM Studio server (e.g. "http://localhost:1234").
            model: Model name to use for embeddings.
            dimensions: Expected returned embedding dimension size.
            timeout: HTTP request timeout in seconds (default: 30).
            requested_dimensions: Optional dimension sent to the runtime only
                when ``dimensions_capability`` is ``tested``.
            dimensions_capability: Evidence state for the runtime's dimensions
                request parameter. Configuration alone is not live evidence.
            native_dimensions: Known full model output dimension. Reduced MRL
                is not claimed unless this is greater than the request.
        """
        self._provider_name = "lmstudio"
        self._model = model
        self._model_name = model
        self._endpoint, self._embeddings_url, _ = _normalize_endpoints(endpoint)
        self._dimensions = dimensions
        self._requested_dimensions = requested_dimensions
        self._dimensions_capability = dimensions_capability
        self._native_dimensions = native_dimensions
        self._returned_dimensions: int | None = None
        self._vector_length_valid: bool | None = None
        self._timeout = timeout
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        if requested_dimensions is not None and requested_dimensions < 1:
            raise ValueError("requested_dimensions must be positive")
        if native_dimensions is not None and native_dimensions < 1:
            raise ValueError("native_dimensions must be positive")

    async def close(self) -> None:
        """Close provider resources.

        The provider creates short-lived clients per request, so there is
        nothing persistent to close here.
        """
        return None

    async def embed(
        self, texts: list[str], profile: str = "default"
    ) -> list[list[float]]:
        """Embed a batch of texts via LM Studio API.

        Args:
            texts: List of text strings to embed.
            profile: Embedding profile name (ignored for LM Studio).

        Returns:
            List of embedding vectors, one per input text.

        Raises:
            EmbeddingError: On timeout, connection error, HTTP error,
                or dimension mismatch.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
        }
        if self._requested_dimensions is not None:
            if self._dimensions_capability != "tested":
                reason = f"requested_dimensions_{self._dimensions_capability}"
                logger.error(
                    "llm.request.failed provider=%s model=%s profile=%s reason=%s "
                    "requested_dimensions=%d calls_attempted=0",
                    self._provider_name,
                    self._model,
                    profile,
                    reason,
                    self._requested_dimensions,
                )
                raise EmbeddingError(reason)
            payload["dimensions"] = self._requested_dimensions
        started_at = perf_counter()
        logger.info(
            "llm.request.started provider=%s model=%s profile=%s input_count=%d",
            self._provider_name,
            self._model,
            profile,
            len(texts),
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._embeddings_url, json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=timeout "
                "input_count=%d elapsed_ms=%d",
                self._provider_name,
                self._model,
                profile,
                len(texts),
                int((perf_counter() - started_at) * 1000),
            )
            raise EmbeddingError("Timeout calling LM Studio embeddings endpoint") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=http_status "
                "status_code=%s input_count=%d elapsed_ms=%d",
                self._provider_name,
                self._model,
                profile,
                status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
            )
            raise EmbeddingError(
                f"LM Studio returned HTTP {status_code} for embeddings request"
            ) from exc
        except httpx.RequestError as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=request_error "
                "error_type=%s input_count=%d elapsed_ms=%d",
                self._provider_name,
                self._model,
                profile,
                type(exc).__name__,
                len(texts),
                int((perf_counter() - started_at) * 1000),
            )
            raise EmbeddingError("Failed to reach LM Studio embeddings endpoint") from exc

        try:
            data = response.json()
        except Exception as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=invalid_json "
                "status_code=%d input_count=%d elapsed_ms=%d",
                self._provider_name,
                self._model,
                profile,
                response.status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
            )
            raise EmbeddingError("Failed to parse LM Studio response") from exc

        if "data" not in data:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s "
                "reason=missing_data_field status_code=%d input_count=%d elapsed_ms=%d",
                self._provider_name,
                self._model,
                profile,
                response.status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
            )
            raise EmbeddingError("Invalid response: missing 'data' field")

        try:
            embeddings = [item["embedding"] for item in data["data"]]
        except (KeyError, TypeError) as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s "
                "reason=invalid_embedding_structure status_code=%d input_count=%d elapsed_ms=%d",
                self._provider_name,
                self._model,
                profile,
                response.status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
            )
            raise EmbeddingError("Invalid embedding response structure") from exc

        returned_lengths = [len(embedding) for embedding in embeddings]
        self._returned_dimensions = returned_lengths[0] if returned_lengths else None
        self._vector_length_valid = bool(returned_lengths) and all(
            length == self._dimensions for length in returned_lengths
        )

        # Validate complete vectors locally. Never truncate provider output.
        for i, emb in enumerate(embeddings):
            if len(emb) != self._dimensions:
                logger.error(
                    "llm.request.failed provider=%s model=%s profile=%s "
                    "reason=dimension_mismatch expected_dims=%d actual_dims=%d text_index=%d "
                    "status_code=%d input_count=%d elapsed_ms=%d",
                    self._provider_name,
                    self._model,
                    profile,
                    self._dimensions,
                    len(emb),
                    i,
                    response.status_code,
                    len(texts),
                    int((perf_counter() - started_at) * 1000),
                )
                raise EmbeddingError(
                    f"Dimension mismatch: expected {self._dimensions}, "
                    f"got {len(emb)} for text index {i}"
                )

        logger.info(
            "llm.request.finished provider=%s model=%s profile=%s input_count=%d "
            "vector_count=%d dims=%d status_code=%d elapsed_ms=%d",
            self._provider_name,
            self._model,
            profile,
            len(texts),
            len(embeddings),
            self._dimensions,
            response.status_code,
            int((perf_counter() - started_at) * 1000),
        )

        return embeddings

    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        """Embed a single query text with retrieval prefix.

        Args:
            text: Query text to embed.
            profile: Embedding profile name (ignored for LM Studio).

        Returns:
            Embedding vector for the query.
        """
        prefixed = f"Represent this document for retrieval: {text}"
        vectors = await self.embed([prefixed], profile)
        return vectors[0]

    async def health(self) -> EmbeddingHealth:
        """Check LM Studio health by attempting a small embedding.

        Returns:
            EmbeddingHealth with provider status information.
        """
        try:
            await self.embed(["health check"])
            return EmbeddingHealth(
                ok=True,
                provider="lmstudio",
                model=self._model,
                dimensions=self._dimensions,
                error=None,
                requested_dimensions=self._requested_dimensions,
                returned_dimensions=self._returned_dimensions,
                vector_length_valid=self._vector_length_valid,
                mrl_status=self.mrl_status,
            )
        except Exception:
            return EmbeddingHealth(
                ok=False,
                provider="lmstudio",
                model=self._model,
                dimensions=self._dimensions,
                error="provider_unavailable",
                requested_dimensions=self._requested_dimensions,
                returned_dimensions=self._returned_dimensions,
                vector_length_valid=self._vector_length_valid,
                mrl_status=self.mrl_status,
            )

    @property
    def dimensions(self) -> int:
        """Return the configured embedding dimension size."""
        return self._dimensions

    @property
    def requested_dimensions(self) -> int | None:
        """Return the dimension explicitly requested from the runtime, if any."""
        return self._requested_dimensions

    @property
    def returned_dimensions(self) -> int | None:
        """Return the last dimension actually returned by the runtime."""
        return self._returned_dimensions

    @property
    def vector_length_valid(self) -> bool | None:
        """Return local validation for the most recent response."""
        return self._vector_length_valid

    @property
    def mrl_status(self) -> str:
        """Report MRL as tested only for matching explicit runtime evidence."""
        if (
            self._requested_dimensions is not None
            and self._dimensions_capability == "tested"
            and self._native_dimensions is not None
            and self._requested_dimensions < self._native_dimensions
            and self._returned_dimensions == self._requested_dimensions
            and self._vector_length_valid is True
        ):
            return "tested"
        return "unsupported_by_runtime"

    @property
    def endpoint(self) -> str:
        """Return the canonical LM Studio API base URL."""
        return self._endpoint
