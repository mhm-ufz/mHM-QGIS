"""Task monitor dialog for the plugin worker queue."""
from __future__ import annotations

from qgis.PyQt import QtWidgets

from ..bindings.thread_display import bind as bind_thread_display
from ..ui import load_form

Ui_ThreadDisplayDialog = load_form("thread_display_dialog.ui")


class ThreadDisplayDialog(QtWidgets.QDialog, Ui_ThreadDisplayDialog):
    def __init__(self, coordinator, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.coordinator = coordinator
        self.spinBox_threadCount.setValue(coordinator.max_threads)
        bind_thread_display(self, coordinator)
        coordinator.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        rows = self.coordinator.snapshots()
        selected = self.tableWidget_threads.currentRow()
        self.tableWidget_threads.setRowCount(len(rows))
        for row, snapshot in enumerate(rows):
            values = (
                str(snapshot["slot"]),
                snapshot["status"],
                snapshot["label"],
                f'{snapshot["progress"]}%',
            )
            for column, value in enumerate(values):
                self.tableWidget_threads.setItem(
                    row, column, QtWidgets.QTableWidgetItem(value)
                )
        self.tableWidget_threads.resizeColumnsToContents()
        if rows:
            self.tableWidget_threads.setCurrentCell(
                min(max(selected, 0), len(rows) - 1), 0
            )
        self._show_log(self.tableWidget_threads.currentRow(), 0, -1, -1)

    def _show_log(self, row, _column, _previous_row, _previous_column):
        snapshots = self.coordinator.snapshots()
        logs = snapshots[row]["logs"] if 0 <= row < len(snapshots) else []
        self.plainTextEdit_threadLog.setPlainText("\n".join(logs))


__all__ = ["ThreadDisplayDialog"]
