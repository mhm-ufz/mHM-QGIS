"""Tier C views: Compare, Extremes, Drought, Anomalies, Summary and "Why here?".

Small cache reads stay on the GUI thread; gauge series, run B's scan, the SMI pass
over the series cache, mhm-tools (bankfull) and the L0 rasters (Why here) run on
tasks (`workflows/post`). Each view's map goes on the shared Map dock
(`post_map.set_view_layers`); Compare adds Map B and Map Δ, their extents linked.
"""
from __future__ import annotations

import re
from html import escape

import numpy as np
import pandas as pd
from qgis.PyQt.QtCore import QSignalBlocker, Qt
from qgis.PyQt.QtWidgets import QTableWidgetItem

from ...core.post import aggregate, cache
from ..workflows import post as work
from . import post_map
from .post_context import plot_widget, seconds

STATISTICS = ("mean", "sum", "max", "min", "std")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
UNITS = {"P": "mm/yr", "PET": "mm/yr", "ET": "mm/yr", "Q": "mm/yr", "recharge": "mm/yr", "SM_Lall": "mean",
         "snowpack": "mm, max", "elevation": "m", "slope": ""}


def _fill(table, frame, digits=3) -> None:
    """A DataFrame into a QTableWidget: the index as row headers, missing values blank."""
    table.setColumnCount(len(frame.columns))
    table.setRowCount(len(frame))
    table.setHorizontalHeaderLabels([str(column) for column in frame.columns])
    table.setVerticalHeaderLabels([str(label) for label in frame.index])
    for r, (_, row) in enumerate(frame.iterrows()):
        for c, value in enumerate(row):
            if hasattr(value, "year"):
                text = str(value)[:10]
            elif isinstance(value, (int, float, np.number)):
                text = f"{value:,.{digits}g}" if np.isfinite(value) else ""
            else:
                text = str(value)
            table.setItem(r, c, QTableWidgetItem(text))


def _domain(state):
    return np.load(state.root / "weights" / "domain.npy") > 0


def _no_gauge(label, notes) -> bool:
    if label is None:
        notes.setText("No gauge in this run: this view needs discharge at a gauge.")
        return True
    return False


# --- Compare ----------------------------------------------------------------------


def bind_compare(dialog) -> None:
    ui = dialog._post.ui
    ui.comboBox_postDelta.addItem("B - A", "delta")
    ui.comboBox_postDelta.addItem("(B - A) / A, %", "percent")
    ui.comboBox_postCompareRun.activated.connect(lambda _index: refresh_compare(dialog, {"compare"}))
    ui.comboBox_postCompareVariable.activated.connect(lambda _index: (_statistics(dialog),
                                                                      refresh_compare(dialog, {"compare"})))
    ui.comboBox_postCompareStatistic.activated.connect(lambda _index: refresh_compare(dialog, {"compare"}))
    ui.comboBox_postDelta.activated.connect(lambda _index: _show_compare(dialog))
    link_extents(dialog, (ui.mapCanvas_post, ui.mapCanvas_postB, ui.mapCanvas_postDelta))


def link_extents(dialog, canvases) -> None:
    """Panning or zooming one canvas moves the others."""
    widgets = dialog._post.widgets

    def follow(source):
        if widgets.get("linking"):
            return
        widgets["linking"] = True
        try:
            for other in canvases:
                if other is not source:
                    other.setExtent(source.extent())
                    other.refresh()
        finally:
            widgets["linking"] = False

    for canvas in canvases:
        canvas.extentsChanged.connect(lambda canvas=canvas: follow(canvas))


def _statistics(dialog) -> None:
    ui, index = dialog._post.ui, dialog._post.index
    info = index["variables"].get(ui.comboBox_postCompareVariable.currentText())
    combo = ui.comboBox_postCompareStatistic
    previous = combo.currentData()
    with QSignalBlocker(combo):
        combo.clear()
        for stat in STATISTICS:
            if info and stat in aggregate.map_statistics(info):
                combo.addItem(post_map.LABELS[stat], stat)
        combo.setCurrentIndex(max(0, combo.findData(previous)))


