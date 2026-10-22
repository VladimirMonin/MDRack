"""W2 provider refusal and legacy query instruction preservation."""
import pytest

from mdrack.config.models import MDRackConfig
from mdrack.embeddings.runtime import create_embedding_provider


def test_unknown_provider_is_not_lmstudio() -> None:
    with pytest.raises(ValueError, match="unsupported_embedding_provider"):
        create_embedding_provider("private-typo", MDRackConfig())


@pytest.mark.parametrize('vector', [(float('nan'), 1.0), (float('inf'), 1.0), (0.0, 0.0), (True, 1.0), (1.0,)])
def test_embedding_result_validates_before_publication(vector) -> None:
    from mdrack.domain.embedding_inputs import EmbeddingResult
    from mdrack_core import EmbeddingSpaceRecord
    with pytest.raises(ValueError):
        EmbeddingResult(vector, EmbeddingSpaceRecord('space', 2, 'cosine', 'fingerprint'))


@pytest.mark.asyncio
async def test_capability_roles_candidates_and_limits_precede_provider() -> None:
    from mdrack.domain.embedding_inputs import EncodingRole, InputCapability, RouteCapabilities, TextInput
    from mdrack.embeddings.synthetic import SyntheticPreparedProvider
    provider = SyntheticPreparedProvider()
    provider.capabilities = RouteCapabilities((InputCapability(EncodingRole.QUERY, 'text', ('image',), 3),))
    provider.capabilities.validate(TextInput('abc', 'q'), EncodingRole.QUERY, ('image',))
    with pytest.raises(ValueError, match='too_large'):
        provider.capabilities.validate(TextInput('abcd', 'q'), EncodingRole.QUERY, ('image',))
    for role, candidates in ((EncodingRole.DOCUMENT, ('image',)), (EncodingRole.QUERY, ('text',))):
        with pytest.raises(ValueError, match='unsupported_embedding_route'):
            provider.capabilities.validate(TextInput('abc', 'q'), role, candidates)
