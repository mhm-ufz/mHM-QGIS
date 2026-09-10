# -*- coding: utf-8 -*-
"""Widget state for `project_terminal_dialog.ui`."""
from __future__ import annotations

from qgis.PyQt import QtGui

def _append_text(dialog, text: str) -> None:
    cursor = dialog.output.textCursor()
    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
    cursor.insertText(text)
    dialog.output.setTextCursor(cursor)
    dialog.output.ensureCursorVisible()


def _show_history(dialog, offset: int) -> None:
    if not dialog._history:
        return
    dialog._history_index = max(
        0,
        min(len(dialog._history), dialog._history_index + offset),
    )
    if dialog._history_index == len(dialog._history):
        dialog.command_edit.clear()
    else:
        dialog.command_edit.setText(dialog._history[dialog._history_index])
