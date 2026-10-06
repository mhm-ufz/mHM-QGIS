"""Small logging adapter used by package internals."""
from __future__ import annotations

from collections.abc import Callable


def log_message(log: Callable[[str], None] | None, message: str) -> None:
    """Send a message to an optional QGIS/plugin logger callback."""
    if log:
        log(message)
