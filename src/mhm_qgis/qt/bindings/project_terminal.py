# -*- coding: utf-8 -*-
"""Widget wiring for `project_terminal_dialog.ui`."""
from __future__ import annotations


def bind(dialog) -> None:
    """Wire the command entry and its buttons.

    The `QProcess` signals stay in the dialog: they belong to the shell it owns,
    not to the form.
    """
    dialog.command_edit.returnPressed.connect(dialog.send_current_command)
    # send_button → dialog.send_current_command
    dialog.send_button.clicked.connect(dialog.send_current_command)
    # clear_button → output.clear
    dialog.clear_button.clicked.connect(dialog.output.clear)
    # close_button → dialog.hide
    dialog.close_button.clicked.connect(dialog.hide)


__all__ = ["bind"]
