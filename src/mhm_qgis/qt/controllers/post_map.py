"""Map Explorer: variable tree, statistic, the map, the cell inspector and its plot.

Instant maps are VRT band switches (the VRT is built on a task: it reads netCDF);
statistic maps come from the cache, or a `derive` pass in the native worker, and are
shown as GeoTIFFs. A click picks an L1 cell; a gauge choice zooms to its catchment.
"""
from __future__ import annotations

import numpy as np
from qgis.PyQt.QtCore import QSignalBlocker, Qt
from qgis.PyQt.QtWidgets import QTableWidgetItem, QTreeWidgetItem

from ...core.executions.post import SERIES_DEFAULT
from ...core.post.catalog import Variable
from ...qgis_bridge.display import post_canvas
from ..workflows import post as work
from .post_context import plot_widget, seconds

LABELS = {"instant": "Instant", "mean": "Mean", "sum": "Sum", "std": "Std. deviation", "min": "Minimum",
          "max": "Maximum", "percentile": "Percentile", "anomaly": "Anomaly"}


def bind(dialog) -> None:
    from qgis.gui import QgsMapToolEmitPoint

    state = dialog._post
    dialog.treeWidget_postVariables.currentItemChanged.connect(lambda item, _old: _variable_chosen(dialog, item))
    dialog.comboBox_postStatistic.activated.connect(lambda _index: _statistic_chosen(dialog))
    dialog.spinBox_postPercentile.valueChanged.connect(lambda _value: _statistic_chosen(dialog))
    # pushButton_postBuildCache → build_cache
    dialog.pushButton_postBuildCache.clicked.connect(lambda: build_cache(dialog))
    # pushButton_postOpenInQgis → post_canvas.open_in_qgis
    dialog.pushButton_postOpenInQgis.clicked.connect(lambda: open_in_qgis(dialog))
    tool = QgsMapToolEmitPoint(dialog.mapCanvas_post)
    tool.canvasClicked.connect(lambda point, _button: clicked(dialog, point.x(), point.y()))
    dialog.mapCanvas_post.setMapTool(tool)
    state.widgets["map_tool"] = tool


def _crs(dialog):
    return post_canvas.crs_for(dialog._post.axes, dialog.get_crs())


def refresh(dialog, fields) -> None:
    state = dialog._post
    if state.root is None:
        dialog.treeWidget_postVariables.clear()
        dialog.mapCanvas_post.setLayers([])
        dialog.tableWidget_postCellInspector.setRowCount(0)
        state.widgets.pop("map_layer", None)
        return
    if "run" in fields:
        _populate_tree(dialog)
        _overlays(dialog)
    if fields & {"run", "variable", "statistic", "period"}:
        show_map(dialog)
    elif "current_time" in fields:
        layer = state.widgets.get("map_layer")
        if layer is not None and layer.customProperty("mhm_qgis/instant", False):
            post_canvas.show_step(layer, int(state.context.get("current_time") or 0) + 1)
    if "gauge" in fields:
        _zoom_to_gauge(dialog)
    if fields & {"run", "cell", "current_time"}:
        _inspect(dialog)
    if fields & {"run", "cell", "variable", "period"}:
        _cell_plot(dialog)


def _populate_tree(dialog) -> None:
    state = dialog._post
    tree = dialog.treeWidget_postVariables
    chosen = state.context.get("variable")
    groups = {}
    with QSignalBlocker(tree):
        tree.clear()
        variables = [v for v in state.run.variables if v.file == "mhm"]
        if "pre" in state.index["variables"]:
            variables.append(Variable("pre", "precipitation", "Precipitation (forcing)", "meteorology",
                                      "flux", ("sum", "mean"), "cell"))
        for variable in variables:
            group = groups.get(variable.group)
            if group is None:
                group = groups[variable.group] = QTreeWidgetItem(tree, [variable.group.replace("_", " ").title()])
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item = QTreeWidgetItem(group, [variable.name])
            item.setData(0, Qt.ItemDataRole.UserRole, variable.name)
            item.setToolTip(0, variable.label if variable.present else variable.message)
            if not variable.present:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        tree.expandAll()
    if chosen not in state.index["variables"]:
        chosen = next((name for name in ("aET", "Q", *state.index["variables"]) if name in state.index["variables"]),
                      None)
    _select_variable(dialog, chosen)


def _select_variable(dialog, name) -> None:
    tree = dialog.treeWidget_postVariables
    for item in tree.findItems(name or "", Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive):
        tree.setCurrentItem(item)
        return