def refresh_compare(dialog, fields) -> None:
    state, ui = dialog._post, dialog._post.ui
    if state.root is None:
        ui.tableWidget_postCompare.setRowCount(0)
        for canvas in (ui.mapCanvas_postB, ui.mapCanvas_postDelta):
            canvas.setLayers([])
        return
    if "run" in fields:
        current = state.context.get("run")
        with QSignalBlocker(ui.comboBox_postCompareRun):
            ui.comboBox_postCompareRun.clear()
            ui.comboBox_postCompareRun.addItem("Choose run B", None)
            for index, ref in enumerate(state.refs):
                if ref != current:
                    ui.comboBox_postCompareRun.addItem(ref.label, index)
        variables = list(state.index["variables"])
        combo = ui.comboBox_postCompareVariable
        with QSignalBlocker(combo):
            previous = combo.currentText()
            combo.clear()
            combo.addItems(variables)
            combo.setCurrentText(previous if previous in variables else
                                 next((name for name in ("aET", "Q") if name in variables), ""))
        _statistics(dialog)
        for canvas in (ui.mapCanvas_postB, ui.mapCanvas_postDelta):
            canvas.setLayers([])
    if fields & {"run", "compare", "period", "basin", "gauge"}:
        _compare(dialog)


def _compare(dialog) -> None:
    state, ui = dialog._post, dialog._post.ui
    index = ui.comboBox_postCompareRun.currentData()
    if index is None:
        ui.label_postCompareNotes.setText("Choose run B: an archived run, or the current one when an archive is open."
                                          if len(state.refs) > 1 else "Archive a run first: there is no run B yet.")
        return
    ref_b, variable = state.refs[index], ui.comboBox_postCompareVariable.currentText()
    stat = ui.comboBox_postCompareStatistic.currentData() or "mean"
    root_a, run_a, gauge = state.root, state.run, state.context.get("gauge")
    period, basin = state.context.get("period"), state.context.get("basin") or "domain"

    def compute(task):
        from ...core.executions import post as post_exec
        from ...core.post import catalog, compare, metrics, series

        run_b = catalog.open_run(ref_b)
        if not run_b.exists("mhm"):
            return f"{ref_b.label} has no mHM output file.", None
        job = post_exec.scan_job(ref_b, run=run_b)
        if job["series_bytes"] > cache.ASK_ABOVE_BYTES:  # the comparison needs no series cache
            job = post_exec.scan_job(ref_b, run=run_b, series=())
        post_exec.scan(task, job)
        root_b = cache.folder(ref_b)
        reason = compare.refusal(root_a, root_b)
        if reason:
            return reason, None
        if variable not in cache.load_index(root_b)["variables"]:
            return f"{ref_b.label} did not write {variable}.", None
        maps = compare.maps(root_a, root_b, variable, stat, period)
        pair = compare.series_pair(root_a, root_b, variable, basin, period)
        scores = None
        if gauge is not None and gauge in series.gauges(run_b):
            a, b = series.gauge(run_a, gauge, period), series.gauge(run_b, gauge, period)
            scores = compare.score_deltas(metrics.evaluate(a["sim"], a["obs"]), metrics.evaluate(b["sim"], b["obs"]))
        return "", (maps, pair, scores)

    def done(result):
        reason, found = result
        ui.label_postCompareNotes.setText(reason or f"A: {state.run.ref.label}; B: {ref_b.label}; {basin}.")
        if found is None:
            return
        maps, pair, scores = found
        frame = pd.DataFrame({"A": [pair["A"].mean()], "B": [pair["B"].mean()]}, index=[f"{variable}, basin mean"])
        frame["B - A"] = frame["B"] - frame["A"]
        if scores is not None:
            frame = pd.concat([frame, scores.rename(index=lambda name: f"Gauge {gauge}: {name}")])
        _fill(ui.tableWidget_postCompare, frame)
        _compare_plot(dialog, pair, variable)
        state.widgets["compare_maps"] = (maps, variable, stat)
        if maps[0] is None or maps[1] is None:
            ui.label_postCompareNotes.setText(ui.label_postCompareNotes.text() + " This statistic over this period "
                                              "needs whole months of a flux; choose the whole run, or mean or sum.")
        _show_compare(dialog)

    work.submit(dialog, "compare", f"Comparing with {ref_b.label}", compute, done)


