"""Overview, Water balance and Processes: the basin-level Tier A pages.

Everything here reads the scan cache, except the Overview's gauge scores (netCDF and
mhm-tools), which run on a task.
"""
from __future__ import annotations

from html import escape

import numpy as np
from qgis.PyQt.QtWidgets import QTableWidgetItem

from ..workflows import post as work
from . import post_map
from .post_context import plot_widget, seconds

FREQ = {"month": "month", "year": "year"}
PANELS = (("Runoff partition", ("QD", "QIf", "QIs", "QB")),
          ("Evapotranspiration", ("PET", "aET", "aET_L")),
          ("Snow", ("pre", "snowpack", "Qsm")),
          ("Soil water", ("SWC_L",)),
          ("Infiltration and recharge", ("soil_infil_L", "recharge")))
COLOURS = ("#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666")


def bind(dialog) -> None:
    dialog._post.ui.comboBox_postBalanceMap.activated.connect(lambda _index: show_balance_map(dialog))


def _basin(dialog):
    return dialog._post.context.get("basin") or "domain"


# --- Overview ---------------------------------------------------------------


def refresh_overview(dialog, fields) -> None:
    state = dialog._post
    if state.root is None:
        dialog.textBrowser_postOverview.setHtml(
            "<p>Open a setup with a finished run; the Outputs tab lists its runs.</p>"
            + "".join(f"<p>{escape(text)}</p>" for text in state.findings))
        return
    if not fields & {"run", "basin", "period"}:
        return
    from ...core.post import balance

    basin, period = _basin(dialog), state.context.get("period")
    table = balance.per_year(balance.basin(state.root, basin, exact=state.exact, period=period))
    run, gauge = state.run, (state.gauges or [None])[0]

    def scores(_task):
        from ...core.post import metrics, series

        if gauge is None:
            return None
        frame = series.gauge(run, gauge, period)
        return frame["sim"].mean(), metrics.evaluate(frame["sim"], frame["obs"])

    work.submit(dialog, "overview", "Scoring the main gauge", scores,
                lambda result: _overview(dialog, table, gauge, result),
                lambda _details: _overview(dialog, table, gauge, None))


def _overview(dialog, table, gauge, gauge_scores) -> None:
    state = dialog._post
    row = table.iloc[0] if len(table) else None
    area = float((np.load(state.root / "weights" / f"{_basin(dialog)}.npy")
                  * np.load(state.root / "area.npy")).sum()) / 1e6
    first, last = state.intervals[0, 0], state.intervals[-1, 1]
    step = float(np.median(np.diff(state.intervals, axis=1)) / np.timedelta64(1, "D"))
    tiles = [("Run", state.run.ref.label), ("Period", f"{str(first)[:10]} to {str(last)[:10]}"),
             ("Output step", f"{step:g} days"), ("Basin area", f"{area:,.1f} km²")]
    if row is not None:
        tiles += [(name, f"{row[name]:,.0f} mm/yr") for name in ("P", "ET", "Q") if np.isfinite(row[name])]
        tiles += [(name, f"{row[name]:.2f}") for name in ("Q/P", "ET/P") if np.isfinite(row[name])]
        if np.isfinite(row["R"]):
            tiles.append(("Unaccounted R (incl. karstic loss)",
                          f"{row['R']:,.1f} ± {row['bound']:,.1f} mm/yr" if row["bound"] else f"{row['R']:,.2f} mm/yr"))
    if gauge_scores is not None:
        mean, scores = gauge_scores
        tiles.append((f"Gauge {gauge}: mean Qsim", f"{mean:,.2f} m³/s"))
        tiles += [(f"Gauge {gauge}: {name}", f"{scores[name]:.2f}") for name in ("NSE", "KGE")
                  if np.isfinite(scores[name])]
        if scores["reason"]:
            tiles.append((f"Gauge {gauge}", scores["reason"]))
    cells = "".join(f"<td style='padding:6px 14px;border:1px solid #ccc'><small>{escape(name)}</small><br>"
                    f"<b>{escape(value)}</b></td>" + ("</tr><tr>" if index % 4 == 3 else "")
                    for index, (name, value) in enumerate(tiles))
    notes = [*state.findings, *state.index.get("problems", []), *table.attrs.get("notes", [])]
    if not state.exact:
        notes.append("Output intervals are longer than the model step: storage changes use interval means "
                     "(the ± bound). Model-step outputs make the balance exact.")
    if state.index.get("domain_source") != "restart":
        notes.append("Set write_restart = .true. to use mHM's own cell areas and sealed fraction.")
    missing = [f"{v.name}: {v.message}" for v in state.run.variables if not v.present]
    dialog.textBrowser_postOverview.setHtml(
        f"<table style='border-collapse:collapse'><tr>{cells}</tr></table>"
        + ("<h4>Findings</h4><ul>" + "".join(f"<li>{escape(n)}</li>" for n in notes) + "</ul>" if notes else "")
        + ("<h4>Not written by this run</h4><ul>" + "".join(f"<li>{escape(m)}</li>" for m in missing) + "</ul>"
           if missing else ""))


