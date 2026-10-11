"""Validation: the run against observations (Tier B, §7.8).

Discharge scores every gauge and colours the gauge points by a score. The gridded
adapters (ET, SWE, soil moisture, neutrons) and the mhm-tools ones (TWS anomaly,
snow cover) run as a `derive("observe")` pass in the native worker; their maps show
on the page's canvas, their scores in the table.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from qgis.PyQt.QtWidgets import QTableWidgetItem

from ...core.datamodel import output_registry
from ...qgis_bridge.display import post_canvas
from ..workflows import post as work
from . import post_map
from .post_context import plot_widget

GAUGE_SCORES = ("NSE", "KGE", "KGE'", "PBIAS", "r", "pairs")


def bind(dialog) -> None:
    from ..dialogs.file_picker import open_file

    combo = dialog._post.ui.comboBox_postAdapter
    for name in output_registry()["observables"]:
        combo.addItem(name.replace("_", " ").capitalize(), name)
    combo.currentIndexChanged.connect(lambda _index: _adapter_chosen(dialog))
    dialog._post.ui.toolButton_postObservation.clicked.connect(
        lambda: _observation_chosen(dialog, open_file(dialog, "Observation", "netCDF (*.nc);;All files (*)")[0]))
    dialog._post.ui.lineEdit_postObservation.editingFinished.connect(
        lambda: _observation_chosen(dialog, dialog._post.ui.lineEdit_postObservation.text()))
    # pushButton_postCompare → compare
    dialog._post.ui.pushButton_postCompare.clicked.connect(lambda: compare(dialog))
    dialog._post.ui.comboBox_postValidationMap.activated.connect(lambda _index: _show_map(dialog))
    _adapter_chosen(dialog)


def _adapter_chosen(dialog) -> None:
    adapter = dialog._post.ui.comboBox_postAdapter.currentData()
    gridded = adapter != "discharge"
    for widget in (dialog._post.ui.lineEdit_postObservation, dialog._post.ui.toolButton_postObservation,
                   dialog._post.ui.comboBox_postObservationVariable):
        widget.setEnabled(gridded)
    dialog._post.ui.doubleSpinBox_postDepth.setVisible(adapter == "soil_moisture")
    dialog._post.ui.doubleSpinBox_postThreshold.setVisible(adapter == "snow_cover")


def _observation_chosen(dialog, path) -> None:
    if not path:
        return
    dialog._post.ui.lineEdit_postObservation.setText(path)

    def variables(_task):
        import netCDF4

        with netCDF4.Dataset(path) as handle:
            return [name for name, var in handle.variables.items() if var.ndim == 3]

    def fill(names):
        combo = dialog._post.ui.comboBox_postObservationVariable
        combo.clear()
        combo.addItems(names)

    work.submit(dialog, "observation", "Reading the observation", variables, fill)


def refresh(dialog, fields) -> None:
    state = dialog._post
    if state.root is None:
        dialog._post.ui.tableWidget_postValidation.setRowCount(0)
        dialog._post.ui.comboBox_postValidationMap.clear()
        dialog._post.ui.label_postValidationNotes.setText("")
        state.widgets.pop("validation", None)
        return
    if "run" in fields:
        dialog._post.ui.label_postValidationNotes.setText("Choose what to compare with, then Compare.")


def compare(dialog) -> None:
    state = dialog._post
    if state.root is None:
        return
    adapter = dialog._post.ui.comboBox_postAdapter.currentData()
    if adapter == "discharge":
        _gauges(dialog)
        return
    path, variable = dialog._post.ui.lineEdit_postObservation.text().strip(), dialog._post.ui.comboBox_postObservationVariable.currentText()
    if not path or not variable:
        dialog._post.ui.label_postValidationNotes.setText("Choose an observation file and its variable first.")
        return
    out = state.root / "derived" / f"observe_{adapter}"
    job = {"what": "observe", "adapter": adapter, "model": str(state.run.files["mhm"]), "observation": path,
           "variable": variable, "out": str(out), "cache": str(state.root),
           "geographic": bool(state.index.get("geographic")), "threshold": dialog._post.ui.doubleSpinBox_postThreshold.value()}
    try:
        if adapter == "soil_moisture":
            job["terms"] = _soil_terms(state, dialog._post.ui.doubleSpinBox_postDepth.value())
        elif adapter == "tws_anomaly":
            from ...core.post.observe import tws_terms

            job["terms"] = tws_terms(state.index)
    except ValueError as error:
        dialog._post.ui.label_postValidationNotes.setText(str(error))
        return
    root = state.root

    def run(task):
        from ...core.executions import post as post_exec

        result = post_exec.derive(task, job, root)
        if isinstance(result, str):  # gridded: maps.npz and scores.json
            return "gridded", result
        return "mhm-tools", (_mhm_tools_maps(result), result)  # netCDF: read here, off the GUI thread

    def failed(details):
        dialog._post.ui.label_postValidationNotes.setText(str(details).strip().splitlines()[-1])

    work.submit(dialog, "observe", f"Comparing with {Path(path).name}", run, lambda r: _results(dialog, *r), failed)


def _soil_terms(state, depth):
    from ...core.post.observe import depth_terms

    layers = {name for name in state.index["variables"] if name.startswith("SWC_L")}
    return depth_terms(state.run.values, state.run.version, state.run.ref.index, depth, layers)


def _results(dialog, kind, result) -> None:
    state = dialog._post
    combo, table = dialog._post.ui.comboBox_postValidationMap, dialog._post.ui.tableWidget_postValidation
    combo.clear()
    if kind == "gridded":
        maps = np.load(Path(result) / "maps.npz")
        scores = json.loads((Path(result) / "scores.json").read_text(encoding="utf-8"))
        state.widgets["validation"] = {name: maps[name] for name in maps.files if name not in ("x", "y")}
        state.widgets["validation_grid"] = (maps["x"], maps["y"])
        rows = [(f"Mean {name}", value) for name, value in scores["pattern_mean"].items()]
        rows += [(f"Basin {name}", scores["basin"].get(name)) for name in ("NSE", "KGE", "PBIAS", "r", "pairs")]
        rows.append(("Windows compared", len(scores["windows"])))
        _plot_basin(dialog, scores)
        dialog._post.ui.label_postValidationNotes.setText(f"Compared on the {scores['grid']} grid over "
                                                 f"{len(scores['windows'])} complete windows.")
    else:
        maps, files = result
        state.widgets["validation"], rows = maps, []
        dialog._post.ui.label_postValidationNotes.setText("mhm-tools evaluation; its plots are in "
                                                 f"{Path(next(iter(files.values()))).parent}.")
    for name in state.widgets["validation"]:
        if not name.startswith("_"):
            combo.addItem(name)
    table.setRowCount(len(rows))
    for row, (name, value) in enumerate(rows):
        table.setItem(row, 0, QTableWidgetItem(name))
        table.setItem(row, 1, QTableWidgetItem(value if isinstance(value, str) else f"{float(value):.3g}"))
    _show_map(dialog)


def _mhm_tools_maps(files) -> dict:
    """The 2-D fields of mhm-tools' metric and accuracy files, north first."""
    import netCDF4

    maps = {}
    for path in files.values():
        name = Path(path).name
        if not name.endswith(".nc") or ("metric" not in name and "accuracy" not in name):
            continue
        with netCDF4.Dataset(path) as handle:
            if "lat" not in handle.variables:
                continue
            lat, lon = handle["lat"][:], handle["lon"][:]
            for name, var in handle.variables.items():
                if var.dimensions == ("lat", "lon"):
                    field = np.ma.filled(np.ma.asarray(var[:], "f8"), np.nan)
                    maps[name] = field[::-1] if lat[0] < lat[-1] else field
            maps["_grid"] = (np.asarray(lon), np.sort(np.asarray(lat))[::-1])
    grid = maps.pop("_grid", None)
    return {"_grid": grid, **maps} if grid is not None else maps


