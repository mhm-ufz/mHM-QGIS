# -*- coding: utf-8 -*-
"""Turning a message from the core layers into a Qt dialog.

Code under `core/` must not import Qt, but it still has things a user has to be
told or asked. It takes a `report=` callback -- and, where an answer is needed,
a `confirm=` predicate -- and this module supplies the implementations that
raise an actual `QMessageBox`.

Both default to inert: `report=None` means "stay quiet" and a missing `confirm`
means "assume no", so a core function called from a worker, a test or the
file-only caller never blocks on a dialog that has no one to answer it.
"""
from __future__ import annotations

from ...core.utils.report import CRITICAL, INFORMATION, WARNING, error_detail


def dialog_reporter(parent, *, log=None):
    """Return a `report(level, title, message)` that raises a QMessageBox.

    `log` also receives the message, so the plugin log keeps a record of what
    the user was shown.
    """
    def report(level, title, message):
        if log is not None:
            prefix = "ERROR" if level == CRITICAL else level.upper()
            log(f"{prefix}: {message}")
        from qgis.PyQt.QtWidgets import QMessageBox

        show = getattr(QMessageBox, level, QMessageBox.warning)
        show(parent, title, message)

    return report


def dialog_confirmer(parent):
    """Return a `confirm(title, question) -> bool` backed by a Yes/No box."""
    def confirm(title, question):
        from qgis.PyQt.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            parent, title, question, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
        )
        return reply == QMessageBox.StandardButton.Yes

    return confirm


def log_reporter(log):
    """Return a `report(...)` that only logs -- for workers and headless runs."""
    def report(level, title, message):
        if log is not None:
            prefix = "ERROR" if level == CRITICAL else level.upper()
            log(f"{prefix}: {title}: {message}")

    return report


__all__ = [
    "error_detail",
    "CRITICAL",
    "INFORMATION",
    "WARNING",
    "dialog_confirmer",
    "dialog_reporter",
    "log_reporter",
]
