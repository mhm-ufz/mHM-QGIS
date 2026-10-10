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


# --- Signatures, Events, Climatology (Tier B) -------------------------------------


def _area_km2(state, gauge):
    """A gauge catchment's area from its basin weights, else the domain's."""
    basins = next(iter(state.index["variables"].values()), {}).get("basins", [])
    name = str(gauge) if str(gauge) in basins else "domain"
    return float((np.load(state.root / "weights" / f"{name}.npy") * np.load(state.root / "area.npy")).sum()) / 1e6, name


def _gauge_job(dialog, key, label, compute, draw):
    """Read the context gauge's series (and its basin P) on a task, then draw."""
    state = dialog._post
    gauge, period = state.context.get("gauge"), state.context.get("period")
    if gauge is None:
        draw(None)
        return
    run, root = state.run, state.root
    area, basin = _area_km2(state, gauge)
    scanned_rain = "pre" in state.index["variables"]

    def read(_task):
        from ...core.post import series

        frame = series.gauge(run, gauge, period)
        raw = series.precipitation(root, basin, period) if scanned_rain else None
        rain = raw
        if raw is not None and not raw.index.equals(frame.index):  # only at the discharge's own step
            same_step = len(raw) > 1 and np.median(np.diff(raw.index.values)) == np.median(np.diff(frame.index.values))
            rain = raw.reindex(frame.index) if same_step else None
        return compute(frame, rain, area, raw)

    work.submit(dialog, key, label, read, draw)


def refresh_signatures(dialog, fields) -> None:
    import pyqtgraph as pg

    state = dialog._post
    table = dialog.tableWidget_postSignatures
    if state.root is None:
        table.setRowCount(0)
        dialog.label_postSignaturesNotes.setText("")
        return
    if not fields & {"run", "gauge", "period"}:
        return

    def compute(frame, _rain, area, raw):
        from ...core.post import signatures

        reason = signatures.usable(frame["sim"])
        if reason:
            return reason, None, None
        observed = signatures.usable(frame["obs"]) == ""
        found = {name: signatures.signatures(frame[name], raw, area) for name in ("sim", *(["obs"] if observed else []))}
        curves = {name: signatures.fdc(frame[name].to_numpy()) for name in found}
        compared = signatures.compare(found.get("obs", dict.fromkeys(found["sim"], np.nan)), found["sim"])
        return "" if observed else "No observations: simulated signatures only.", compared, curves

    def draw(result):
        plot = plot_widget(dialog, dialog.widget_postSignaturesFdc, dates=False)
        plot.clear()
        if result is None:
            return
        reason, compared, curves = result
        dialog.label_postSignaturesNotes.setText(reason)
        if compared is None:
            table.setRowCount(0)
            return
        plot.setLogMode(y=True)
        plot.setLabel("bottom", "Exceedance probability")
        plot.setLabel("left", "Discharge (m³/s)")
        for name, colour in (("obs", "k"), ("sim", "#e6550d")):
            if name in curves:
                p, flows = curves[name]
                keep = flows > 0
                plot.plot(p[keep], flows[keep], pen=pg.mkPen(colour, width=1.5),
                          name="Observed" if name == "obs" else "Simulated")
        table.setRowCount(len(compared))
        table.setVerticalHeaderLabels(list(compared.index))
        for row, (_, values) in enumerate(compared.iterrows()):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem("" if not np.isfinite(value) else f"{value:,.3g}"))

    _gauge_job(dialog, "signatures", "Computing signatures", compute, draw)


def refresh_events(dialog, fields) -> None:
    state = dialog._post
    table = dialog.tableWidget_postEvents
    if state.root is None:
        table.setRowCount(0)
        state.widgets.pop("events", None)
        return
    if "events_bound" not in state.widgets:
        from ...core.post.events import RANKINGS

        dialog.comboBox_postEventRank.addItems(RANKINGS)
        dialog.comboBox_postEventRank.setCurrentText("observed peak")
        dialog.comboBox_postEventRank.activated.connect(lambda _index: _show_events(dialog))
        table.currentCellChanged.connect(lambda row, *_: _draw_event(dialog, row))
        dialog.toolButton_postEventPrevious.clicked.connect(lambda: table.selectRow(max(0, table.currentRow() - 1)))
        dialog.toolButton_postEventNext.clicked.connect(
            lambda: table.selectRow(min(table.rowCount() - 1, table.currentRow() + 1)))
        state.widgets["events_bound"] = True
    if not fields & {"run", "gauge", "period"}:
        return

    def compute(frame, rain, area, _raw):
        from ...core.post import events, signatures

        reason = signatures.usable(frame["sim"])
        if reason:
            return reason.replace("Signatures", "Events"), None, None, None
        found = events.table(frame, rain=rain, area_km2=area)
        notes = [] if frame["obs"].notna().any() else ["No observations: events from the simulation, errors hidden."]
        if rain is None:
            notes.append("No precipitation at the gauge's step: rainfall columns are left out.")
        return " ".join(notes), found, frame, rain

    def draw(result):
        if result is None:
            return
        notes, found, frame, rain = result
        dialog.label_postEventsNotes.setText(notes)
        state.widgets["events"] = (found, frame, rain)
        _show_events(dialog)

    _gauge_job(dialog, "events", "Finding events", compute, draw)