def _compare_plot(dialog, pair, variable) -> None:
    import pyqtgraph as pg

    plot = plot_widget(dialog, dialog._post.ui.widget_postComparePlot)
    plot.clear()
    x = seconds(pair.index)
    plot.plot(x, pair["A"].to_numpy(), pen=pg.mkPen("#1f78b4", width=1.4), name="A", connect="finite")
    plot.plot(x, pair["B"].to_numpy(), pen=pg.mkPen("#e6550d", width=1.4), name="B", connect="finite")
    plot.setTitle(f"{variable}, basin series")


def _show_compare(dialog) -> None:
    state, ui = dialog._post, dialog._post.ui
    found = state.widgets.get("compare_maps")
    if found is None or found[0][0] is None or found[0][1] is None:
        return
    (a, b, delta, percent), variable, stat = found
    domain = _domain(state)
    a, b = (np.where(domain, m, np.nan) for m in (a, b))
    both = np.concatenate([a[np.isfinite(a)], b[np.isfinite(b)]])
    shared = np.percentile(both, [2, 98]).tolist() if both.size else (0.0, 1.0)
    label = f"{variable} {post_map.LABELS[stat].lower()}"
    relative = ui.comboBox_postDelta.currentData() == "percent"
    d = np.where(domain, percent if relative else delta, np.nan)
    finite = np.abs(d[np.isfinite(d)])
    high = float(np.percentile(finite, 98)) if finite.size else 1.0
    post_map.set_view_layers(dialog, "compare", [post_map.array_layer(dialog, a, f"A: {label}", "compare_a",
                                                                      value_range=shared)])
    layers = state.widgets["compare_layers"] = (  # canvases don't own layers: keep them alive
        post_map.array_layer(dialog, b, f"B: {label}", "compare_b", value_range=shared),
        post_map.array_layer(dialog, d, f"{'(B - A) / A, %' if relative else 'B - A'}: {label}",
                             "compare_delta" + ("_percent" if relative else ""), diverging=True,
                             value_range=(-high, high)))
    post_map.show_layers(dialog, [layers[0]], ui.mapCanvas_postB)
    post_map.show_layers(dialog, [layers[1]], ui.mapCanvas_postDelta)


# --- Extremes ---------------------------------------------------------------------


def bind_extremes(dialog) -> None:
    ui = dialog._post.ui
    for text, kind in (("Annual maximum", "max"), ("Annual minimum", "min"), ("7-day minimum", "am7")):
        ui.comboBox_postExtremeKind.addItem(text, kind)
    for month, name in enumerate(MONTHS, 1):
        ui.comboBox_postYearStart.addItem(name, month)
    for combo in (ui.comboBox_postExtremeKind, ui.comboBox_postYearStart):
        combo.activated.connect(lambda _index: refresh_extremes(dialog, {"extremes"}))
    ui.comboBox_postExtremesMap.activated.connect(lambda _index: _extremes_map(dialog))


