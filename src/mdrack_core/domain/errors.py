"""Stable, privacy-safe core error categories and exceptions."""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    BRANCH_UNAVAILABLE = "branch_unavailable"
    INCOMPATIBLE_VECTOR_SPACE = "incompatible_vector_space"
    ADAPTER_TIMEOUT = "adapter_timeout"
    ADAPTER_ERROR = "adapter_error"
    CATALOG_ERROR = "catalog_error"
    INTERNAL_ERROR = "internal_error"


class DegradationCategory(StrEnum):
    BRANCH_UNAVAILABLE = "branch_unavailable"
    INCOMPATIBLE_VECTOR_SPACE = "incompatible_vector_space"
    ADAPTER_TIMEOUT = "adapter_timeout"
    ADAPTER_ERROR = "adapter_error"


class CoreError(Exception):
    """A stable core failure that never accepts raw exception text."""

    def __init__(self, category: ErrorCategory) -> None:
        if not isinstance(category, ErrorCategory):
            raise ValueError("category must be an ErrorCategory")
        self.category = category
        super().__init__(category.value)


class BranchExecutionError(CoreError):
    """A branch failure classified at the adapter/application boundary."""

    def __init__(self, category: ErrorCategory, *, branch_id: str) -> None:
        if category not in {
            ErrorCategory.BRANCH_UNAVAILABLE,
            ErrorCategory.INCOMPATIBLE_VECTOR_SPACE,
            ErrorCategory.ADAPTER_TIMEOUT,
            ErrorCategory.ADAPTER_ERROR,
        }:
            raise ValueError("category is not valid for branch execution")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("branch_id must be a non-empty string")
        self.branch_id = branch_id
        super().__init__(category)


class CatalogExecutionError(CoreError):
    """A catalog failure with a stable category and no adapter exception payload."""

    def __init__(self, category: ErrorCategory) -> None:
        if category not in {ErrorCategory.CATALOG_ERROR, ErrorCategory.ADAPTER_TIMEOUT}:
            raise ValueError("category is not valid for catalog execution")
        super().__init__(category)
