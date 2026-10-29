"""Shared time helpers."""

from datetime import datetime, timezone


def utc_timestamp():
    """Return a compact UTC timestamp without using deprecated utcnow()."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")