def refresh_extremes(dialog, fields) -> None:
    state, ui = dialog._post, dialog._post.ui
    if state.root is None:
        ui.tableWidget_postReturnLevels.setRowCount(0)
        ui.comboBox_postExtremesMap.clear()
        return
    if "run" in fields:
        with QSignalBlocker(ui.comboBox_postExtremesMap):
            ui.comboBox_postExtremesMap.clear()
            if "Q" in state.index["variables"]:
                ui.comboBox_postExtremesMap.addItem("Maximum of Q over the run (mm per step)", "max")
            if state.run.exists("mrm"):
                ui.comboBox_postExtremesMap.addItem("Bankfull discharge (mRM, mhm-tools)", "bankfull")
        _extremes_map(dialog)
    if not fields & {"run", "gauge", "period", "extremes"}:
        return
    gauge = state.context.get("gauge")
    if _no_gauge(gauge, ui.label_postExtremesNotes):
        return
    run, period = state.run, state.context.get("period")
    kind, start = ui.comboBox_postExtremeKind.currentData(), ui.comboBox_postYearStart.currentData()

    def compute(_task):
        from ...core.post import extremes, series, signatures

        frame, found = series.gauge(run, gauge, period), {}
        reason = signatures.usable(frame["sim"]).replace("Signatures", "Extremes")
        if reason:
            return reason, found
        for name in ("sim", "obs"):
            if frame[name].notna().any():
                found[name] = (extremes.am7(frame[name], start) if kind == "am7"
                               else extremes.annual(frame[name], kind, start))
        return "", found

    work.submit(dialog, "extremes", f"Annual extremes at gauge {gauge}", compute,
                lambda result: _draw_extremes(dialog, gauge, kind, *result))


def _draw_extremes(dialog, gauge, kind, reason, found) -> None:
    import pyqtgraph as pg

    from ...core.post import extremes

    ui = dialog._post.ui
    annual = plot_widget(dialog, ui.widget_postExtremesAnnual, dates=False)
    returns = plot_widget(dialog, ui.widget_postExtremesReturn, dates=False)
    annual.clear()
    returns.clear()
    ui.tableWidget_postReturnLevels.setRowCount(0)
    notes = [reason] if reason else []
    colours, names = {"obs": "k", "sim": "#e6550d"}, {"obs": "Observed", "sim": "Simulated"}
    levels = {}
    for name, values in found.items():
        annual.plot(values.index.to_numpy(float), values.to_numpy(), pen=pg.mkPen(colours[name], width=1.2),
                    symbol="o", symbolSize=5, symbolBrush=colours[name], name=names[name])
        if kind != "max":
            continue
        x, period = extremes.plotting_positions(values.to_numpy())
        returns.plot(period, x, pen=None, symbol="o", symbolSize=6, symbolBrush=colours[name], name=names[name])
        table = extremes.return_levels(values)
        if len(table):
            span = np.geomspace(1.05, 200, 80)
            returns.plot(span, extremes.gev_quantile(extremes.gev_fit(values.to_numpy()), span),
                         pen=pg.mkPen(colours[name], width=1.2, style=Qt.PenStyle.DashLine))
            levels[f"{names[name]} (m³/s)"] = table.set_index("return period (years)")["flow"]
    years = sorted({int(year) for values in found.values() for year in values.index})
    annual.getAxis("bottom").setTicks([[(year, str(year)) for year in years]])
    annual.setTitle(f"Gauge {gauge}: {ui.comboBox_postExtremeKind.currentText().lower()} per year")
    annual.setLabel("left", "m³/s")
    if kind == "max":
        returns.setLogMode(x=True)
        returns.setLabel("bottom", "Return period (years), Gringorten; dashed: GEV")
        returns.setLabel("left", "m³/s")
        if found and not levels:
            notes.append("The GEV needs ten or more years: empirical return periods only.")
    elif found:
        notes.append("Return periods are fitted to annual maxima.")
    if levels:
        _fill(ui.tableWidget_postReturnLevels, pd.DataFrame(levels).rename_axis("T (years)"))
    ui.label_postExtremesNotes.setText(" ".join(notes))


