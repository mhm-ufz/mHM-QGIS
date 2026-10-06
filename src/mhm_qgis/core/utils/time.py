"""Shared time helpers."""

from datetime import UTC, datetime


def utc_timestamp():
    """Return a compact UTC timestamp without using deprecated utcnow()."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z")