def _plot_basin(dialog, scores) -> None:
    import pyqtgraph as pg

    plot = plot_widget(dialog, dialog._post.ui.widget_postValidationPlot)
    plot.clear()
    starts = np.array([window[0] for window in scores["windows"]], dtype="datetime64[s]").astype("int64")
    plot.plot(starts, np.asarray(scores["basin"]["obs"], float), pen=pg.mkPen("k", width=1.4), name="Observed")
    plot.plot(starts, np.asarray(scores["basin"]["sim"], float), pen=pg.mkPen("#e6550d", width=1.4),
              name="Simulated")
    plot.setTitle("Basin mean per window")


def _show_map(dialog) -> None:
    from ...core.post.grid import Axes, _edges

    state = dialog._post
    maps = dict(state.widgets.get("validation") or {})
    grid = maps.pop("_grid", None) or state.widgets.get("validation_grid")
    name = dialog._post.ui.comboBox_postValidationMap.currentText()
    if name not in maps or grid is None:
        return
    x, y = grid
    axes = Axes(np.asarray(x, float), np.asarray(y, float), _edges(np.asarray(x, float)), _edges(np.asarray(y, float)),
                bool(state.index.get("geographic")))
    layer = post_map.array_layer(dialog, maps[name], name, f"validation_{name}", axes=axes,
                                 diverging=name in ("bias", "relative bias"))
    _set(dialog, [layer])


