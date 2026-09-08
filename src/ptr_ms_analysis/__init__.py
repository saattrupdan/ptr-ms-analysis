"""Open-source PTR-MS analysis and review tools."""

__all__ = ["__version__"]

# One version, in pyproject.toml. It used to be stated here too, and this copy fell
# five releases behind (it read 0.1.0 while the installers shipped 0.4.0), which is
# what an unpoliced second source of truth always does. The packaging tools already
# read the distribution metadata, so this does the same; the fallback is for a source
# checkout that was never installed, where there is no metadata to read.
try:
    from importlib.metadata import version as _version

    __version__ = _version("ptr_ms_analysis")
except Exception:  # pragma: no cover - only from a bare checkout
    __version__ = "0.0.0+unknown"
