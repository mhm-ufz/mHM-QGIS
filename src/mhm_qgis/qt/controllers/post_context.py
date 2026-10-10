"""The Outputs tab's context: run, basin, period, resolution and time.

Owns `dialog._post` (a `PostState` and its `AnalysisContext`). Selecting a run opens
it and scans what is stale on coordinator tasks (the scan itself runs in the native
worker); after that the pages read only the cache. Only the visible page refreshes
on `changed`; hidden pages collect the changed fields and refresh when shown (§9.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from qgis.PyQt.QtCore import QDate, QSignalBlocker, QTimer
from qgis.PyQt.QtWidgets import QInputDialog, QMenu, QMessageBox, QVBoxLayout

from ...core.post import cache, runs
from ..objects.analysis_context import AnalysisContext
from ..workflows import post as work

PLAY_MS = 300


@dataclass
class PostState:
    context: AnalysisContext
    refs: list = field(default_factory=list)
    run: object = None            # core.post.catalog.Run, variables already read
    root: Path | None = None      # the run's cache folder
    index: dict = field(default_factory=dict)
    intervals: object = None      # (T, 2) datetime64 of the mhm file
    axes: object = None
    gauges: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    exact: bool = False           # output step = model step: the balance is exact
    dirty: dict = field(default_factory=dict)
    widgets: dict = field(default_factory=dict)


def pages(dialog) -> dict:
    """Navigation text -> (page, refresh(dialog, fields))."""
    from . import post_balance, post_hydro, post_map, post_validation

    return {"Overview": (dialog.page_postOverview, post_balance.refresh_overview),
            "Map Explorer": (dialog.page_postMap, post_map.refresh),
            "Hydrograph": (dialog.page_postHydrograph, post_hydro.refresh),
            "Water balance": (dialog.page_postWaterBalance, post_balance.refresh_balance),
            "Processes": (dialog.page_postProcesses, post_balance.refresh_processes),
            "Signatures": (dialog.page_postSignatures, post_hydro.refresh_signatures),
            "Events": (dialog.page_postEvents, post_hydro.refresh_events),
            "Validation": (dialog.page_postValidation, post_validation.refresh),
            "Climatology": (dialog.page_postClimatology, post_hydro.refresh_climatology)}


def plot_widget(dialog, container, *, dates=True):
    """A pyqtgraph PlotWidget filling a placeholder widget of the form (made once).

    Pages make their plots only once a run is open: a dialog holding pyqtgraph items
    must be deleted by Qt (deleteLater), not left to Python's cycle collector, or the
    session teardown segfaults (seen in tests; the plugin deletes old dialogs)."""
    widgets = dialog._post.widgets
    if container.objectName() not in widgets:
        import pyqtgraph as pg

        plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)} if dates else None)
        plot.setBackground("w")
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.addLegend(offset=(5, 5))
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(plot)
        widgets[container.objectName()] = plot
    return widgets[container.objectName()]


def seconds(moments) -> np.ndarray:
    """datetime64 -> seconds since 1970 (pyqtgraph's date axis)."""
    return np.asarray(moments, dtype="datetime64[s]").astype("int64").astype(float)


def bind(dialog) -> None:
    from . import post_balance, post_hydro, post_map, post_validation

    dialog._post = PostState(AnalysisContext(dialog))
    context = dialog._post.context
    context.changed.connect(lambda name: changed(dialog, name))
    # comboBox_postRun → run_selected; its context menu archives the current run
    dialog.comboBox_postRun.activated.connect(lambda _index: run_selected(dialog))
    dialog.comboBox_postRun.customContextMenuRequested.connect(lambda point: run_menu(dialog, point))
    # comboBox_postBasin → basin_selected
    dialog.comboBox_postBasin.activated.connect(lambda _index: basin_selected(dialog))
    dialog.dateEdit_postStart.dateChanged.connect(lambda _date: period_changed(dialog))
    dialog.dateEdit_postEnd.dateChanged.connect(lambda _date: period_changed(dialog))
    dialog.comboBox_postResolution.currentTextChanged.connect(
        lambda text: context.set("resolution", text.lower()))
    # pushButton_postRefresh → populate (rescan)
    dialog.pushButton_postRefresh.clicked.connect(lambda: populate(dialog, rescan=True))
    dialog.listWidget_postNavigation.currentRowChanged.connect(lambda _row: page_selected(dialog))
    debounce = QTimer(dialog)
    debounce.setSingleShot(True)
    debounce.setInterval(50)
    debounce.timeout.connect(lambda: context.set("current_time", dialog.horizontalSlider_postTime.value()))
    dialog.horizontalSlider_postTime.valueChanged.connect(lambda _value: debounce.start())
    dialog.horizontalSlider_postTime.valueChanged.connect(lambda value: _show_time(dialog, value))
    dialog.toolButton_postStepBack.clicked.connect(lambda: step(dialog, -1))
    dialog.toolButton_postStepForward.clicked.connect(lambda: step(dialog, 1))
    player = QTimer(dialog)
    player.setInterval(PLAY_MS)
    player.timeout.connect(lambda: step(dialog, 1, playing=True))
    dialog.toolButton_postPlay.toggled.connect(lambda on: player.start() if on else player.stop())
    dialog.task_coordinator.changed.connect(lambda: work.show_progress(dialog))
    context.set("resolution", "native")
    post_map.bind(dialog)
    post_hydro.bind(dialog)
    post_balance.bind(dialog)
    post_validation.bind(dialog)
    dialog.listWidget_postNavigation.setCurrentRow(1)


def clear(dialog) -> None:
    """Forget the open run (another setup was opened)."""
    state = dialog._post
    for name in ("run", "root", "intervals", "axes"):
        setattr(state, name, None)
    state.refs, state.gauges, state.findings, state.index, state.dirty = [], [], [], {}, {}
    with QSignalBlocker(state.context):
        state.context.clear()
        state.context.set("resolution", dialog.comboBox_postResolution.currentText().lower())
    dialog.comboBox_postRun.clear()
    dialog.comboBox_postBasin.clear()
    for _page, refresh in pages(dialog).values():
        refresh(dialog, {"run"})


def populate(dialog, rescan=False) -> None:
    """List the runs of the open setup; reopen the selected one when asked or when new."""
    state = dialog._post
    if not dialog.project_folder:
        clear(dialog)
        return
    try:
        refs = runs.list_runs(dialog.project_folder)
    except (OSError, ValueError) as error:
        dialog.log_message(f"WARNING: Could not list the runs: {error}")
        refs = []
    current = state.context.get("run")
    combo = dialog.comboBox_postRun
    with QSignalBlocker(combo):
        combo.clear()
        for ref in refs:
            combo.addItem(ref.label)
        if not refs:
            combo.addItem("No run yet: run mHM first")
        combo.setCurrentIndex(refs.index(current) if current in refs else 0)
    state.refs = refs
    if refs and (rescan or current not in refs):
        run_selected(dialog)


def run_selected(dialog, series=None) -> None:
    from ...core.executions import post as post_exec

    state = dialog._post
    index = dialog.comboBox_postRun.currentIndex()
    if not 0 <= index < len(state.refs):
        return
    ref = state.refs[index]
    options = {} if series is None else {"series": series}

    def open_run(_task):
        from ...core.post import catalog
        from ...core.post import series as series_module

        run = catalog.open_run(ref)
        findings = run.findings()
        job = post_exec.scan_job(ref, run=run, **options) if run.exists("mhm") else None
        step = run.model_step_days
        exact = bool(job and step and run.step_days <= step + 1e-9)
        run.variables  # read here: the pages use them on the GUI thread  # noqa: B018
        return run, findings, job, series_module.gauges(run), exact

    work.submit(dialog, "open", f"Opening {ref.label}", open_run, lambda result: _opened(dialog, ref, *result))


def _opened(dialog, ref, run, findings, job, gauges, exact) -> None:
    from ...core.executions import post as post_exec

    if job is None:
        dialog._post.findings = findings
        dialog.textBrowser_postOverview.setHtml("<p>" + "<br>".join(findings) + "</p>")
        return
    if job["series_bytes"] > cache.ASK_ABOVE_BYTES and job["series"]:
        answer = QMessageBox.question(
            dialog, "Series cache",
            f"The series cache for {', '.join(job['series'])} needs {job['series_bytes'] / 1e9:.1f} GB. "
            "Build it? Without it, cell series are read from the file on demand (slow).")
        if answer != QMessageBox.StandardButton.Yes:
            run_selected(dialog, series=())
            return
    if not job["variables"]:
        _ready(dialog, ref, run, findings, gauges, exact)
        return
    work.submit(dialog, "scan", f"Scanning {ref.label}", lambda task: post_exec.scan(task, job),
                lambda _summary: _ready(dialog, ref, run, findings, gauges, exact))


def _ready(dialog, ref, run, findings, gauges, exact) -> None:
    from ...core.post import grid, series

    state = dialog._post
    state.run, state.root, state.findings, state.gauges, state.exact = run, cache.folder(ref), findings, gauges, exact
    state.index = cache.load_index(state.root)
    state.intervals = series.bounds(state.root)
    state.axes = grid.axes_from_cache(state.root)
    first, last = state.intervals[0, 0], state.intervals[-1, 1] - np.timedelta64(1, "s")
    for editor in (dialog.dateEdit_postStart, dialog.dateEdit_postEnd):
        with QSignalBlocker(editor):
            editor.setDateRange(_qdate(first), _qdate(last))
    with QSignalBlocker(dialog.dateEdit_postStart), QSignalBlocker(dialog.dateEdit_postEnd):
        dialog.dateEdit_postStart.setDate(_qdate(first))
        dialog.dateEdit_postEnd.setDate(_qdate(last))
    slider = dialog.horizontalSlider_postTime
    with QSignalBlocker(slider):
        slider.setRange(0, len(state.intervals) - 1)
        slider.setValue(0)
    _show_time(dialog, 0)
    names = next(iter(state.index["variables"].values()), {}).get("basins", ["domain"])
    combo = dialog.comboBox_postBasin
    with QSignalBlocker(combo):
        combo.clear()
        for name in names:
            combo.addItem("Domain" if name == "domain" else f"Gauge {name}", name)
    context = state.context
    with QSignalBlocker(context):
        context.set("period", None)
        context.set("basin", names[0] if names else "domain")
        context.set("gauge", gauges[0] if gauges else None)
        context.set("current_time", 0)
        context.set("cell", None)
    if not context.set("run", ref):
        changed(dialog, "run")  # the same run, rescanned


def changed(dialog, name) -> None:
    state = dialog._post
    visible = dialog.stackedWidget_post.currentWidget()
    for page, refresh in pages(dialog).values():
        if page is visible:
            refresh(dialog, {name})
        else:
            state.dirty.setdefault(page, set()).add(name)
    if name == "current_time" and state.intervals is not None:
        slider = dialog.horizontalSlider_postTime
        with QSignalBlocker(slider):
            slider.setValue(int(state.context.get("current_time") or 0))
        _show_time(dialog, slider.value())


def page_selected(dialog) -> None:
    item = dialog.listWidget_postNavigation.currentItem()
    entry = pages(dialog).get(item.text()) if item is not None else None
    if entry is None:
        return
    page, refresh = entry
    dialog.stackedWidget_post.setCurrentWidget(page)
    fields = dialog._post.dirty.pop(page, set())
    if fields:
        refresh(dialog, fields)


def basin_selected(dialog) -> None:
    name = dialog.comboBox_postBasin.currentData()
    context = dialog._post.context
    context.set("basin", name)
    if name not in (None, "domain"):
        context.set("gauge", int(name))


def period_changed(dialog) -> None:
    state = dialog._post
    if state.intervals is None:
        return
    start = np.datetime64(dialog.dateEdit_postStart.date().toString("yyyy-MM-dd"))
    end = np.datetime64(dialog.dateEdit_postEnd.date().addDays(1).toString("yyyy-MM-dd"))
    whole = start <= state.intervals[0, 0] and end >= state.intervals[-1, 1]
    state.context.set("period", None if whole else (str(start), str(end)))


def step(dialog, delta, playing=False) -> None:
    slider = dialog.horizontalSlider_postTime
    if playing and slider.value() >= slider.maximum():
        dialog.toolButton_postPlay.setChecked(False)
        return
    slider.setValue(slider.value() + delta)


def _show_time(dialog, value) -> None:
    intervals = dialog._post.intervals
    if intervals is None or not 0 <= value < len(intervals):
        return
    from qgis.PyQt.QtCore import QDateTime, Qt

    with QSignalBlocker(dialog.dateTimeEdit_postTime):
        dialog.dateTimeEdit_postTime.setDateTime(
            QDateTime.fromString(str(intervals[value, 0].astype("datetime64[s]")), Qt.DateFormat.ISODate))


def _qdate(moment) -> QDate:
    return QDate.fromString(str(np.datetime64(moment, "D")), "yyyy-MM-dd")


def run_menu(dialog, point) -> None:
    menu = QMenu(dialog)
    action = menu.addAction("Archive current run…")
    if menu.exec(dialog.comboBox_postRun.mapToGlobal(point)) is action:
        archive(dialog)


def archive(dialog) -> None:
    """Move output/ into runs/<name>/ (refused while mHM runs)."""
    from . import workflow_state

    if workflow_state.running_key(dialog) == "run_mhm":
        QMessageBox.information(dialog, "Archive run", "mHM is running; archive the run when it has finished.")
        return
    name, ok = QInputDialog.getText(dialog, "Archive run", "Name of the archived run:")
    if not ok or not name.strip():
        return
    note, _ = QInputDialog.getText(dialog, "Archive run", "Note (optional):")
    folder = dialog.project_folder

    def done(refs):
        dialog.log_message(f"Archived the current run as {name.strip()}.")
        populate(dialog)
        if refs:
            dialog.comboBox_postRun.setCurrentIndex(dialog._post.refs.index(refs[0]))
            run_selected(dialog)

    work.submit(dialog, "archive", "Archiving the run", lambda _task: runs.archive(folder, name.strip(), note),
                done, lambda details: QMessageBox.warning(dialog, "Archive run", str(details).splitlines()[-1]))
