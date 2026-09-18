# -*- coding: utf-8 -*-
"""The worker that runs the QGIS-free meteorology phase off the GUI thread."""
from __future__ import annotations

import traceback

from qgis.PyQt import QtCore


class MeteorologyWorkflowWorker(QtCore.QObject):
    """Run only the QGIS-free meteorology phase in a worker thread."""

    log_message = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal(str, bool, str)

    def __init__(
            self,
            workflow_key,
            workflow_label,
            meteorology_state,
            meteorology_run):
        super().__init__()
        self.workflow_key = workflow_key
        self.workflow_label = workflow_label
        self.meteorology_state = meteorology_state
        self.meteorology_run = meteorology_run

    @QtCore.pyqtSlot()
    def run(self):
        """Execute meteorology and hand morphology back to the GUI thread."""
        from ...core.executions import meteo

        try:
            # No `report`: a worker thread must not raise a modal dialog, so
            # failures come back through `finished` for the GUI thread to show.
            ok = meteo.prepare_forcing(
                self.meteorology_run,
                self.meteorology_state,
                log=self.log_message.emit,
                progress=self.progress.emit,
            )
            self.finished.emit(self.workflow_key, ok, "")
        except Exception as exc:
            self.log_message.emit(
                f"\nERROR: {self.workflow_label} worker failed: {exc}"
            )
            self.log_message.emit(f"Traceback: {traceback.format_exc()}")
            self.finished.emit(self.workflow_key, False, str(exc))