def _variable_chosen(dialog, item) -> None:
    name = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
    if name is None:
        return
    from ...core.post import aggregate

    info = dialog._post.index["variables"].get(name)
    combo = dialog.comboBox_postStatistic
    previous = combo.currentData()
    with QSignalBlocker(combo):
        combo.clear()
        if name != "pre":
            combo.addItem(LABELS["instant"], "instant")
        for stat in aggregate.map_statistics(info) if info else ():
            combo.addItem(LABELS[stat], stat)
        combo.setCurrentIndex(max(0, combo.findData(previous)))
    dialog.spinBox_postPercentile.setEnabled(combo.currentData() == "percentile")
    context = dialog._post.context
    if not context.set("variable", name):
        return
    context.set("statistic", _statistic(dialog))


def _statistic(dialog):
    stat = dialog.comboBox_postStatistic.currentData() or "instant"
    return (stat, dialog.spinBox_postPercentile.value() if stat == "percentile" else None)


def _statistic_chosen(dialog) -> None:
    dialog.spinBox_postPercentile.setEnabled(dialog.comboBox_postStatistic.currentData() == "percentile")
    dialog._post.context.set("statistic", _statistic(dialog))


def show_map(dialog) -> None:
    state = dialog._post
    name, (stat, q) = state.context.get("variable"), state.context.get("statistic") or ("instant", None)
    if name is None:
        return
    crs = _crs(dialog)
    wkt, root, run, period = crs.toWkt(), state.root, state.run, state.context.get("period")
    info = state.index["variables"].get(name, {})
    if stat == "instant":
        path = run.files["mhm"]

        def build(_task):
            from ...core.post import grid

            return post_canvas.build_vrt(root, path, name, grid.axes(path), wkt)

        def done(vrt):
            low, high = info.get("range") or (0.0, 1.0)
            layer = post_canvas.variable_layer(vrt, name, state.intervals, (low, high))
            layer.setCustomProperty("mhm_qgis/instant", True)
            _set_layer(dialog, layer)
            post_canvas.show_step(layer, int(state.context.get("current_time") or 0) + 1)

        work.submit(dialog, "map", f"Preparing {name}", build, done)
        return
    axes = state.axes

    def compute(task):
        from ...core.executions import post as post_exec
        from ...core.post import aggregate

        array = aggregate.statistic(root, name, stat, period, q)
        key = f"{name}_{stat}{q or ''}_{'-'.join(period) if period else 'all'}".replace(":", "")
        if array is None:
            target = root / "derived" / f"{key}.npy"
            target.parent.mkdir(exist_ok=True)
            first, last = period or (str(state.intervals[0, 0]), str(state.intervals[-1, 1]))
            post_exec.derive(task, {"what": "statistic", "path": str(run.files["mhm"]), "variable": name,
                                    "stat": stat, "q": q, "kind": info.get("kind"), "period": [first, last],
                                    "out": str(target)}, root)
            array = np.load(target)
        tif = post_canvas.write_geotiff(array, axes, wkt, root / "derived" / f"{key}.tif")
        finite = array[np.isfinite(array)]
        return tif, (np.percentile(finite, [2, 98]).tolist() if finite.size else [0.0, 1.0])

    def done(result):
        tif, value_range = result
        layer = post_canvas.raster_layer(tif, f"{name} {LABELS[stat].lower()}", value_range,
                                         diverging=stat == "anomaly")
        _set_layer(dialog, layer)

    work.submit(dialog, "map", f"Computing the {LABELS[stat].lower()} of {name}", compute, done)


def _set_layer(dialog, layer) -> None:
    state = dialog._post
    canvas = dialog.mapCanvas_post
    first = state.widgets.get("map_layer") is None
    state.widgets["map_layer"] = layer
    canvas.setDestinationCrs(layer.crs())
    canvas.setLayers([*state.widgets.get("overlays", []), layer])
    if first:
        canvas.setExtent(layer.extent())
    canvas.refresh()


def _overlays(dialog) -> None:
    """Basin outlines and the river network over every map."""
    from ...core.handlers.store.paths import temp_morphology_folder

    state = dialog._post
    masks = {name: np.load(state.root / "weights" / f"{name}.npy")
             for name in next(iter(state.index["variables"].values()), {}).get("basins", [])}
    layers = [post_canvas.outline_layer(masks, state.axes, dialog.get_crs())] if masks else []
    network = post_canvas.network_layer(f"{temp_morphology_folder(state.run.ref.domain)}/2_channel_network.shp")
    state.widgets["overlays"] = [layer for layer in (*layers, network) if layer is not None]
    state.widgets.pop("map_layer", None)


