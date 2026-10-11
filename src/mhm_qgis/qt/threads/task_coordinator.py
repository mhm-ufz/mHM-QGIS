"""Bounded QGIS task queue shared by plugin preprocessing tasks."""
from __future__ import annotations

import contextlib
import threading
import traceback
from dataclasses import dataclass, field

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt import QtCore


@dataclass
class TaskRecord:
    key: str
    label: str
    runner: object
    controls: tuple = ()
    on_success: object = None
    on_error: object = None
    resource: str = ""
    status: str = "queued"
    progress: int = 0
    slot: int | None = None
    logs: list[str] = field(default_factory=list)
    task: object = None


class TaskCoordinator(QtCore.QObject):
    """Queue file-only jobs and expose stable QGIS-task status to the UI.

    A runner is called as `runner(task)` on a plain worker thread. It reports
    through `task.setProgress`, stops on `task.isCanceled()` and logs through
    `log.emit(key, message)`, which is safe from any thread.
    """

    changed = QtCore.pyqtSignal()
    log = QtCore.pyqtSignal(str, str)

    def __init__(self, parent=None, max_threads=2):
        super().__init__(parent)
        self._max_threads = max(1, int(max_threads))
        self._pending: list[TaskRecord] = []
        self._active: dict[str, TaskRecord] = {}
        self._records: dict[str, TaskRecord] = {}
        self._history: dict[int, TaskRecord] = {}
        self._control_owners: dict[int, set[str]] = {}
        self._control_states: dict[int, bool] = {}
        self._stopping = False
        self.log.connect(self.append_log)

    @property
    def max_threads(self):
        return self._max_threads

    def set_max_threads(self, value):
        try:
            requested = int(value)
        except (TypeError, ValueError):
            requested = 2
        highest_active = max(
            (record.slot or 1 for record in self._active.values()), default=1
        )
        self._max_threads = max(highest_active, min(32, max(1, requested)))
        self.changed.emit()
        self._start_available()

    def is_busy(self, key=None):
        if key is None:
            return bool(self._pending or self._active)
        record = self._records.get(str(key))
        return bool(record and record.status in {"queued", "running"})

    def resource_busy(self, resource):
        resource = str(resource or "")
        return bool(
            resource
            and any(
                record.resource == resource
                for record in (*self._active.values(), *self._pending)
            )
        )

    def has_capacity(self):
        return len(self._active) < self._max_threads

    def submit(
        self,
        key,
        label,
        runner,
        *,
        controls=(),
        on_success=None,
        on_error=None,
        resource="",
    ):
        key = str(key)
        if self._stopping or self.is_busy(key):
            return False
        record = TaskRecord(
            key=key,
            label=str(label),
            runner=runner,
            controls=tuple(widget for widget in controls if widget is not None),
            on_success=on_success,
            on_error=on_error,
            resource=str(resource or ""),
        )
        self._records[key] = record
        self._pending.append(record)
        self._set_controls(record, False)
        self.changed.emit()
        self._start_available()
        return True

    def append_log(self, key, message):
        record = self._records.get(str(key))
        if record is None:
            return
        record.logs.append(str(message))
        del record.logs[:-500]
        self.changed.emit()

    def set_progress(self, key, value):
        record = self._records.get(str(key))
        if record is None or record.status != "running":
            return
        record.progress = max(0, min(100, int(value)))
        self.changed.emit()

    @QtCore.pyqtSlot(float)
    def _task_progress_changed(self, value):
        source = self.sender()
        key = source.property("mhm_qgis_task_key") if source is not None else ""
        if key:
            self.set_progress(str(key), value)

    def snapshots(self):
        rows = []
        for slot in range(1, self._max_threads + 1):
            record = next(
                (item for item in self._active.values() if item.slot == slot),
                None,
            )
            if record is None:
                record = self._history.get(slot)
            if record is None:
                rows.append(
                    {"slot": slot, "key": "", "status": "idle", "label": "", "progress": 0, "logs": []}
                )
            else:
                rows.append(
                    {
                        "slot": slot,
                        "key": record.key,
                        "status": record.status,
                        "label": record.label,
                        "progress": record.progress,
                        "logs": list(record.logs),
                    }
                )
        return rows

    def queued_count(self):
        return len(self._pending)

    def cancel(self, key):
        """Cancel a queued or active task."""
        key = str(key)
        for record in tuple(self._pending):
            if record.key == key:
                self._pending.remove(record)
                self._finish_record(record, False, "Task cancelled.", "cancelled")
                return True
        record = self._active.get(key)
        if record is None:
            return False
        if record.task is None:
            return False
        record.task.cancel()
        return True

    def cancel_all(self):
        """Stop queued work and request cancellation of every running worker."""
        self._stopping = True
        for key in tuple(record.key for record in (*self._pending, *self._active.values())):
            self.cancel(key)

    def _free_slot(self):
        used = {record.slot for record in self._active.values()}
        return next((slot for slot in range(1, self._max_threads + 1) if slot not in used), None)

    def _start_available(self):
        if self._stopping:
            return
        while self._pending and len(self._active) < self._max_threads:
            slot = self._free_slot()
            if slot is None:
                return
            active_resources = {
                item.resource for item in self._active.values() if item.resource
            }
            index = next(
                (
                    position
                    for position, item in enumerate(self._pending)
                    if not item.resource or item.resource not in active_resources
                ),
                None,
            )
            if index is None:
                return
            record = self._pending.pop(index)
            record.slot = slot
            record.status = "running"
            record.logs.append(f"Started: {record.label}")
            self._active[record.key] = record
            self._start_qgis_task(record)
            self.changed.emit()

    def _start_qgis_task(self, record):
        """Submit a callable to QGIS's managed task pool."""
        runner = record.runner

        def run(task):
            if task.isCanceled():
                return _CANCELLED
            # Boxed: QGIS's task wrapper tests the returned value's truth, which drops a
            # falsy result and raises (silently, leaving the task unfinished) on a DataFrame.
            return [_off_qthread(lambda: runner(task))]

        def finished(exception, result=None):
            if task.isCanceled():
                self._finish(record.key, False, "Task cancelled.", "cancelled")
            elif exception is not None:
                details = "".join(
                    traceback.format_exception(
                        type(exception), exception, exception.__traceback__
                    )
                )
                self._finish(record.key, False, details)
            elif result is _CANCELLED:
                self._finish(record.key, False, "Task cancelled.", "cancelled")
            else:
                self._finish(record.key, True, result[0])

        task = QgsTask.fromFunction(record.label, run, on_finished=finished)
        task.setProperty("mhm_qgis_task_key", record.key)
        task.progressChanged.connect(self._task_progress_changed)
        record.task = task
        QgsApplication.taskManager().addTask(task)


    def _finish(self, key, success, payload, status=None):
        record = self._active.pop(str(key), None)
        if record is None:
            return
        self._finish_record(record, success, payload, status)

    def _finish_record(self, record, success, payload, status=None):
        record.status = status or ("completed" if success else "failed")
        record.progress = 100 if success else record.progress
        record.logs.append(
            f"Completed: {record.label}" if success else str(payload)
        )
        self._set_controls(record, True)
        if record.slot is not None:
            self._history[record.slot] = record
        record.task = None
        self.changed.emit()
        callback = record.on_success if success else record.on_error
        if callback is not None:
            try:
                callback(payload)
            except Exception as error:
                details = "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )
                record.status = "failed"
                record.logs.append(details)
                if success and record.on_error is not None:
                    try:
                        record.on_error(details)
                    except Exception:
                        record.logs.append(traceback.format_exc())
        self._start_available()

    def _set_controls(self, record, enabled):
        seen = set()
        for widget in record.controls:
            widget_id = id(widget)
            if widget_id in seen:
                continue
            seen.add(widget_id)
            owners = self._control_owners.setdefault(widget_id, set())
            if enabled:
                owners.discard(record.key)
            else:
                if not owners:
                    try:
                        self._control_states[widget_id] = widget.isEnabled()
                    except RuntimeError:
                        self._control_states[widget_id] = True
                owners.add(record.key)
            with contextlib.suppress(RuntimeError):
                widget.setEnabled(
                    False
                    if owners
                    else self._control_states.pop(widget_id, True)
                )
            if not owners:
                self._control_owners.pop(widget_id, None)
                self._control_states.pop(widget_id, None)


_CANCELLED = object()


def _off_qthread(work):
    """Run `work` on a plain thread and return its result, re-raising failures.

    pyproj must never be called from a `QgsTask` thread. QGIS keeps its PROJ
    context in thread-local storage and tears it down when a task thread ends,
    which leaves libproj's cached database context dangling for the next pooled
    thread; the second `proj_create` then segfaults the whole QGIS process
    (`projCppContext::getDatabaseContext`). A short-lived Python thread gets a
    clean context, and every background job goes through here, so no plugin
    code has to know. `isCanceled()` and `setProgress()` stay safe from it.
    """
    outcome = {}

    def call():
        try:
            outcome["value"] = work()
        except BaseException as error:  # re-raised on the QgsTask thread
            outcome["error"] = error

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


__all__ = ["TaskCoordinator", "TaskRecord"]
