"""Canonical app-owned textual embedding-space identity and media re-keying."""

from __future__ import annotations

from dataclasses import dataclass, replace

from mdrack.application.vector_values import value_policy_metadata
from mdrack.domain.identifiers import logical_id
from mdrack.domain.profiles import EmbeddingProfile
from mdrack_core import EmbeddingSpaceRecord, PreparedResourceBatch
from mdrack_core.domain import METRIC_COSINE, VectorRecord
from mdrack_media import EmbeddingFingerprint


@dataclass(frozen=True)
class CanonicalTextEmbeddingSpace:
    """The one application-owned textual space derived from a full profile."""

    profile: EmbeddingProfile

    @property
    def space_id(self) -> str:
        return embedding_space_id(
            self.profile.name,
            self.profile.fingerprint,
            self.profile.vector_value_policy,
        )

    @property
    def record(self) -> EmbeddingSpaceRecord:
        return EmbeddingSpaceRecord(
            space_id=self.space_id,
            dimensions=self.profile.output_dimensions,
            metric=METRIC_COSINE,
            fingerprint=self.profile.fingerprint,
            metadata={
                "profile": self.profile.name,
                **value_policy_metadata(self.profile.vector_value_policy),
            },
        )

    @property
    def media_fingerprint(self) -> EmbeddingFingerprint:
        """Return the media-builder transport encoding of this same app profile."""
        return EmbeddingFingerprint.from_dict(f"sha256:{self.profile.fingerprint}")

    def rekey_batch(self, batch: PreparedResourceBatch) -> PreparedResourceBatch:
        """Replace media transport space IDs with this canonical app-owned record."""
        if not batch.vectors:
            return batch
        source_space_ids = {space.space_id for space in batch.spaces}
        vector_space_ids = {vector.space_id for vector in batch.vectors}
        if vector_space_ids != source_space_ids:
            raise ValueError("textual media batch has inconsistent embedding spaces")
        return replace(
            batch,
            spaces=(self.record,),
            vectors=tuple(
                VectorRecord(vector.unit_id, self.space_id, vector.vector)
                for vector in batch.vectors
            ),
        )


def embedding_space_id(
    profile_name: str,
    fingerprint: str,
    vector_value_policy: str | None = None,
) -> str:
    """Return the deterministic app-owned identity for one textual profile."""
    return logical_id("embedding-space", profile_name, fingerprint, vector_value_policy)


__all__ = ["CanonicalTextEmbeddingSpace", "embedding_space_id"]
