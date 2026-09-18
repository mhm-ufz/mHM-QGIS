from __future__ import annotations

import pytest

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt import QtCore, QtWidgets  # noqa: E402

from mhm_qgis.qt.threads.task_coordinator import TaskCoordinator  # noqa: E402
from mhm_qgis.qt.dialogs.thread_display import ThreadDisplayDialog  # noqa: E402


_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or _APPLICATION or QtWidgets.QApplication([])
    return _APPLICATION


def test_bounded_jobs_lock_controls_and_keep_per_slot_log():
    app = _app()
    coordinator = TaskCoordinator(max_threads=1)
    button = QtWidgets.QPushButton()
    results = []
    enabled_during_queue = []
    loop = QtCore.QEventLoop()

    coordinator.submit(
        "one",
        "First",
        lambda: 1,
        controls=(button,),
        on_success=lambda value: (
            results.append(value),
            enabled_during_queue.append(button.isEnabled()),
        ),
    )
    coordinator.submit(
        "two",
        "Second",
        lambda: 2,
        controls=(button,),
        on_success=lambda value: (results.append(value), loop.quit()),
    )
    QtCore.QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()

    assert results == [1, 2]
    assert enabled_during_queue == [False]
    assert button.isEnabled()
    assert coordinator.queued_count() == 0
    snapshot = coordinator.snapshots()[0]
    assert snapshot["status"] == "completed"
    assert snapshot["label"] == "Second"

    dialog = ThreadDisplayDialog(coordinator)
    assert dialog.tableWidget_threads.rowCount() == 1
    assert dialog.tableWidget_threads.item(0, 2).text() == "Second"
    dialog.close()


def test_jobs_run_off_the_qgstask_thread_and_still_report_failures():
    """pyproj segfaults QGIS when called twice from a QgsTask thread."""
    app = _app()
    coordinator = TaskCoordinator(max_threads=1)
    kinds = []
    errors = []
    loop = QtCore.QEventLoop()

    coordinator.submit(
        "thread-kind",
        "Thread kind",
        lambda: threading.current_thread().__class__.__name__,
        on_success=kinds.append,
    )
    coordinator.submit(
        "boom",
        "Failing job",
        lambda: (_ for _ in ()).throw(ValueError("boom")),
        on_error=lambda message: (errors.append(message), loop.quit()),
    )
    QtCore.QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()

    # A raw QgsTask thread is not created by Python, so it reports as
    # `_DummyThread`; a real one means the job ran off the QgsTask thread.
    assert kinds == ["Thread"]
    assert errors and "boom" in errors[0]