def _extremes_map(dialog) -> None:
    state, ui = dialog._post, dialog._post.ui
    which = ui.comboBox_postExtremesMap.currentData()
    if which == "max":
        array = np.where(_domain(state), aggregate.statistic(state.root, "Q", "max"), np.nan)
        post_map.set_view_layers(dialog, "extremes", [post_map.array_layer(dialog, array, "Q maximum", "extremes_q_max")])
        return
    if which != "bankfull":
        return
    path, root = state.run.files["mrm"], state.root
    out = root / "derived" / "bankfull.nc"

    def compute(task):
        import netCDF4

        from ...core.executions import post as post_exec
        from ...core.post.grid import Axes, _edges

        if not out.is_file() or out.stat().st_mtime < path.stat().st_mtime:
            with netCDF4.Dataset(path) as handle:
                variable = "Qrouted" if "Qrouted" in handle.variables else "discharge"
            out.parent.mkdir(exist_ok=True)
            post_exec.derive(task, {"what": "bankfull", "path": str(path), "out": str(out), "variable": variable}, root)
        with netCDF4.Dataset(out) as handle:
            var = handle["Q_bkfl"]
            q = np.ma.filled(np.ma.asarray(var[:], "f8"), np.nan)
            y, x = (np.asarray(handle[name][:], "f8") for name in var.dimensions[-2:])
            geographic = "degree" in getattr(handle[var.dimensions[-1]], "units", "")
        if len(y) > 1 and y[0] < y[-1]:
            q, y = q[::-1], y[::-1]
        return q, Axes(x, y, _edges(x), _edges(y), geographic)

    def done(result):
        q, axes = result
        layer = post_map.array_layer(dialog, q, "Bankfull discharge (m³/s)", "extremes_bankfull", axes=axes)
        post_map.set_view_layers(dialog, "extremes", [layer])

    work.submit(dialog, "bankfull", "Bankfull discharge (mhm-tools)", compute, done)


# --- Drought ----------------------------------------------------------------------


def bind_drought(dialog) -> None:
    combo = dialog._post.ui.comboBox_postDroughtMap
    # No drought-frequency map: SMI is a per-cell rank, so over the run every cell is dry
    # in about the same share of months (the frequency is uniform by construction).
    for text, which in (("SMI at the current month", "smi"), ("Longest drought (months)", "longest")):
        combo.addItem(text, which)
    combo.activated.connect(lambda _index: _drought_map(dialog))


def refresh_drought(dialog, fields) -> None:
    state, ui = dialog._post, dialog._post.ui
    if state.root is None:
        ui.tableWidget_postDroughtEvents.setRowCount(0)
        state.widgets.pop("smi", None)
        return
    if "run" in fields:
        _smi(dialog)
    if fields & {"run", "gauge", "period"}:
        _streamflow_drought(dialog)
    if "current_time" in fields and ui.comboBox_postDroughtMap.currentData() == "smi":
        _drought_map(dialog)


def _smi(dialog) -> None:
    state, ui = dialog._post, dialog._post.ui
    root = state.root

    def compute(_task):
        from ...core.post import drought

        monthly = drought.monthly_from_series(root, "SM_Lall")
        if monthly is None:
            return None
        values, labels = monthly
        index = drought.smi(values, labels)
        cells = np.load(root / "cells_mhm.npy")
        area, longest, _ = drought.smi_products(index, np.load(root / "weights" / "domain.npy")[cells[0], cells[1]] > 0)
        return {"labels": labels, "index": index, "cells": cells, "area": area,
                "longest": aggregate._grid(root, cells, longest)}

    def done(found):
        import pyqtgraph as pg

        state.widgets["smi"] = found
        for widget in (ui.widget_postDroughtArea, ui.label_postDroughtMap, ui.comboBox_postDroughtMap):
            widget.setVisible(found is not None)
        if found is None:
            state.widgets["drought_notes"] = "No SM_Lall series cache: the soil-moisture (SMI) panel is hidden."
            _drought_notes(dialog)
            return
        plot = plot_widget(dialog, ui.widget_postDroughtArea)
        plot.clear()
        x = seconds(found["labels"].astype("datetime64[s]"))
        plot.addItem(pg.BarGraphItem(x=x + 15 * 86400, height=100 * found["area"], width=27 * 86400,
                                     brush="#d95f02", pen=None))
        plot.setTitle("Domain area in soil-moisture drought (SMI < 0.2), %")
        state.widgets["drought_notes"] = ""
        _drought_notes(dialog)
        _drought_map(dialog)

    work.submit(dialog, "smi", "Soil moisture index", compute, done)