# --- Water balance --------------------------------------------------------------


def refresh_balance(dialog, fields) -> None:
    import pyqtgraph as pg

    state = dialog._post
    table = dialog._post.ui.tableWidget_postWaterBalance
    if state.root is None:
        table.setRowCount(0)
        if "widget_postWaterBalancePlot" in state.widgets:
            state.widgets["widget_postWaterBalancePlot"].clear()
        dialog._post.ui.comboBox_postBalanceMap.clear()
        return
    plot = plot_widget(dialog, dialog._post.ui.widget_postWaterBalancePlot)
    from ...core.post import balance

    if not fields & {"run", "basin", "period", "resolution"}:
        return
    basin, period = _basin(dialog), state.context.get("period")
    freq = FREQ.get(state.context.get("resolution"), "period")
    frame = balance.basin(state.root, basin, freq, state.exact, period)
    shown = balance.per_year(frame) if freq == "period" else frame
    unit = "mm/yr" if freq == "period" else "mm"
    columns = [c for c in shown.columns if shown[c].notna().any()]
    table.setColumnCount(len(columns))
    table.setRowCount(len(shown))
    table.setHorizontalHeaderLabels([f"{c} ({unit})" if c not in balance.RATIOS and c != "days" else c
                                     for c in columns])
    table.setVerticalHeaderLabels(["Period" if freq == "period" else str(label)[: 7 if freq == "month" else 4]
                                   for label in shown.index])
    for r, (_, row) in enumerate(shown.iterrows()):
        for c, column in enumerate(columns):
            table.setItem(r, c, QTableWidgetItem(f"{row[column]:,.3g}" if column in balance.RATIOS
                                                 else f"{row[column]:,.1f}"))
    dialog._post.ui.label_postBalanceNotes.setText(" ".join(frame.attrs.get("notes", [])))
    monthly = balance.basin(state.root, basin, "month", state.exact, period)
    plot.clear()
    x = seconds(monthly.index) + 15 * 86400
    base = np.zeros(len(monthly))
    for column, colour in (("ET", "#66a61e"), ("Q", "#1f78b4"), ("dS", "#bdbdbd")):
        values = np.nan_to_num(monthly[column].to_numpy())
        bottom = np.where(values >= 0, base, 0)
        plot.addItem(pg.BarGraphItem(x=x, y0=bottom, height=values, width=25 * 86400, brush=colour, pen=None,
                                     name=column))
        base = base + np.clip(values, 0, None)
    plot.plot(x, monthly["P"].to_numpy(), pen=pg.mkPen("k", width=1.5), symbol="o", symbolSize=4, name="P")
    plot.setLabel("left", "mm per month")
    if "run" in fields:
        maps = balance.cell_maps(state.root, state.exact)
        state.widgets["balance_maps"] = maps
        combo = dialog._post.ui.comboBox_postBalanceMap
        combo.clear()
        for name, value in maps.items():
            if isinstance(value, np.ndarray) and value.dtype != bool:
                flagged = maps.get(f"{name} > 1")
                count = int(np.nansum(flagged)) if flagged is not None else 0
                combo.addItem(f"{name} ({count} cells > 1)" if count else name, name)
        show_balance_map(dialog)


