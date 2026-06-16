"""Tests for forbidden dependency checker."""
import subprocess
import sys
from pathlib import Path


def test_check_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_no_forbidden_deps.py"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent.parent
    )
    assert result.returncode == 0, f"Script failed: {result.stdout} {result.stderr}"

def test_forbidden_list_completeness() -> None:
    from scripts import check_no_forbidden_deps
    required = {"torch", "transformers", "sentence-transformers", "qdrant-client", "chromadb", "lancedb"}
    assert required.issubset(check_no_forbidden_deps.FORBIDDEN)