def _drought_map(dialog) -> None:
    state = dialog._post
    found, which = state.widgets.get("smi"), state.ui.comboBox_postDroughtMap.currentData()
    if not found:
        return
    if which == "smi":
        step = int(state.context.get("current_time") or 0)
        month = state.intervals[step, 0].astype("datetime64[M]")
        hits = np.flatnonzero(found["labels"] == month)
        if not hits.size:
            return
        array, name, value_range = aggregate._grid(state.root, found["cells"], found["index"][hits[0]]), f"SMI {month}", (0, 1)
    else:
        array, name, value_range = found[which], state.ui.comboBox_postDroughtMap.currentText(), None
    stem = f"drought_{which}" + (f"_{name[4:]}" if which == "smi" else "")
    layer = post_map.array_layer(dialog, np.where(_domain(state), array, np.nan), name, stem, value_range=value_range)
    post_map.set_view_layers(dialog, "drought", [layer])


def _streamflow_drought(dialog) -> None:
    state = dialog._post
    gauge = state.context.get("gauge")
    if gauge is None:
        state.widgets["flow_notes"] = "No gauge in this run: streamflow droughts need discharge at a gauge."
        _drought_notes(dialog)
        return
    run, period = state.run, state.context.get("period")

    def compute(_task):
        from ...core.post import drought, series, signatures

        frame = series.gauge(run, gauge, period)
        reason = signatures.usable(frame["sim"]).replace("Signatures", "Streamflow droughts")
        if reason:
            return reason, frame, None, None
        level = drought.threshold(frame["sim"])
        return "", frame, level, drought.events(frame["sim"], level)

    work.submit(dialog, "drought", f"Streamflow droughts at gauge {gauge}", compute,
                lambda result: _draw_drought(dialog, gauge, *result))


def _draw_drought(dialog, gauge, reason, frame, level, found) -> None:
    import pyqtgraph as pg

    state, ui = dialog._post, dialog._post.ui
    plot = plot_widget(dialog, ui.widget_postDroughtFlow)
    plot.clear()
    ui.tableWidget_postDroughtEvents.setRowCount(0)
    x = seconds(frame.index)
    plot.plot(x, frame["obs"].to_numpy(), pen=pg.mkPen("k", width=1.1), name="Observed", connect="finite")
    plot.plot(x, frame["sim"].to_numpy(), pen=pg.mkPen("#e6550d", width=1.1), name="Simulated", connect="finite")
    plot.setTitle(f"Gauge {gauge}")
    state.widgets["flow_notes"] = reason
    if level is not None:
        plot.plot(x, level.to_numpy(), pen=pg.mkPen("#1f78b4", width=1.2, style=Qt.PenStyle.DashLine),
                  name="Q80 (day of year)")
        for _, event in found.iterrows():
            plot.addItem(pg.LinearRegionItem(values=(seconds(event["start"])[()], seconds(event["end"])[()] + 86400),
                                             movable=False, brush=pg.mkBrush(217, 95, 2, 50), pen=pg.mkPen(None)))
        _fill(ui.tableWidget_postDroughtEvents, found.rename(index=lambda row: row + 1))
        state.widgets["flow_notes"] = (f"{len(found)} streamflow droughts of the simulated discharge"
                                       + ("." if len(found) else " (none below the threshold for 5 days or more)."))
    _drought_notes(dialog)


def _drought_notes(dialog) -> None:
    widgets = dialog._post.widgets
    dialog._post.ui.label_postDroughtNotes.setText(
        " ".join(text for text in (widgets.get("flow_notes"), widgets.get("drought_notes")) if text))


# --- Anomalies --------------------------------------------------------------------


def bind_anomalies(dialog) -> None:
    ui = dialog._post.ui
    ui.tableWidget_postFindings.currentCellChanged.connect(lambda row, *_: _jump(dialog, row))
    ui.comboBox_postAnomalyVariable.activated.connect(lambda _index: _anomaly_map(dialog))