def show_balance_map(dialog) -> None:
    state = dialog._post
    name = dialog._post.ui.comboBox_postBalanceMap.currentData()
    maps = state.widgets.get("balance_maps") or {}
    if name not in maps:
        return
    array = np.where(np.load(state.root / "weights" / "domain.npy") > 0, maps[name], np.nan)
    layer = post_map.array_layer(dialog, array, name, f"balance_{name.replace('/', '_per_')}",
                                 diverging=name in ("dS", "R", "P-ET"))
    post_map.set_view_layers(dialog, "water_balance", [layer])


# --- Processes ------------------------------------------------------------------


def refresh_processes(dialog, fields) -> None:
    import pyqtgraph as pg

    state = dialog._post
    container = dialog._post.ui.widget_postProcessesPlot
    if state.root is None:
        if "processes" in state.widgets:
            state.widgets["processes"].clear()
        dialog._post.ui.label_postProcessesNotes.setText("")
        return
    if "processes" not in state.widgets:
        from qgis.PyQt.QtWidgets import QVBoxLayout

        layout_widget = pg.GraphicsLayoutWidget()
        layout_widget.setBackground("w")
        QVBoxLayout(container).addWidget(layout_widget)
        state.widgets["processes"] = layout_widget
    view = state.widgets["processes"]
    if not fields & {"run", "basin", "cell", "period", "resolution"}:
        return
    view.clear()
    from ...core.post import aggregate

    context = state.context
    cell, period = context.get("cell"), context.get("period")
    resolution = context.get("resolution") or "native"
    scanned = state.index["variables"]
    present = {v.name: v for v in state.run.variables}
    notes, first = [], None
    where = f"cell row {cell[0]}, column {cell[1]}" if cell else f"basin {_basin(dialog)}"
    for title, outputs in PANELS:
        names = [name for name, info in scanned.items() if info.get("output") in outputs or name in outputs]
        missing = [v for v in present.values() if v.output in outputs and not v.present]
        notes += [f"{title}: {v.name} not written ({v.message})" for v in missing]
        curves = []
        for name in names:
            how = "sum" if scanned[name].get("kind") == "flux" else "mean"
            try:
                values = aggregate.temporal(state.root, name, how, resolution, basin=_basin(dialog),
                                            cell=cell, period=period)
            except ValueError as error:
                notes.append(f"{title}: {error}")
                continue
            if values is None:
                notes.append(f"{title}: {name} has no series cache for this cell (Map Explorer, Build series cache).")
                continue
            curves.append((name, values))
        if not curves:
            continue
        plot = view.addPlot(title=title, axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        plot.addLegend(offset=(5, 5))
        plot.showGrid(x=True, y=True, alpha=0.2)
        if first is None:
            first = plot
        else:
            plot.setXLink(first)
        stacked = title == "Runoff partition"
        base = None
        for (name, values), colour in zip(curves, COLOURS, strict=False):
            x, y = seconds(values.index), np.nan_to_num(values.to_numpy())
            if stacked:
                top = y if base is None else base + y
                upper = plot.plot(x, top, pen=pg.mkPen(colour), name=name)
                lower = plot.plot(x, base if base is not None else np.zeros_like(y), pen=None)
                plot.addItem(pg.FillBetweenItem(lower, upper, brush=pg.mkBrush(colour + "80")))
                base = top
            else:
                plot.plot(x, y, pen=pg.mkPen(colour, width=1.2), name=name)
        view.nextRow()
    if first is not None:  # a FillBetweenItem's bounds include x = 0; pin the shared axis to the period
        chosen = state.intervals if period is None else state.intervals[
            (state.intervals[:, 0] >= np.datetime64(period[0])) & (state.intervals[:, 1] <= np.datetime64(period[1]))]
        first.setXRange(seconds(chosen[0, 0])[()], seconds(chosen[-1, 1])[()], padding=0.01)
    dialog._post.ui.label_postProcessesNotes.setText(f"Showing {where}. " + " ".join(dict.fromkeys(notes)))