def _show_events(dialog) -> None:
    from ...core.post import events

    table = dialog.tableWidget_postEvents
    found = dialog._post.widgets.get("events")
    if not found or found[0] is None:
        table.setRowCount(0)
        return
    ranked = events.ranked(found[0], dialog.comboBox_postEventRank.currentText())
    dialog._post.widgets["events_ranked"] = ranked
    columns = [c for c in ranked.columns if not c.startswith("abs ")]
    with QSignalBlocker(table):
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setRowCount(len(ranked))
        for row, (_, values) in enumerate(ranked.iterrows()):
            for column, name in enumerate(columns):
                value = values[name]
                text = str(value)[:10] if name in ("start", "end") else (
                    "" if value is None or (isinstance(value, float) and not np.isfinite(value)) else f"{value:,.3g}")
                table.setItem(row, column, QTableWidgetItem(text))
    if len(ranked):
        table.selectRow(0)
        _draw_event(dialog, 0)


def _draw_event(dialog, row) -> None:
    import pyqtgraph as pg

    state = dialog._post
    ranked, (_, frame, rain) = state.widgets.get("events_ranked"), state.widgets.get("events", (None,) * 3)
    plot = plot_widget(dialog, dialog.widget_postEventsPlot)
    plot.clear()
    if ranked is None or not 0 <= row < len(ranked):
        return
    event = ranked.iloc[row]
    margin = np.timedelta64(5, "D")
    window = frame[(frame.index >= event["start"] - margin) & (frame.index <= event["end"] + margin)]
    times = seconds(window.index)
    plot.plot(times, window["obs"].to_numpy(), pen=pg.mkPen("k", width=1.4), name="Observed", connect="finite")
    plot.plot(times, window["sim"].to_numpy(), pen=pg.mkPen("#e6550d", width=1.4), name="Simulated",
              connect="finite")
    if rain is not None:
        bars = rain.reindex(window.index).to_numpy()
        flows = window[["obs", "sim"]].to_numpy()
        top = float(np.nanmax(flows)) if np.isfinite(flows).any() else 1.0
        scale = top / max(float(np.nanmax(bars)), 1e-9) / 2
        plot.addItem(pg.BarGraphItem(x=times + 43200, height=-np.nan_to_num(bars) * scale, y0=top * 1.6,
                                     width=86400 * 0.9, brush=pg.mkBrush(70, 130, 180, 110), pen=None))
    plot.setTitle(f"Event {str(event['start'])[:10]} to {str(event['end'])[:10]}")


def refresh_climatology(dialog, fields) -> None:
    import pyqtgraph as pg

    state = dialog._post
    if state.root is None:
        if "climatology" in state.widgets:
            state.widgets["climatology"].clear()
        dialog.label_postClimatologyNotes.setText("")
        return
    if not fields & {"run", "basin", "period"}:
        return
    from ...core.post import aggregate, climatology

    if "climatology" not in state.widgets:
        from qgis.PyQt.QtWidgets import QVBoxLayout

        view = pg.GraphicsLayoutWidget()
        view.setBackground("w")
        QVBoxLayout(dialog.widget_postClimatologyPlot).addWidget(view)
        state.widgets["climatology"] = view
    view = state.widgets["climatology"]
    view.clear()
    basin, period = state.context.get("basin") or "domain", state.context.get("period")
    notes = []
    names = [(name, label) for name, label in (("pre", "P"), ("aET", "ET"), ("Q", "Q"), ("snowpack", "Snowpack"),
                                               ("SM_Lall", "Soil moisture")) if name in state.index["variables"]]
    months = np.arange(1, 13)
    annual = {}
    for name, label in names:
        how = "sum" if state.index["variables"][name].get("kind") == "flux" else "mean"
        try:
            monthly = aggregate.temporal(state.root, name, how, "month", basin=basin, period=period)
        except ValueError as error:
            notes.append(str(error))
            continue
        reason = climatology.usable(monthly)
        if reason:
            dialog.label_postClimatologyNotes.setText(reason)
            return
        stats = climatology.monthly(monthly)
        plot = view.addPlot(title=f"{label} by calendar month ({'mm' if how == 'sum' else 'mean'})")
        plot.showGrid(x=True, y=True, alpha=0.2)
        mean, spread = stats["mean"].to_numpy(), np.nan_to_num(stats["std"].to_numpy())
        upper = plot.plot(months, mean + spread, pen=None)
        lower = plot.plot(months, mean - spread, pen=None)
        plot.addItem(pg.FillBetweenItem(lower, upper, brush=pg.mkBrush(31, 120, 180, 50)))
        plot.plot(months, mean, pen=pg.mkPen("#1f78b4", width=2), symbol="o", symbolSize=5)
        plot.getAxis("bottom").setTicks([[(m, "JFMAMJJASOND"[m - 1]) for m in months]])
        view.nextRow()
        if how == "sum":
            annual[label] = aggregate.temporal(state.root, name, "sum", "year", basin=basin, period=period)
    if annual:
        plot = view.addPlot(title="Annual totals (mm), interannual variability")
        plot.addLegend(offset=(5, 5))
        width = 0.8 / len(annual)
        for offset, ((label, values), colour) in enumerate(zip(annual.items(), ("#1f78b4", "#66a61e", "#e6550d"),
                                                               strict=False)):
            years = values.index.year.to_numpy() + offset * width - 0.4 + width / 2
            plot.addItem(pg.BarGraphItem(x=years, height=np.nan_to_num(values.to_numpy()), width=width,
                                         brush=colour, pen=None, name=label))
            plot.getAxis("bottom").setTicks([[(int(year), str(int(year))) for year in values.index.year]])
    dialog.label_postClimatologyNotes.setText(" ".join(notes) or
                                              f"Basin {basin}: the band is ± one standard deviation between years.")