def refresh_anomalies(dialog, fields) -> None:
    state, ui = dialog._post, dialog._post.ui
    if state.root is None:
        ui.tableWidget_postFindings.setRowCount(0)
        state.widgets.pop("anomaly_z", None)
        return
    if "run" in fields:
        state.widgets.pop("anomaly_z", None)
        names = [name for name, info in state.index["variables"].items() if info.get("months")]
        with QSignalBlocker(ui.comboBox_postAnomalyVariable):
            ui.comboBox_postAnomalyVariable.clear()
            ui.comboBox_postAnomalyVariable.addItems(names)
            if "pre" in names:
                ui.comboBox_postAnomalyVariable.setCurrentText("pre")
        _checks(dialog)
    if fields & {"run", "current_time"}:
        _anomaly_map(dialog)


def _checks(dialog) -> None:
    state, ui = dialog._post, dialog._post.ui
    root, run, gauges, exact = state.root, state.run, list(state.gauges), state.exact

    def compute(_task):
        from ...core.post import checks, metrics, series

        scores = {}
        for gauge in gauges:
            frame = series.gauge(run, gauge)
            scores[gauge] = metrics.evaluate(frame["sim"], frame["obs"])
        return checks.run(root, exact, scores)

    def done(findings):
        state.widgets["findings"] = findings
        table = ui.tableWidget_postFindings
        with QSignalBlocker(table):
            table.setColumnCount(2)
            table.setHorizontalHeaderLabels(["Severity", "Finding"])
            table.setRowCount(len(findings))
            for row, finding in enumerate(findings):
                table.setItem(row, 0, QTableWidgetItem(finding.severity))
                table.setItem(row, 1, QTableWidgetItem(finding.message))
        ui.label_postAnomaliesNotes.setText(f"{len(findings)} findings; select one to go to its cell, gauge or month."
                                            if findings else "The checks found nothing implausible.")

    work.submit(dialog, "checks", "Plausibility checks", compute, done)


def _jump(dialog, row) -> None:
    """A finding's cell, gauge or month becomes the context's."""
    state = dialog._post
    findings = state.widgets.get("findings") or []
    if not 0 <= row < len(findings):
        return
    finding, context = findings[row], state.context
    if finding.cells:
        context.set("cell", tuple(finding.cells[0]))
    if finding.gauge is not None:
        context.set("gauge", finding.gauge)
    if finding.month:
        months = state.intervals[:, 0].astype("datetime64[M]")
        hits = np.flatnonzero(months == np.datetime64(finding.month, "M"))
        if hits.size:
            context.set("current_time", int(hits[0]))


def _anomaly_map(dialog) -> None:
    from ...core.post import climatology

    state = dialog._post
    name = state.ui.comboBox_postAnomalyVariable.currentText()
    if not name:
        return
    cached = state.widgets.get("anomaly_z")
    if cached is None or cached[0] != name:
        cached = state.widgets["anomaly_z"] = (name, *climatology.anomaly_maps(state.root, name))
    _, z, labels = cached
    month = state.intervals[int(state.context.get("current_time") or 0), 0].astype("datetime64[M]")
    hits = np.flatnonzero(labels == month)
    if not hits.size:
        return
    array = np.where(_domain(state), z[hits[0]], np.nan)
    layer = post_map.array_layer(dialog, array, f"{name} anomaly z, {month}", f"anomaly_{name}_{month}",
                                 diverging=True, value_range=(-3, 3))
    post_map.set_view_layers(dialog, "anomalies", [layer])


# --- Summary ----------------------------------------------------------------------


def bind_summary(dialog) -> None:
    dialog._post.ui.comboBox_postSummaryColumn.activated.connect(lambda _index: _summary_map(dialog))


