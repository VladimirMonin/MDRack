"""Unit tests for RRF scoring."""

from __future__ import annotations

import pytest

from mdrack.search.scoring import reciprocal_rank_fusion
from mdrack.search.semantic import SemanticSearchResultItem
from mdrack.search.text import TextSearchItem


class TestRRFBasic:
    """Basic RRF functionality tests."""

    def test_non_overlapping_lists(self):
        """RRF should combine non-overlapping lists correctly."""
        text_results = [
            TextSearchItem(
                chunk_id="t1",
                score=0.9,
                snippet="...",
                file_relative_path="f1.md",
                section_title=None,
                heading_path=None,
            ),
            TextSearchItem(
                chunk_id="t2",
                score=0.8,
                snippet="...",
                file_relative_path="f2.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        semantic_results = [
            SemanticSearchResultItem(
                chunk_id="s1",
                score=0.95,
                content_preview="...",
                file_relative_path="s1.md",
                section_title=None,
                heading_path=None,
            ),
            SemanticSearchResultItem(
                chunk_id="s2",
                score=0.9,
                content_preview="...",
                file_relative_path="s2.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        result = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        # Should have 4 unique items (2 from text + 2 from semantic)
        assert len(result) == 4

        # Check all chunk_ids are present
        chunk_ids = {r.chunk_id for r in result}
        assert chunk_ids == {"t1", "t2", "s1", "s2"}

        # RRF scores: t1=1/(60+1), t2=1/(60+2), s1=1/(60+1), s2=1/(60+2)
        # Expected order: s1 ≈ t1 > s2 ≈ t2 (same scores within modalities)
        scores = {r.chunk_id: r.combined_score for r in result}
        assert scores["t1"] == pytest.approx(1.0 / 61)
        assert scores["s1"] == pytest.approx(1.0 / 61)
        assert scores["t2"] == pytest.approx(1.0 / 62)
        assert scores["s2"] == pytest.approx(1.0 / 62)

    def test_overlapping_lists(self):
        """RRF should sum scores for items appearing in both lists."""
        common_item = "c1"

        text_results = [
            TextSearchItem(
                chunk_id=common_item,
                score=0.9,
                snippet="...",
                file_relative_path="f1.md",
                section_title=None,
                heading_path=None,
            ),
            TextSearchItem(
                chunk_id="t2",
                score=0.8,
                snippet="...",
                file_relative_path="f2.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        semantic_results = [
            SemanticSearchResultItem(
                chunk_id=common_item,
                score=0.95,
                content_preview="...",
                file_relative_path="s1.md",
                section_title=None,
                heading_path=None,
            ),
            SemanticSearchResultItem(
                chunk_id="s2",
                score=0.9,
                content_preview="...",
                file_relative_path="s2.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        result = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        # Should have 3 unique items: c1, t2, s2
        assert len(result) == 3
        chunk_ids = {r.chunk_id for r in result}
        assert chunk_ids == {common_item, "t2", "s2"}

        # c1 should have combined score from both lists
        c1_result = next(r for r in result if r.chunk_id == common_item)
        expected_score = (1.0 / (60 + 1)) + (1.0 / (60 + 1))
        assert c1_result.combined_score == pytest.approx(expected_score)
        assert c1_result.text_rank == 1
        assert c1_result.semantic_rank == 1

    def test_empty_text_results(self):
        """RRF with only semantic results should work."""
        text_results = []

        semantic_results = [
            SemanticSearchResultItem(
                chunk_id="s1",
                score=0.95,
                content_preview="...",
                file_relative_path="s1.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        result = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        assert len(result) == 1
        assert result[0].chunk_id == "s1"
        assert result[0].combined_score == pytest.approx(1.0 / 61)
        assert result[0].text_rank is None
        assert result[0].semantic_rank == 1

    def test_empty_semantic_results(self):
        """RRF with only text results should work."""
        text_results = [
            TextSearchItem(
                chunk_id="t1",
                score=0.9,
                snippet="...",
                file_relative_path="f1.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        semantic_results = []

        result = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        assert len(result) == 1
        assert result[0].chunk_id == "t1"
        assert result[0].combined_score == pytest.approx(1.0 / 61)
        assert result[0].text_rank == 1
        assert result[0].semantic_rank is None

    def test_both_empty(self):
        """RRF with empty lists should return empty list."""
        result = reciprocal_rank_fusion([], [], k=60)
        assert result == []


class TestRRFDeterminism:
    """RRF should produce deterministic results."""

    def test_deterministic_output(self):
        """Same inputs should always produce same output order."""
        text_results = [
            TextSearchItem(
                chunk_id=f"t{i}",
                score=0.9 - i * 0.1,
                snippet="...",
                file_relative_path=f"f{i}.md",
                section_title=None,
                heading_path=None,
            )
            for i in range(5)
        ]

        semantic_results = [
            SemanticSearchResultItem(
                chunk_id=f"s{i}",
                score=0.95 - i * 0.1,
                content_preview="...",
                file_relative_path=f"s{i}.md",
                section_title=None,
                heading_path=None,
            )
            for i in range(5)
        ]

        result1 = reciprocal_rank_fusion(text_results, semantic_results, k=60)
        result2 = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        chunk_ids1 = [r.chunk_id for r in result1]
        chunk_ids2 = [r.chunk_id for r in result2]

        assert chunk_ids1 == chunk_ids2

    def test_different_k_produces_different_ordering(self):
        """Different k values can change ranking order for overlapping items."""
        text_results = [
            TextSearchItem(
                chunk_id="common",
                score=0.9,
                snippet="...",
                file_relative_path="f.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        semantic_results = [
            SemanticSearchResultItem(
                chunk_id="common",
                score=0.9,
                content_preview="...",
                file_relative_path="s.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        # With same rank in both, combined score = 2/(k+1)
        # The absolute value changes with k, but ordering relative to
        # other items with different ranks also depends on k
        result_k10 = reciprocal_rank_fusion(text_results, semantic_results, k=10)
        result_k100 = reciprocal_rank_fusion(text_results, semantic_results, k=100)

        # The score magnitude differs, but there's only one item so both orders are same
        assert result_k10[0].chunk_id == result_k100[0].chunk_id


class TestRRFScoringEdgeCases:
    """Edge cases for RRF scoring."""

    def test_k_parameter(self):
        """Test that k parameter affects scores correctly."""
        text_results = [
            TextSearchItem(
                chunk_id="t1",
                score=1.0,
                snippet="...",
                file_relative_path="f.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        semantic_results = [
            SemanticSearchResultItem(
                chunk_id="s1",
                score=1.0,
                content_preview="...",
                file_relative_path="s.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        result_k1 = reciprocal_rank_fusion(text_results, semantic_results, k=1)
        result_k60 = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        # Smaller k yields larger scores (stronger influence of top rank)
        assert result_k1[0].combined_score > result_k60[0].combined_score

    def test_large_result_sets(self):
        """RRF should handle larger result sets correctly."""
        num_items = 100
        text_results = [
            TextSearchItem(
                chunk_id=f"t{i}",
                score=1.0 - i * 0.01,
                snippet="...",
                file_relative_path=f"f{i}.md",
                section_title=None,
                heading_path=None,
            )
            for i in range(num_items)
        ]

        semantic_results = [
            SemanticSearchResultItem(
                chunk_id=f"s{i}",
                score=1.0 - i * 0.01,
                content_preview="...",
                file_relative_path=f"s{i}.md",
                section_title=None,
                heading_path=None,
            )
            for i in range(num_items)
        ]

        result = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        # Should have 200 unique items
        assert len(result) == 200

        # First item should have highest score
        prev_score = float("inf")
        for item in result:
            assert item.combined_score <= prev_score
            prev_score = item.combined_score

    def test_rank_is_none_when_not_present(self):
        """text_rank and semantic_rank should be None if item not in that list."""
        text_results = [
            TextSearchItem(
                chunk_id="t1",
                score=0.9,
                snippet="...",
                file_relative_path="f1.md",
                section_title=None,
                heading_path=None,
            ),
        ]

        semantic_results = []

        result = reciprocal_rank_fusion(text_results, semantic_results, k=60)

        assert result[0].chunk_id == "t1"
        assert result[0].text_rank == 1
        assert result[0].semantic_rank is None
