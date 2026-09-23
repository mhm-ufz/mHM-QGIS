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


def test_finished_tasks_ignore_late_progress_and_cancel_stops_queued_work():
    coordinator = TaskCoordinator(max_threads=1)
    cancelled = []
    assert coordinator.start_external("running", "Running", cancel=lambda: cancelled.append(True))
    coordinator.set_progress("running", 40)
    assert coordinator.submit("queued", "Queued", lambda: None)
    coordinator.cancel_all()
    assert cancelled == [True]
    assert coordinator.queued_count() == 0
    coordinator.finish_external("running", False)
    coordinator.set_progress("running", 80)
    assert coordinator.snapshots()[0]["progress"] == 40
    assert not coordinator.submit("later", "Later", lambda: None)


def test_main_dialog_cancel_waits_for_workers_then_closes():
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog

    dialog = MhmQgisDialog()
    loop = QtCore.QEventLoop()
    cancelled = []
    dialog.finished.connect(loop.quit)
    dialog.task_coordinator.start_external(
        "running", "Running", cancel=lambda: (
            cancelled.append(True),
            dialog.task_coordinator.finish_external("running", False),
        ))
    dialog.show()
    dialog.reject()
    QtCore.QTimer.singleShot(2000, loop.quit)
    loop.exec()
    assert cancelled == [True]
    assert not dialog.isVisible()


def test_meteo_qthread_stops_on_cancel(monkeypatch):
    import time
    from mhm_qgis.core.executions import meteo
    from mhm_qgis.qt.threads.meteo_workflow import MeteorologyWorkflowWorker

    def prepare(_run, _state, *, cancelled, **_options):
        while not cancelled():
            time.sleep(0.01)
        return False

    monkeypatch.setattr(meteo, "prepare_forcing", prepare)
    worker = MeteorologyWorkflowWorker("execute_all_meteo", "Meteo", None, None)
    thread = QtCore.QThread()
    worker.moveToThread(thread)
    worker.finished.connect(lambda *_: thread.quit())
    thread.started.connect(worker.run)
    loop = QtCore.QEventLoop()
    thread.finished.connect(loop.quit)
    thread.start()
    QtCore.QTimer.singleShot(50, lambda: worker.cancel())
    QtCore.QTimer.singleShot(2000, loop.quit)
    loop.exec()
    assert not thread.isRunning()
    worker.deleteLater()
    thread.deleteLater()
