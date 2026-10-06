"""Widget wiring for `thread_display_dialog.ui`."""
from __future__ import annotations


def bind(dialog, coordinator) -> None:
    """Wire the thread controls.

    `coordinator.changed` stays in the dialog: it is the coordinator pushing to
    the form, not the form reacting to a user.
    """
    dialog.spinBox_threadCount.valueChanged.connect(coordinator.set_max_threads)
    dialog.tableWidget_threads.currentCellChanged.connect(dialog._show_log)


__all__ = ["bind"]
