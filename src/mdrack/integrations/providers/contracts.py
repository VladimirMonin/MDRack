"""Optional, opt-in text-generation provider contracts.

The adapters in this module are deliberately not composed into the default
MDRack engine. They share an OpenAI-compatible request shape, but keep all
network behavior behind an injected transport so the contract suite remains
fully offline.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

import httpx

from mdrack.application.artifact_cache import ArtifactCache, ArtifactCacheKey

logger = logging.getLogger(__name__)

DEFAULT_MAX_INPUT_CHARS = 100_000
DEFAULT_MAX_OUTPUT_CHARS = 100_000
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_CACHE_ENTRIES = 128


@dataclass(frozen=True)
class ProviderHTTPResponse:
    """Minimal transport response needed by the adapter contract."""

    status_code: int
    payload: Mapping[str, Any]


class ProviderTransport(Protocol):
    async def __call__(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> ProviderHTTPResponse: ...


async def _httpx_transport(
    endpoint: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: float,
) -> ProviderHTTPResponse:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=dict(headers), json=dict(payload))
        try:
            body = response.json()
        except ValueError:
            body = {}
        return ProviderHTTPResponse(response.status_code, body if isinstance(body, dict) else {})


class OptionalProviderError(Exception):
    """Stable, privacy-safe error from an optional provider adapter."""

    def __init__(self, category: str, message: str | None = None) -> None:
        self.category = category
        super().__init__(message or category)


@dataclass(frozen=True)
class GenerationResult:
    """Provider output plus safe cache/fingerprint metadata."""

    text: str
    provider: str
    model: str
    fingerprint: str
    cached: bool
    attempts: int


@dataclass(frozen=True)
class ProviderCacheContext:
    """Caller-owned opaque identity for one provider artifact request."""

    source_fingerprint: str
    producer_fingerprint: str
    prompt_fingerprint: str
    config_fingerprint: str
    preprocessing_fingerprint: str
    artifact_kind: str


class OptionalTextProvider:
    """Bounded OpenAI-compatible provider with offline-injectable transport."""

    provider_name = "optional"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        transport: ProviderTransport | None = None,
        artifact_cache: ArtifactCache | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("endpoint must be an absolute HTTP URL")
        if not model:
            raise ValueError("model must not be empty")
        if timeout <= 0 or max_retries < 0 or max_input_chars < 1 or max_output_chars < 1:
            raise ValueError("provider limits must be positive and retries non-negative")
        if max_cache_entries < 0:
            raise ValueError("max_cache_entries must be non-negative")
        base = endpoint.rstrip("/")
        for suffix in ("/v1/chat/completions", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        self.endpoint = base + "/v1/chat/completions"
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_input_chars = max_input_chars
        self.max_output_chars = max_output_chars
        self.max_cache_entries = max_cache_entries
        self._transport = transport or _httpx_transport
        self._artifact_cache = artifact_cache
        self._cache: OrderedDict[str, str] = OrderedDict()

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def _fingerprint(self, prompt: str, max_tokens: int) -> str:
        canonical = json.dumps(
            {
                "provider": self.provider_name,
                "model": self.model,
                "endpoint": self.endpoint,
                "prompt": prompt,
                "max_tokens": max_tokens,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _opaque_fingerprint(payload: Mapping[str, Any] | str) -> str:
        if isinstance(payload, str):
            canonical = payload.encode("utf-8")
        else:
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        return "sha256:" + hashlib.sha256(canonical).hexdigest()

    def cache_key(
        self,
        prompt: str,
        max_tokens: int,
        context: ProviderCacheContext,
    ) -> ArtifactCacheKey:
        """Build complete durable identity and reject stale prompt metadata."""
        prompt_fingerprint = self._opaque_fingerprint(prompt)
        if context.prompt_fingerprint != prompt_fingerprint:
            raise OptionalProviderError("invalid_cache_identity")
        model_fingerprint = self._opaque_fingerprint(
            {
                "provider": self.provider_name,
                "model": self.model,
                "endpoint_family": "openai_chat_completions",
            }
        )
        runtime_config_fingerprint = self._opaque_fingerprint(
            {
                "caller_config_fingerprint": context.config_fingerprint,
                "max_tokens": max_tokens,
                "max_output_chars": self.max_output_chars,
            }
        )
        return ArtifactCacheKey(
            artifact_kind=context.artifact_kind,
            source_fingerprint=context.source_fingerprint,
            producer_fingerprint=context.producer_fingerprint,
            model_fingerprint=model_fingerprint,
            prompt_fingerprint=prompt_fingerprint,
            config_fingerprint=runtime_config_fingerprint,
            preprocessing_fingerprint=context.preprocessing_fingerprint,
        )

    def clear_cache(self) -> None:
        """Drop process-local outputs without destructively purging durable artifacts."""
        self._cache.clear()

    def cache_size(self) -> int:
        """Return process-local entry count without exposing keys."""
        return len(self._cache)

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 256,
        cache_context: ProviderCacheContext | None = None,
    ) -> GenerationResult:
        """Generate bounded text, retrying only transport/server failures."""
        if not isinstance(prompt, str) or not prompt:
            raise OptionalProviderError("invalid_input")
        if len(prompt) > self.max_input_chars or max_tokens < 1 or max_tokens > self.max_output_chars:
            raise OptionalProviderError("input_limit")
        durable_key = self.cache_key(prompt, max_tokens, cache_context) if cache_context is not None else None
        fingerprint = durable_key.digest if durable_key is not None else self._fingerprint(prompt, max_tokens)
        cached = self._cache.get(fingerprint)
        if cached is not None:
            self._cache.move_to_end(fingerprint)
            return GenerationResult(cached, self.provider_name, self.model, fingerprint, True, 0)

        if durable_key is not None and self._artifact_cache is not None:
            durable = self._artifact_cache.lookup(durable_key)
            if durable.payload is not None:
                durable_text = self._decode_cached_text(durable.payload)
                if durable_text is not None and len(durable_text) <= self.max_output_chars:
                    self._put_cache(fingerprint, durable_text)
                    return GenerationResult(durable_text, self.provider_name, self.model, fingerprint, True, 0)
                self._artifact_cache.discard(durable_key)

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        attempts = 0
        while attempts <= self.max_retries:
            attempts += 1
            try:
                response = await self._transport(self.endpoint, self._headers(), payload, self.timeout)
            except (httpx.TimeoutException, TimeoutError):
                if attempts <= self.max_retries:
                    continue
                raise OptionalProviderError("timeout") from None
            except httpx.RequestError:
                if attempts <= self.max_retries:
                    continue
                raise OptionalProviderError("unavailable") from None
            if response.status_code >= 500:
                if attempts <= self.max_retries:
                    continue
                raise OptionalProviderError("server_error")
            if response.status_code in {401, 403}:
                raise OptionalProviderError("authentication")
            if response.status_code >= 400:
                raise OptionalProviderError("http_error")
            text = self._extract_text(response.payload)
            if len(text) > self.max_output_chars:
                raise OptionalProviderError("output_limit")
            self._put_cache(fingerprint, text)
            if durable_key is not None and self._artifact_cache is not None:
                cache_payload = json.dumps(
                    {"schema": "mdrack.provider-text.v1", "text": text},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                stored = self._artifact_cache.store(durable_key, cache_payload)
                if stored.state == "exists":
                    winning = self._artifact_cache.lookup(durable_key)
                    winning_text = self._decode_cached_text(winning.payload) if winning.payload is not None else None
                    if winning_text is not None:
                        text = winning_text
                        self._put_cache(fingerprint, text)
            logger.info(
                "optional_provider.completed provider=%s model=%s attempts=%d cached=false output_chars=%d",
                self.provider_name,
                self.model,
                attempts,
                len(text),
            )
            return GenerationResult(text, self.provider_name, self.model, fingerprint, False, attempts)
        raise OptionalProviderError("server_error")

    @staticmethod
    def _decode_cached_text(payload: bytes) -> str | None:
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(decoded, dict) or decoded.get("schema") != "mdrack.provider-text.v1":
            return None
        text = decoded.get("text")
        return text if isinstance(text, str) else None

    @staticmethod
    def _extract_text(payload: Mapping[str, Any]) -> str:
        try:
            choices = payload["choices"]
            first = choices[0]
            message = first["message"]
            text = message["content"]
        except (KeyError, IndexError, TypeError):
            raise OptionalProviderError("invalid_response") from None
        if not isinstance(text, str):
            raise OptionalProviderError("invalid_response")
        return text

    def _put_cache(self, fingerprint: str, text: str) -> None:
        if self.max_cache_entries == 0:
            return
        self._cache[fingerprint] = text
        self._cache.move_to_end(fingerprint)
        while len(self._cache) > self.max_cache_entries:
            self._cache.popitem(last=False)


class LMStudioTextProvider(OptionalTextProvider):
    """Opt-in local LM Studio chat-completions adapter."""

    provider_name = "lmstudio"


class OpenRouterTextProvider(OptionalTextProvider):
    """Opt-in OpenRouter chat-completions adapter."""

    provider_name = "openrouter"


__all__ = [
    "GenerationResult",
    "LMStudioTextProvider",
    "OpenRouterTextProvider",
    "OptionalProviderError",
    "OptionalTextProvider",
    "ProviderCacheContext",
    "ProviderHTTPResponse",
    "ProviderTransport",
]
