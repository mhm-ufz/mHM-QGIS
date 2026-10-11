"""The Outputs tab's context: run, basin, period, resolution, the views and time.

Owns `dialog._post` (a `PostState` and its `AnalysisContext`). Selecting a run opens
it and scans what is stale on coordinator tasks (the scan itself runs in the native
worker); after that the views read only the cache. The Overview lives in the tab; the
other views are a central panel (over the map canvas) and left docks in QGIS's main
window or the standalone map window (`qt/docks/host.py`): selecting one in the
navigation list arranges the window for it. Only visible panels and docks refresh on
`changed`; hidden ones collect the changed fields and refresh when shown (post plan
§0c, §9.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from qgis.PyQt.QtCore import QDate, QSignalBlocker, QTimer
from qgis.PyQt.QtWidgets import QInputDialog, QMenu, QMessageBox, QVBoxLayout

from ...core.post import cache, runs
from ..docks import host
from ..objects.analysis_context import AnalysisContext
from ..workflows import post as work

PLAY_MS = 300
MAP_DOCKS = ("map", "map_controls", "cell_inspector")  # one controller (post_map) drives all three
TOGETHER = (MAP_DOCKS, ("compare", "compare_maps"))  # parts made (and bound) together


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
    dirty: dict = field(default_factory=dict)      # refresh function -> fields changed while hidden
    widgets: dict = field(default_factory=dict)    # plots, layers, flags made by the controllers
    ui: SimpleNamespace = field(default_factory=SimpleNamespace)  # the panels' and docks' widgets, by name
    panels: dict = field(default_factory=dict)     # central panel name -> page over the canvas
    docks: dict = field(default_factory=dict)      # side dock name -> QgsDockWidget
    toolbar: object = None
    view: str | None = None
    workspace: bool = False


def refreshers():
    """(refresh(dialog, fields), the panels and docks it draws on; None: the tab's Overview)."""
    from . import post_advanced, post_balance, post_hydro, post_map, post_validation

    return ((post_map.refresh, MAP_DOCKS),
            (post_balance.refresh_overview, None),
            (post_hydro.refresh, ("hydrograph",)),
            (post_balance.refresh_balance, ("water_balance",)),
            (post_balance.refresh_processes, ("processes",)),
            (post_hydro.refresh_signatures, ("signatures",)),
            (post_hydro.refresh_events, ("events",)),
            (post_validation.refresh, ("validation",)),
            (post_hydro.refresh_climatology, ("climatology",)),
            (post_advanced.refresh_compare, ("compare", "compare_maps")),
            (post_advanced.refresh_extremes, ("extremes",)),
            (post_advanced.refresh_drought, ("drought",)),
            (post_advanced.refresh_anomalies, ("anomalies",)),
            (post_advanced.refresh_summary, ("summary",)),
            (post_advanced.refresh_why_here, ("why_here",)))


def plot_widget(dialog, container, *, dates=True):
    """A pyqtgraph PlotWidget filling a placeholder widget of a dock form (made once).

    Plots are made only once a run is open: a widget holding pyqtgraph items must be
    deleted by Qt (deleteLater), not left to Python's cycle collector, or the session
    teardown segfaults (seen in tests); the docks are deleted with deleteLater."""
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
    # listWidget_postNavigation → view_selected (arranges the host window for the view)
    dialog.listWidget_postNavigation.currentRowChanged.connect(lambda _row: view_selected(dialog))
    dialog.listWidget_postNavigation.itemClicked.connect(lambda _item: view_selected(dialog))  # brings a panel back
    # pushButton_postWorkspace → workspace on/off; pushButton_postResetLayout → reset_layout
    dialog.pushButton_postWorkspace.toggled.connect(lambda on: workspace(dialog, on))
    dialog.pushButton_postResetLayout.clicked.connect(lambda: reset_layout(dialog))
    # toolButton_postExport → its menu: export the shown view, archive the current run
    from . import post_export

    post_export.bind(dialog)
    dialog.task_coordinator.changed.connect(lambda: work.show_progress(dialog))
    context.set("resolution", "native")


def _created(dialog, name) -> None:
    """Bind a new panel's or dock's widgets, and refresh it in full when it is first shown."""
    from . import post_advanced, post_balance, post_hydro, post_map, post_validation

    state = dialog._post
    for group in TOGETHER:
        if name in group:
            for other in group:
                host.part(dialog, other, _created)
    if name in MAP_DOCKS and not state.widgets.get("map_bound"):
        state.widgets["map_bound"] = True
        post_map.bind(dialog)
    binders = {"hydrograph": post_hydro.bind, "water_balance": post_balance.bind,
               "validation": post_validation.bind, "compare": post_advanced.bind_compare,
               "extremes": post_advanced.bind_extremes, "drought": post_advanced.bind_drought,
               "anomalies": post_advanced.bind_anomalies, "summary": post_advanced.bind_summary}
    if name in binders:
        binders[name](dialog)
    if name in state.docks:  # a panel's showing is reported by _show_view
        state.docks[name].visibilityChanged.connect(lambda visible: visible and dock_shown(dialog, name))
    for refresh, docks in refreshers():
        if docks and name in docks:
            state.dirty.setdefault(refresh, set()).add("run")


def _toolbar_created(dialog) -> None:
    """The time controls: slider (debounced), steps, play."""
    ui, context = dialog._post.ui, dialog._post.context
    debounce = QTimer(dialog._post.toolbar)
    debounce.setSingleShot(True)
    debounce.setInterval(50)
    debounce.timeout.connect(lambda: context.set("current_time", ui.horizontalSlider_postTime.value()))
    ui.horizontalSlider_postTime.valueChanged.connect(lambda _value: debounce.start())
    ui.horizontalSlider_postTime.valueChanged.connect(lambda value: _show_time(dialog, value))
    ui.toolButton_postStepBack.clicked.connect(lambda: step(dialog, -1))
    ui.toolButton_postStepForward.clicked.connect(lambda: step(dialog, 1))
    player = QTimer(dialog._post.toolbar)
    player.setInterval(PLAY_MS)
    player.timeout.connect(lambda: step(dialog, 1, playing=True))
    ui.toolButton_postPlay.toggled.connect(lambda on: player.start() if on else player.stop())
    _sync_time_bar(dialog)


def workspace(dialog, on) -> None:
    """Turn the analysis layout on (keep the user's layout) or off (restore it)."""
    state = dialog._post
    if on == state.workspace:
        return
    with QSignalBlocker(dialog.pushButton_postWorkspace):
        dialog.pushButton_postWorkspace.setChecked(on)
    if on:
        state.workspace = True
        host.begin(dialog)
        host.time_toolbar(dialog, _toolbar_created)
        view = host.view_for(getattr(dialog.listWidget_postNavigation.currentItem(), "text", lambda: "")())
        if view:
            _show_view(dialog, view)
        return
    from . import post_map

    host.save_view(dialog, state.view)
    post_map.leave(dialog)
    host.end(dialog)
    state.workspace, state.view, state.dirty = False, None, {}
    state.ui = SimpleNamespace()
    state.widgets = {"pending": state.widgets.get("pending", {})}


def view_selected(dialog) -> None:
    item = dialog.listWidget_postNavigation.currentItem()
    view = host.view_for(item.text()) if item is not None else None
    if view is None:
        return
    if not dialog._post.workspace:
        workspace(dialog, True)  # applies the selected view
        return
    _show_view(dialog, view)


def _show_view(dialog, view) -> None:
    from . import post_map

    state = dialog._post
    if state.view == view:  # QGIS may have shown the canvas (a layer was added): back to the panel
        host.show_central(dialog, view)
    else:
        host.save_view(dialog, state.view)
        host.apply(dialog, view, _created)
        state.view = view
    post_map.view_changed(dialog, view)
    if host.central(view) != "canvas":
        dock_shown(dialog, host.central(view))


def open_dock(dialog, name, beside=None) -> None:
    """Show a dock outside the view's layout ("Why here?") as a left tab, beside `beside`
    when that is shown."""
    created = host.part(dialog, name, _created)
    other = dialog._post.docks.get(beside)
    host.add_left(dialog, created, other if other is not None and other.isVisible() else None)
    created.raise_()


def reset_layout(dialog) -> None:
    state = dialog._post
    if state.workspace and state.view:
        host.forget_view(dialog, state.view)
        host.apply(dialog, state.view, _created, reset=True)


def end_workspace(dialog) -> None:
    """For closing a setup or unloading the plugin."""
    if getattr(dialog, "_post", None) is not None and dialog._post.workspace:
        workspace(dialog, False)


def _made(dialog, name):
    return dialog._post.panels.get(name) or dialog._post.docks.get(name)


def _visible(dialog, name) -> bool:
    made = _made(dialog, name)
    return made is not None and made.isVisible()


def changed(dialog, name) -> None:
    state = dialog._post
    for refresh, parts in refreshers():
        if parts is None or any(_visible(dialog, part) for part in parts):
            refresh(dialog, {name})
        elif any(_made(dialog, part) is not None for part in parts):
            state.dirty.setdefault(refresh, set()).add(name)
    if name == "current_time":
        _sync_time_bar(dialog)


def dock_shown(dialog, name) -> None:
    state = dialog._post
    for refresh, parts in refreshers():
        if parts and name in parts and state.dirty.get(refresh):
            refresh(dialog, state.dirty.pop(refresh))


def clear(dialog) -> None:
    """Forget the open run (another setup was opened); the analysis layout ends."""
    state = dialog._post
    end_workspace(dialog)
    for name in ("run", "root", "intervals", "axes"):
        setattr(state, name, None)
    state.refs, state.gauges, state.findings, state.index, state.dirty = [], [], [], {}, {}
    with QSignalBlocker(state.context):
        state.context.clear()
        state.context.set("resolution", dialog.comboBox_postResolution.currentText().lower())
    dialog.comboBox_postRun.clear()
    dialog.comboBox_postBasin.clear()
    from . import post_balance

    post_balance.refresh_overview(dialog, {"run"})


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
        dialog.log_message("Post-processing: " + " ".join(findings))
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
    _sync_time_bar(dialog)
    if not context.set("run", ref):
        changed(dialog, "run")  # the same run, rescanned


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
    slider = getattr(dialog._post.ui, "horizontalSlider_postTime", None)
    if slider is None:
        return
    if playing and slider.value() >= slider.maximum():
        dialog._post.ui.toolButton_postPlay.setChecked(False)
        return
    slider.setValue(slider.value() + delta)


def _sync_time_bar(dialog) -> None:
    """Put the time toolbar (when shown) on the run's intervals and the current time."""
    state = dialog._post
    slider = getattr(state.ui, "horizontalSlider_postTime", None)
    if slider is None or state.intervals is None:
        return
    with QSignalBlocker(slider):
        slider.setRange(0, len(state.intervals) - 1)
        slider.setValue(int(state.context.get("current_time") or 0))
    _show_time(dialog, slider.value())


def _show_time(dialog, value) -> None:
    intervals, ui = dialog._post.intervals, dialog._post.ui
    if intervals is None or not 0 <= value < len(intervals) or not hasattr(ui, "dateTimeEdit_postTime"):
        return
    from qgis.PyQt.QtCore import QDateTime, Qt

    with QSignalBlocker(ui.dateTimeEdit_postTime):
        ui.dateTimeEdit_postTime.setDateTime(
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
