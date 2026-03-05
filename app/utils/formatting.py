"""Helpers for consistent status message formatting.

All bot responses must use one of these four helpers so iconography
and tone stay uniform across the entire codebase.
"""


def info(text: str) -> str:
    """ℹ️  Neutral information."""
    return f"ℹ️ {text}"


def success(text: str) -> str:
    """✅ Successful operation or positive status."""
    return f"✅ {text}"


def warning(text: str) -> str:
    """⚠️  Non-critical issue or elevated metric."""
    return f"⚠️ {text}"


def error(text: str) -> str:
    """❌ Error or failed operation."""
    return f"❌ {text}"
