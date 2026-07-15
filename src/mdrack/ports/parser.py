"""Port for replaceable Markdown parser adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mdrack.domain.documents import Document


class MarkdownParser(Protocol):
    name: str
    version: str

    def parse(
        self,
        path: Path,
        *,
        content: str | None = None,
        document_id: str,
        relative_path: str,
    ) -> Document: ...
