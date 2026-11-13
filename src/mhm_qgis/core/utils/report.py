"""Severity levels for the `report=` callback contract.

Defined here, in a leaf with no dependencies, so `core` can name a severity
without importing the Qt layer that renders it. `qt/reporting` imports these;
nothing goes the other way.
"""
from __future__ import annotations

WARNING = "warning"
CRITICAL = "critical"
INFORMATION = "information"


#: The types the plugin raises with a message for the user: the dialog shows the message alone.
_USER_ERRORS = ("ValueError: ", "RuntimeError: ", "FileNotFoundError: ")


def error_detail(message) -> str:
    """Return the one line of `message` worth showing in a dialog title bar.

    Background work reports a failure as its whole formatted traceback. That is reduced to
    the first line of its exception (a long message can run on over several lines, ending
    in a URL), without the type of the plugin's own user-facing errors; anything else to its
    first non-empty line.
    """
    lines = [line.rstrip() for line in str(message).splitlines() if line.strip()]
    if not lines:
        return str(message).strip()
    if not lines[0].startswith("Traceback (most recent call last)"):
        return lines[0].strip()
    frame = max((number for number, line in enumerate(lines) if line.lstrip().startswith('File "')), default=0)
    line = next((line for line in lines[frame + 1:] if not line[:1].isspace()), lines[-1]).strip()
    for prefix in _USER_ERRORS:
        line = line.removeprefix(prefix)
    return line


__all__ = ["CRITICAL", "INFORMATION", "WARNING", "error_detail"]