def _zoom_to_gauge(dialog) -> None:
    state = dialog._post
    gauge = state.context.get("gauge")
    outlines = next(iter(state.widgets.get("overlays", [])), None)
    if gauge is None or outlines is None:
        return
    for feature in outlines.getFeatures():
        if feature["basin"] == str(gauge):
            box = feature.geometry().boundingBox()
            box.scale(1.2)
            dialog.mapCanvas_post.setExtent(box)
            dialog.mapCanvas_post.flashGeometries([feature.geometry()], outlines.crs())
            dialog.mapCanvas_post.refresh()


def clicked(dialog, x, y) -> None:
    from ...core.post import grid

    state = dialog._post
    if state.axes is None:
        return
    cell = grid.cell_index(state.axes, x, y)
    if cell is not None:
        state.context.set("cell", cell)


def _inspect(dialog) -> None:
    from ...core.post import series

    state = dialog._post
    table = dialog.tableWidget_postCellInspector
    cell = state.context.get("cell")
    band = state.widgets.pop("cell_band", None)
    if band is not None:
        band.reset()
    if cell is None:
        table.setRowCount(0)
        dialog.label_postCell.setText("Click a cell on the map.")
        return
    iy, ix = cell
    state.widgets["cell_band"] = post_canvas.highlight_cell(dialog.mapCanvas_post, state.axes, iy, ix,
                                                            dialog.get_crs())
    step = int(state.context.get("current_time") or 0)
    rows = [("Cell area (km²)", np.load(state.root / "area.npy")[iy, ix] / 1e6),
            ("Domain coverage", np.load(state.root / "weights" / "domain.npy")[iy, ix])]
    if (state.root / "sealed.npy").is_file():
        scene = np.load(state.root / "scenes.npy")[step]
        rows.append(("Sealed fraction", np.load(state.root / "sealed.npy")[scene, iy, ix]))
    for name in state.index["variables"]:
        values = series.cell(state.root, name, iy, ix)
        rows.append((name, values.iloc[step] if values is not None else None))
    dialog.label_postCell.setText(f"Row {iy}, column {ix}; values at {str(state.intervals[step, 0])[:16]}")
    table.setRowCount(len(rows))
    for row, (name, value) in enumerate(rows):
        table.setItem(row, 0, QTableWidgetItem(name))
        text = "no series cache" if value is None else f"{float(value):.4g}"
        table.setItem(row, 1, QTableWidgetItem(text))


def _cell_plot(dialog) -> None:
    from ...core.post import series

    state = dialog._post
    plot = plot_widget(dialog, dialog.widget_postCellPlot)
    plot.clear()
    cell, name = state.context.get("cell"), state.context.get("variable")
    if cell is None or name is None:
        return
    period = state.context.get("period")
    values = series.cell(state.root, name, *cell, period)
    if values is not None:
        plot.plot(seconds(values.index), values.to_numpy(), pen="#2c7fb8", name=name)
        return
    if name == "pre":
        return
    path, root = state.run.files["mhm"], state.root

    def read(task):
        from ...core.executions import post as post_exec

        return post_exec.derive(task, {"what": "cell", "path": str(path), "variable": name,
                                       "iy": int(cell[0]), "ix": int(cell[1])}, root)

    def done(read_values):
        if state.context.get("cell") == cell and state.context.get("variable") == name:
            plot.plot(seconds(state.intervals[:, 0]), np.asarray(read_values, float), pen="#2c7fb8", name=name)

    work.submit(dialog, "cell", f"Reading {name} at row {cell[0]}, column {cell[1]}", read, done)


def build_cache(dialog) -> None:
    """Rescan with the chosen variable's series cached too."""
    from ...core.executions import post as post_exec

    state = dialog._post
    name = state.context.get("variable")
    if state.root is None or name is None:
        return
    ref = state.run.ref
    wanted = tuple({*SERIES_DEFAULT, *(v for v in state.index["variables"] if (state.root / "series" /
                                                                                f"{v}.npy").is_file()), name})

    def rescan(task):
        job = post_exec.scan_job(ref, series=wanted, run=state.run)
        post_exec.scan(task, job)

    def done(_result):
        from ...core.post import cache

        state.index = cache.load_index(state.root)
        refresh(dialog, {"cell", "variable"})

    work.submit(dialog, "scan", f"Building the series cache for {name}", rescan, done)


def open_in_qgis(dialog) -> None:
    state = dialog._post
    layer = state.widgets.get("map_layer")
    if layer is not None:
        post_canvas.open_in_qgis(layer, f"{layer.name()} ({state.run.ref.label})")

