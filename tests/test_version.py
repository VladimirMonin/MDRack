"""Tests for mdrack package version."""


def test_version_exists() -> None:
    """Verify that mdrack.__version__ is defined."""
    import mdrack

    assert hasattr(mdrack, "__version__")
    assert isinstance(mdrack.__version__, str)
    assert mdrack.__version__ == "0.1.0"
