"""Hydrograph: observed and simulated discharge at a gauge, P on top, its scores.

The gauge series read netCDF and the scores import mhm-tools (KGE), so both run on a
task. The vertical cursor is the shared time: dragging it moves the map's step.
"""
from __future__ import annotations

import numpy as np
from qgis.PyQt.QtCore import QSignalBlocker, Qt
from qgis.PyQt.QtWidgets import QTableWidgetItem

from ..workflows import post as work
from .post_context import plot_widget, seconds

FREQ = {"day": "D", "month": "MS", "year": "YS"}
SHOWN = ("NSE", "KGE", "KGE'", "logNSE", "r", "alpha", "beta", "PBIAS", "volume error", "RMSE", "MAE", "pairs")


def bind(dialog) -> None:
    dialog.comboBox_postGauge.activated.connect(lambda _index: _gauge_chosen(dialog))
    dialog.checkBox_postLogY.toggled.connect(
        lambda on: plot_widget(dialog, dialog.widget_postHydrographPlot).setLogMode(y=on))


def _gauge_chosen(dialog) -> None:
    context = dialog._post.context
    gauge = dialog.comboBox_postGauge.currentData()
    context.set("gauge", gauge)
    basins = next(iter(dialog._post.index["variables"].values()), {}).get("basins", [])
    if str(gauge) in basins:
        context.set("basin", str(gauge))


def _plots(dialog):
    import pyqtgraph as pg

    state = dialog._post
    main = plot_widget(dialog, dialog.widget_postHydrographPlot)
    if "rain" not in state.widgets:
        main.setLabel("left", "Discharge", units="m³/s")
        main.getAxis("left").enableAutoSIPrefix(False)
        rain = pg.ViewBox()
        main.showAxis("right")
        main.scene().addItem(rain)
        main.getAxis("right").linkToView(rain)
        main.getAxis("right").setLabel("P (mm)")
        rain.setXLink(main)
        rain.invertY(True)
        main.getViewBox().sigResized.connect(lambda: rain.setGeometry(main.getViewBox().sceneBoundingRect()))
        cursor = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#e31a1c", width=1.5))
        cursor.sigPositionChangeFinished.connect(lambda line: _cursor_moved(dialog, line.value()))
        state.widgets.update(rain=rain, cursor=cursor)
    scatter = plot_widget(dialog, dialog.widget_postHydrographScatter, dates=False)
    residuals = plot_widget(dialog, dialog.widget_postHydrographResiduals)
    return main, state.widgets["rain"], state.widgets["cursor"], scatter, residuals


def refresh(dialog, fields) -> None:
    state = dialog._post
    if state.root is None:  # plots are made only once a run is open (see plot_widget)
        for name in ("widget_postHydrographPlot", "widget_postHydrographScatter", "widget_postHydrographResiduals",
                     "rain"):
            if name in state.widgets:
                state.widgets[name].clear()
        dialog.comboBox_postGauge.clear()
        dialog.tableWidget_postHydrographMetrics.setRowCount(0)
        return
    cursor = _plots(dialog)[2]
    if "run" in fields:
        with QSignalBlocker(dialog.comboBox_postGauge):
            dialog.comboBox_postGauge.clear()
            for gauge in state.gauges:
                dialog.comboBox_postGauge.addItem(f"Gauge {gauge}", gauge)
    gauge = state.context.get("gauge")
    if gauge is not None:
        with QSignalBlocker(dialog.comboBox_postGauge):
            dialog.comboBox_postGauge.setCurrentIndex(max(0, dialog.comboBox_postGauge.findData(gauge)))
    if fields & {"run", "gauge", "period", "resolution"}:
        _load(dialog, gauge)
    if fields & {"run", "current_time"}:
        step = int(state.context.get("current_time") or 0)
        with QSignalBlocker(cursor):
            cursor.setValue(seconds(state.intervals[step, 0])[()])


def _load(dialog, gauge) -> None:
    state = dialog._post
    if gauge is None:
        return
    run, root, context = state.run, state.root, state.context
    period, resolution = context.get("period"), context.get("resolution") or "native"
    basins = next(iter(state.index["variables"].values()), {}).get("basins", [])
    basin = str(gauge) if str(gauge) in basins else "domain"

    def read(_task):
        from ...core.post import aggregate, metrics, series

        frame = series.gauge(run, gauge, period)
        if resolution in FREQ:
            frame = frame.resample(FREQ[resolution]).mean()
        rain = None
        if "pre" in state.index["variables"]:
            try:
                rain = aggregate.temporal(root, "pre", "sum", resolution if resolution in FREQ else "native",
                                          basin=basin, period=period)
            except ValueError:  # finer than the output step: the bars are left out
                rain = None
        return frame, rain, metrics.evaluate(frame["sim"], frame["obs"])

    work.submit(dialog, "hydrograph", f"Reading gauge {gauge}", read, lambda result: _draw(dialog, gauge, *result))


def _draw(dialog, gauge, frame, rain, scores) -> None:
    import pyqtgraph as pg

    main, rain_box, cursor, scatter, residuals = _plots(dialog)
    for plot in (main, scatter, residuals):
        plot.clear()
    rain_box.clear()
    times = seconds(frame.index)
    if rain is not None and len(rain):
        width = float(np.median(np.diff(seconds(rain.index)))) if len(rain) > 1 else 86400.0
        rain_box.addItem(pg.BarGraphItem(x=seconds(rain.index) + width / 2, height=rain.to_numpy(), width=width * 0.9,
                                         brush=pg.mkBrush(70, 130, 180, 90), pen=None))
    main.plot(times, frame["obs"].to_numpy(), pen=pg.mkPen("k", width=1.2), name="Observed", connect="finite")
    main.plot(times, frame["sim"].to_numpy(), pen=pg.mkPen("#e6550d", width=1.2), name="Simulated", connect="finite")
    main.addItem(cursor)
    main.setTitle(f"Gauge {gauge}")
    paired = frame.dropna()
    if len(paired):
        scatter.plot(paired["obs"].to_numpy(), paired["sim"].to_numpy(), pen=None, symbol="o", symbolSize=4,
                     symbolBrush=pg.mkBrush(230, 85, 13, 120))
        top = float(paired.max().max())
        scatter.plot([0, top], [0, top], pen=pg.mkPen("#636363", style=Qt.PenStyle.DashLine))
        scatter.setLabel("bottom", "Observed")
        scatter.setLabel("left", "Simulated")
    residuals.plot(times, (frame["sim"] - frame["obs"]).to_numpy(), pen=pg.mkPen("#756bb1"), connect="finite")
    residuals.setLabel("left", "Sim - obs")
    table = dialog.tableWidget_postHydrographMetrics
    rows = [(name, scores.get(name)) for name in SHOWN] + ([("Note", scores["reason"])] if scores["reason"] else [])
    table.setRowCount(len(rows))
    for row, (name, value) in enumerate(rows):
        table.setItem(row, 0, QTableWidgetItem(name))
        table.setItem(row, 1, QTableWidgetItem(value if isinstance(value, str) else f"{value:.3g}"))


def _cursor_moved(dialog, position) -> None:
    """The cursor's date -> the output interval holding it -> the shared time."""
    state = dialog._post
    if state.intervals is None:
        return
    starts = seconds(state.intervals[:, 0])
    step = int(np.clip(np.searchsorted(starts, position, "right") - 1, 0, len(starts) - 1))
    state.context.set("current_time", step)
