# -*- coding: utf-8 -*-
"""Widget state for `thread_display_dialog.ui`."""
from __future__ import annotations

from qgis.PyQt import QtWidgets

def refresh(dialog):
    rows = dialog.coordinator.snapshots()
    selected = dialog.tableWidget_threads.currentRow()
    dialog.tableWidget_threads.setRowCount(len(rows))
    for row, snapshot in enumerate(rows):
        values = (
            str(snapshot["slot"]),
            snapshot["status"],
            snapshot["label"],
            f'{snapshot["progress"]}%',
        )
        for column, value in enumerate(values):
            dialog.tableWidget_threads.setItem(
                row, column, QtWidgets.QTableWidgetItem(value)
            )
    dialog.tableWidget_threads.resizeColumnsToContents()
    if rows:
        dialog.tableWidget_threads.setCurrentCell(
            min(max(selected, 0), len(rows) - 1), 0
        )
    dialog._show_log(dialog.tableWidget_threads.currentRow(), 0, -1, -1)


def _show_log(dialog, row, _column, _previous_row, _previous_column):
    snapshots = dialog.coordinator.snapshots()
    logs = snapshots[row]["logs"] if 0 <= row < len(snapshots) else []
    dialog.plainTextEdit_threadLog.setPlainText("\n".join(logs))
