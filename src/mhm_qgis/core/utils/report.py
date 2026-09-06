# -*- coding: utf-8 -*-
"""Severity levels for the `report=` callback contract.

Defined here, in a leaf with no dependencies, so `core` can name a severity
without importing the Qt layer that renders it. `qt/reporting` imports these;
nothing goes the other way.
"""
from __future__ import annotations

WARNING = "warning"
CRITICAL = "critical"
INFORMATION = "information"


def error_detail(message) -> str:
    """Return the one line of `message` worth showing in a dialog title bar.

    Background work runs in `native_worker`, which reports a failure by raising
    the worker's whole formatted traceback. Its first line is the useless
    ``Traceback (most recent call last):`` banner, so a traceback is reduced to
    its last line -- the exception type and text -- and anything else to its
    first non-empty line.
    """
    lines = [line.strip() for line in str(message).splitlines() if line.strip()]
    if not lines:
        return str(message).strip()
    if lines[0].startswith("Traceback (most recent call last)"):
        return lines[-1]
    return lines[0]


__all__ = ["CRITICAL", "INFORMATION", "WARNING", "error_detail"]