def refresh_summary(dialog, fields) -> None:
    state, ui = dialog._post, dialog._post.ui
    if state.root is None:
        ui.tableWidget_postSummary.setRowCount(0)
        state.widgets.pop("summary", None)
        return
    if "run" not in fields:  # the whole run, every basin
        return
    root, run, gauges, exact = state.root, state.run, list(state.gauges), state.exact

    def compute(_task):
        from ...core.post import balance

        return balance.summary(root, run, gauges, exact)

    def done(frame):
        state.widgets["summary"] = frame
        _fill(ui.tableWidget_postSummary, frame)
        combo = ui.comboBox_postSummaryColumn
        with QSignalBlocker(combo):
            previous = combo.currentText()
            combo.clear()
            combo.addItems([column for column in frame.columns if frame[column].notna().any()])
            combo.setCurrentText(previous if previous in frame.columns else "Q/P")
        ui.label_postSummaryNotes.setText("Whole run. Missing data stay blank, never zero.")
        _summary_map(dialog)

    work.submit(dialog, "summary", "Summary of the basins", compute, done)


def _summary_map(dialog) -> None:
    state = dialog._post
    frame, column = state.widgets.get("summary"), state.ui.comboBox_postSummaryColumn.currentText()
    if frame is None or column not in frame.columns:
        return
    weights = {}
    for label in frame.index:
        path = state.root / "weights" / f"{'domain' if label == 'Domain' else label.split()[-1]}.npy"
        if path.is_file():
            weights[label] = np.load(path)
    array = np.full(state.axes.shape, np.nan)
    for label in sorted(weights, key=lambda name: -weights[name].sum()):  # nested basins: smallest on top
        array[weights[label] >= 0.5] = frame.loc[label, column]
    stem = "summary_" + re.sub(r"\W+", "_", column)
    post_map.set_view_layers(dialog, "summary", [post_map.array_layer(dialog, array, f"{column} by basin", stem)])


# --- Why here? --------------------------------------------------------------------


def why_here(dialog) -> None:
    """Open the "Why here?" dock beside the Cell inspector for the context's cell."""
    from . import post_context

    state = dialog._post
    if state.root is None or state.context.get("cell") is None:
        return
    shown = state.docks.get("why_here")
    if shown is not None and shown.isVisible():
        shown.raise_()
        refresh_why_here(dialog, {"cell"})
        return
    post_context.open_dock(dialog, "why_here", beside="cell_inspector")


def refresh_why_here(dialog, fields) -> None:
    state, browser = dialog._post, dialog._post.ui.textBrowser_postWhyHere
    if state.root is None:
        browser.setHtml("")
        return
    if not fields & {"run", "cell"}:
        return
    cell = state.context.get("cell")
    if cell is None:
        browser.setHtml("<p>Right-click a cell on the map and choose Why here?</p>")
        return
    root, run, axes = state.root, state.run, state.axes

    def compute(_task):
        from ...core.post import explain

        return explain.cell(root, run, axes, *cell)

    work.submit(dialog, "why", f"Explaining row {cell[0]}, column {cell[1]}", compute,
                lambda found: _explained(dialog, cell, found))


def _explained(dialog, cell, found) -> None:
    def number(value, sign=""):
        return f"{value:{sign},.3g}" if value is not None and np.isfinite(value) else ""

    rows = "".join(f"<tr><td>{escape(name)} <small>{escape(UNITS.get(name, ''))}</small></td>"
                   f"<td align='right'>{number(value)}</td><td align='right'>{number(found['z'].get(name), '+')}</td>"
                   f"<td align='right'>{number(found['percent'].get(name), '+')}</td></tr>"
                   for name, value in found["values"].items())
    shares = ", ".join(f"{name[:-6]} {100 * value:.0f} %" for name, value in found["shares"].items())
    static = ", ".join(f"{name} {value:,.4g}" for name, value in found["static"].items()
                       if name not in ("elevation", "slope"))
    dialog._post.ui.textBrowser_postWhyHere.setHtml(
        f"<h4>Row {cell[0]}, column {cell[1]}</h4><ul>"
        + "".join(f"<li>{escape(text)}</li>" for text in found["phrases"]) + "</ul>"
        "<table cellpadding='3'><tr><th align='left'>Long-term</th><th>Cell</th><th>z</th><th>% vs basin</th></tr>"
        + rows + "</table>"
        + (f"<p>Runoff shares: {escape(shares)}.</p>" if shares else "")
        + (f"<p>Classes under the cell (majority): {escape(static)}.</p>" if static else ""))
