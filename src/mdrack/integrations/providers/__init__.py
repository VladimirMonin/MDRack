"""Optional provider adapters; importing this package performs no network I/O."""

from mdrack.integrations.providers.contracts import (
    GenerationResult,
    LMStudioTextProvider,
    OpenRouterTextProvider,
    OptionalProviderError,
    OptionalTextProvider,
    ProviderHTTPResponse,
    ProviderTransport,
)

__all__ = [
    "GenerationResult",
    "LMStudioTextProvider",
    "OpenRouterTextProvider",
    "OptionalProviderError",
    "OptionalTextProvider",
    "ProviderHTTPResponse",
    "ProviderTransport",
]