def _set(dialog, layers) -> None:
    post_map.set_view_layers(dialog, "validation", layers)


def _gauges(dialog) -> None:
    """Every gauge's scores, and the gauge points coloured by the chosen one."""
    state = dialog._post
    run, gauges, domain = state.run, list(state.gauges), state.run.ref.domain

    def score(_task):
        from ...core.handlers.state import domain_state
        from ...core.post import metrics, series

        outlets = domain_state.load_state(domain).get("outlets", {})
        located = {str(record.get("gauge_id") or key): record for key, record in outlets.items()}
        rows = []
        for gauge in gauges:
            frame = series.gauge(run, gauge)
            found = metrics.evaluate(frame["sim"], frame["obs"])
            point = located.get(str(gauge), {}).get("gauge_point") or {}
            rows.append({"gauge": gauge, **{name: found[name] for name in GAUGE_SCORES}, "point": point,
                         "area": float(located.get(str(gauge), {}).get("catchment_area_m2") or 0) / 1e6})
        return rows

    work.submit(dialog, "gauges", "Scoring the gauges", score, lambda rows: _gauge_results(dialog, rows))


def _gauge_results(dialog, rows) -> None:
    from qgis.core import QgsCoordinateReferenceSystem

    table = dialog._post.ui.tableWidget_postValidation
    table.setRowCount(len(rows) * len(GAUGE_SCORES))
    for index, (row, name) in enumerate((row, name) for row in rows for name in GAUGE_SCORES):
        table.setItem(index, 0, QTableWidgetItem(f"Gauge {row['gauge']}: {name}"))
        table.setItem(index, 1, QTableWidgetItem(f"{float(row[name]):.3g}"))
    located = [row for row in rows if row["point"]]
    combo = dialog._post.ui.comboBox_postValidationMap
    combo.clear()
    if not located:
        dialog._post.ui.label_postValidationNotes.setText("Scored every gauge; none has a saved location to map.")
        return
    crs = QgsCoordinateReferenceSystem(located[0]["point"].get("crs", "EPSG:4326"))
    layer = post_canvas.gauge_layer([{"id": row["gauge"], "x": row["point"]["x"], "y": row["point"]["y"],
                                      "value": row["KGE"], "area": row["area"]} for row in located], crs, "KGE")
    dialog._post.ui.label_postValidationNotes.setText("Gauge points coloured by KGE and sized by catchment area.")
    _set(dialog, [layer])
