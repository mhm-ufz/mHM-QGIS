from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt import QtCore, QtWidgets

from mhm_qgis.qt.dialogs.thread_display import ThreadDisplayDialog
from mhm_qgis.qt.threads.task_coordinator import TaskCoordinator

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
        lambda _task: 1,
        controls=(button,),
        on_success=lambda value: (
            results.append(value),
            enabled_during_queue.append(button.isEnabled()),
        ),
    )
    coordinator.submit(
        "two",
        "Second",
        lambda _task: 2,
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
        lambda _task: threading.current_thread().__class__.__name__,
        on_success=kinds.append,
    )
    coordinator.submit(
        "boom",
        "Failing job",
        lambda _task: (_ for _ in ()).throw(ValueError("boom")),
        on_error=lambda message: (errors.append(message), loop.quit()),
    )
    QtCore.QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()

    # A raw QgsTask thread is not created by Python, so it reports as
    # `_DummyThread`; a real one means the job ran off the QgsTask thread.
    assert kinds == ["Thread"]
    assert errors
    assert "boom" in errors[0]


def _until_cancelled(task):
    """Block like a long job; give up after 5 s so a failing test cannot hang."""
    import time

    deadline = time.monotonic() + 5
    while not task.isCanceled() and time.monotonic() < deadline:
        time.sleep(0.01)
    return "stopped"


def _wait(condition, seconds=5):
    import time

    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def test_finished_tasks_ignore_late_progress_and_cancel_stops_queued_work():
    _app()
    coordinator = TaskCoordinator(max_threads=1)
    errors = []
    assert coordinator.submit("running", "Running", _until_cancelled, on_error=errors.append)
    _wait(lambda: coordinator.snapshots()[0]["status"] == "running")
    coordinator.set_progress("running", 40)
    assert coordinator.snapshots()[0]["progress"] == 40
    assert coordinator.submit("queued", "Queued", lambda _task: None)
    coordinator.cancel_all()
    assert coordinator.queued_count() == 0
    _wait(lambda: not coordinator.is_busy())
    assert errors == ["Task cancelled."]
    final = coordinator.snapshots()[0]["progress"]
    coordinator.set_progress("running", 80)
    assert coordinator.snapshots()[0]["progress"] == final
    assert not coordinator.submit("later", "Later", lambda _task: None)


def test_worker_log_reaches_the_monitor_from_its_thread():
    _app()
    coordinator = TaskCoordinator(max_threads=1)
    done = []
    coordinator.submit(
        "logging", "Logging",
        lambda _task: coordinator.log.emit("logging", "from the worker"),
        on_success=done.append,
    )
    _wait(lambda: done)
    assert "from the worker" in coordinator.snapshots()[0]["logs"]


def test_main_dialog_cancel_waits_for_workers_then_closes():
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog

    dialog = MhmQgisDialog()
    loop = QtCore.QEventLoop()
    dialog.finished.connect(loop.quit)
    assert dialog.task_coordinator.submit("running", "Running", _until_cancelled)
    _wait(lambda: dialog.task_coordinator.snapshots()[0]["status"] == "running")
    dialog.show()
    dialog.reject()
    QtCore.QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert not dialog.task_coordinator.is_busy()
    assert not dialog.isVisible()


def test_cancelled_dialog_is_replaced_when_plugin_reopens(monkeypatch):
    from types import SimpleNamespace

    from mhm_qgis import dependency_bootstrap
    from mhm_qgis.mhm_qgis import MhmQgis
    from mhm_qgis.qt.dialogs import mhm_qgis_main

    old = SimpleNamespace(
        _closing=True, isVisible=lambda: False, deleteLater=lambda: None)
    fresh = SimpleNamespace(
        show=lambda: None, raise_=lambda: None, activateWindow=lambda: None)
    monkeypatch.setattr(
        dependency_bootstrap, "ensure_qgis_runtime_dependencies",
        lambda **_kwargs: SimpleNamespace(ok=True),
    )
    monkeypatch.setattr(
        mhm_qgis_main, "MhmQgisDialog", lambda **_kwargs: fresh)
    plugin = MhmQgis.__new__(MhmQgis)
    plugin.iface = SimpleNamespace(mainWindow=lambda: None)
    plugin.first_start = False
    plugin.dlg = old

    plugin.run()

    assert plugin.dlg is fresh


def test_meteorology_runs_as_a_cancellable_coordinator_task(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from mhm_qgis.core.executions import meteo
    from mhm_qgis.core.handlers.state import processing
    from mhm_qgis.qt.controllers import forcing, grid, time, workflow_state
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
    from mhm_qgis.qt.workflows import lifecycle, meteorology

    progress = []

    def prepare(_run, _state, *, cancelled, progress, log, **_options):
        log("preparing")
        progress(10)
        _until_cancelled(SimpleNamespace(isCanceled=cancelled))
        return True

    def unexpected_box(*args):
        raise AssertionError(f"Unexpected message box: {args[1:]}")

    for name in ("warning", "information"):
        monkeypatch.setattr(lifecycle.QMessageBox, name, unexpected_box)
    failures = []
    monkeypatch.setattr(workflow_state.QMessageBox, "critical",
                        lambda _parent, _title, message: failures.append(message))
    monkeypatch.setattr(meteo, "prepare_forcing", prepare)
    monkeypatch.setattr(meteo, "MeteorologyRun", lambda **_kwargs: None)
    monkeypatch.setattr(grid, "require_common_grid", lambda _dialog: {"l2_header": {}})
    monkeypatch.setattr(meteorology, "target_grid_from_header",
                        lambda *_args: SimpleNamespace(validate=lambda: None))
    monkeypatch.setattr(forcing, "selected_meteo_specs", lambda _dialog: ())
    monkeypatch.setattr(time, "values", lambda _dialog: {})
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.check_prerequisites = lambda: True
    dialog.task_coordinator.set_progress = lambda key, value: progress.append((key, value))

    assert meteorology.start(dialog)
    assert workflow_state.running_key(dialog) == "execute_all_meteo"
    assert not dialog.tabWidget_steps.isEnabled()
    _wait(lambda: progress)
    dialog.task_coordinator.cancel_all()
    _wait(lambda: failures)
    assert failures == ["Task cancelled."]
    assert processing.workflow(tmp_path, "execute_all_meteo")["status"] == "failed"
    assert "preparing" in dialog.LogText.toPlainText()
    assert workflow_state.running_key(dialog) is None
    assert dialog.tabWidget_steps.isEnabled()
    dialog.close()
