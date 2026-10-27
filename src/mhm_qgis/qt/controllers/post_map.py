"""Map Explorer: variable tree, statistic, the map, the cell inspector and its plot.

Instant maps are VRT band switches (the VRT is built on a task: it reads netCDF);
statistic maps come from the cache, or a `derive` pass in the native worker, and are
shown as GeoTIFFs. A click picks an L1 cell; a gauge choice zooms to its catchment.

Map Explorer draws on the host's main canvas (post plan §0c). That canvas follows the
project's layer tree, so a copy of the map and its overlays goes into the temporary
group "mHM post (temporary)" (removed by `leave`), and our click tool replaces the
user's map tool while the view is shown. The other views use the Map dock, whose
layers stay out of the project.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from qgis.PyQt.QtCore import QSignalBlocker, Qt
from qgis.PyQt.QtWidgets import QTableWidgetItem, QTreeWidgetItem

from ...core.post.catalog import Variable
from ...qgis_bridge.display import post_canvas
from ..workflows import post as work
from .post_context import plot_widget, seconds

LABELS = {"instant": "Instant", "mean": "Mean", "sum": "Sum", "std": "Std. deviation", "min": "Minimum",
          "max": "Maximum", "percentile": "Percentile", "anomaly": "Anomaly"}


def bind(dialog) -> None:
    from qgis.gui import QgsMapToolEmitPoint

    state = dialog._post
    dialog._post.ui.treeWidget_postVariables.currentItemChanged.connect(lambda item, _old: _variable_chosen(dialog, item))
    dialog._post.ui.comboBox_postStatistic.activated.connect(lambda _index: _statistic_chosen(dialog))
    dialog._post.ui.spinBox_postPercentile.valueChanged.connect(lambda _value: _statistic_chosen(dialog))
    # pushButton_postBuildCacheAll → build_cache
    dialog._post.ui.pushButton_postBuildCacheAll.clicked.connect(lambda: build_cache(dialog))
    # pushButton_postOpenInQgis → post_canvas.open_in_qgis
    dialog._post.ui.pushButton_postOpenInQgis.clicked.connect(lambda: open_in_qgis(dialog))
    tool = QgsMapToolEmitPoint(dialog._post.ui.mapCanvas_post)
    tool.canvasClicked.connect(lambda point, button: clicked(dialog, point.x(), point.y(), button,
                                                            dialog._post.ui.mapCanvas_post))
    dialog._post.ui.mapCanvas_post.setMapTool(tool)
    state.widgets["map_tool"] = tool


def _crs(dialog):
    return post_canvas.crs_for(dialog._post.axes, dialog.get_crs())


def refresh(dialog, fields) -> None:
    state = dialog._post
    if state.root is None:
        dialog._post.ui.treeWidget_postVariables.clear()
        dialog._post.ui.mapCanvas_post.setLayers([])
        dialog._post.ui.tableWidget_postCellInspector.setRowCount(0)
        state.widgets.pop("map_layer", None)
        return
    if "run" in fields:
        _populate_tree(dialog)
        _overlays(dialog)
    if fields & {"run", "variable", "statistic", "period"}:
        show_map(dialog)
    elif "current_time" in fields:
        band = int(state.context.get("current_time") or 0) + 1
        for layer in (state.widgets.get("map_layer"), _main_layer(dialog)):
            if layer is not None and layer.customProperty("mhm_qgis/instant", False):
                post_canvas.show_step(layer, band)
    if "gauge" in fields:
        _zoom_to_gauge(dialog)
    if fields & {"run", "cell", "current_time", "statistic", "period"}:
        _inspect(dialog)
    if fields & {"run", "cell", "variable", "period"}:
        _cell_plot(dialog)
    elif "current_time" in fields:
        _time_line(dialog)


def _populate_tree(dialog) -> None:
    state = dialog._post
    tree = dialog._post.ui.treeWidget_postVariables
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
    tree = dialog._post.ui.treeWidget_postVariables
    for item in tree.findItems(name or "", Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive):
        tree.setCurrentItem(item)
        return


def _variable_chosen(dialog, item) -> None:
    name = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
    if name is None:
        return
    from ...core.post import aggregate

    info = dialog._post.index["variables"].get(name)
    combo = dialog._post.ui.comboBox_postStatistic
    previous = combo.currentData()
    with QSignalBlocker(combo):
        combo.clear()
        if name != "pre":
            combo.addItem(LABELS["instant"], "instant")
        for stat in aggregate.map_statistics(info) if info else ():
            combo.addItem(LABELS[stat], stat)
        combo.setCurrentIndex(max(0, combo.findData(previous)))
    dialog._post.ui.spinBox_postPercentile.setEnabled(combo.currentData() == "percentile")
    context = dialog._post.context
    if not context.set("variable", name):
        return
    context.set("statistic", _statistic(dialog))


def _statistic(dialog):
    stat = dialog._post.ui.comboBox_postStatistic.currentData() or "instant"
    return (stat, dialog._post.ui.spinBox_postPercentile.value() if stat == "percentile" else None)


def _statistic_chosen(dialog) -> None:
    dialog._post.ui.spinBox_postPercentile.setEnabled(dialog._post.ui.comboBox_postStatistic.currentData() == "percentile")
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


# views that show their own map on the shared Map dock
VIEW_MAPS = ("water_balance", "validation", "extremes", "drought", "anomalies", "summary")
TEMPORARY = "mHM post (temporary)"  # Map Explorer's layers on the main canvas


def _set_layer(dialog, layer) -> None:
    """The Map Explorer's layer: on the main canvas while Map Explorer is shown (else
    when it is next shown), and on the Map dock unless the view has its own map."""
    state = dialog._post
    state.widgets["map_layer"] = layer
    state.widgets["main_stale"] = True
    if state.view not in VIEW_MAPS:
        show_layers(dialog, [layer])
    if state.view == "map_explorer":
        _sync_main(dialog)


def set_view_layers(dialog, view, layers) -> None:
    """A view's own map (balance term, validation score) on the shared Map dock."""
    state = dialog._post
    state.widgets.setdefault("view_layers", {})[view] = layers
    if state.view == view:
        show_layers(dialog, layers)


def view_changed(dialog, view) -> None:
    """Map Explorer: its map and our click tool on the main canvas. Other views: the
    user's map tool back, and the view's own map (or the explorer's) on the Map dock."""
    state = dialog._post
    _main_tool(dialog, view == "map_explorer")
    if view == "map_explorer":
        _sync_main(dialog)
    if "map" in state.docks:
        layers = state.widgets.get("view_layers", {}).get(view) if view in VIEW_MAPS else None
        explorer = state.widgets.get("map_layer")
        show_layers(dialog, layers or ([explorer] if explorer is not None else []))
    if state.root is not None:
        _highlight(dialog)


def _main_layer(dialog):
    """The main canvas's copy of the explorer map (None if absent or removed by the user)."""
    from qgis.core import QgsProject

    found = dialog._post.widgets.get("main_layer_id")
    return QgsProject.instance().mapLayer(found) if found else None


def _sync_main(dialog) -> None:
    """Put the explorer map and fresh overlays in the temporary group (top of the tree)."""
    from qgis.core import QgsProject

    state = dialog._post
    layer = state.widgets.get("map_layer")
    if layer is None or not state.widgets.get("main_stale", True):
        return
    _clear_main(dialog)
    project = QgsProject.instance()
    group = project.layerTreeRoot().insertGroup(0, TEMPORARY)
    overlays = _overlay_layers(dialog)
    for overlay in overlays:  # drawn over the map
        project.addMapLayer(overlay, False)
        group.addLayer(overlay)
    copy = post_canvas.open_in_qgis(layer, layer.name(), TEMPORARY)
    copy.setCustomProperty("mhm_qgis/instant", layer.customProperty("mhm_qgis/instant", False))
    if copy.customProperty("mhm_qgis/instant", False):
        post_canvas.show_step(copy, layer.renderer().inputBand())
    state.widgets.update(main_ids=[*(item.id() for item in overlays), copy.id()], main_layer_id=copy.id(),
                         main_stale=False)
    from qgis.core import QgsCoordinateTransform

    from ..docks import host

    main = host.canvas(dialog)  # layers added to a group don't zoom the canvas: show the run if it's out of view
    extent = QgsCoordinateTransform(copy.crs(), main.mapSettings().destinationCrs(), project).transformBoundingBox(
        copy.extent())
    if not main.extent().intersects(extent) or main.extent().width() > 20 * extent.width():
        main.setExtent(extent)
        main.refresh()


def _clear_main(dialog) -> None:
    from qgis.core import QgsProject

    project, widgets = QgsProject.instance(), dialog._post.widgets
    project.removeMapLayers([found for found in widgets.pop("main_ids", []) if project.mapLayer(found)])
    widgets.pop("main_layer_id", None)
    widgets["main_stale"] = True
    group = project.layerTreeRoot().findGroup(TEMPORARY)
    if group is not None:
        group.parent().removeChildNode(group)


def _main_tool(dialog, on) -> None:
    """Our click tool on the main canvas while Map Explorer is shown; the user's back after."""
    from qgis.gui import QgsMapToolEmitPoint

    from ..docks import host

    widgets, main = dialog._post.widgets, host.canvas(dialog)
    tool = widgets.get("main_tool")
    if on:
        if tool is None:
            tool = widgets["main_tool"] = QgsMapToolEmitPoint(main)
            tool.canvasClicked.connect(lambda point, button: clicked(dialog, point.x(), point.y(), button, main))
        if main.mapTool() is not tool:
            widgets["previous_tool"] = main.mapTool()
            main.setMapTool(tool)
    elif tool is not None and main.mapTool() is tool:
        previous = widgets.pop("previous_tool", None)
        try:
            main.setMapTool(previous) if previous is not None else main.unsetMapTool(tool)
        except RuntimeError:  # the user's tool was deleted meanwhile
            main.unsetMapTool(tool)


def leave(dialog) -> None:
    """The analysis layout ends: the user's map tool back, our layers and marks gone."""
    if getattr(dialog._post.ui, "mapCanvas_post", None) is None and "main_tool" not in dialog._post.widgets:
        return
    _main_tool(dialog, False)
    _clear_main(dialog)
    band = dialog._post.widgets.pop("cell_band", None)
    if band is not None:
        band.reset()
        if band.scene() is not None:
            band.scene().removeItem(band)


def _cell_canvas(dialog):
    """Where the cell is marked and gauges are shown: the main canvas in Map Explorer."""
    from ..docks import host

    state = dialog._post
    return host.canvas(dialog) if state.view == "map_explorer" else getattr(state.ui, "mapCanvas_post", None)


def array_layer(dialog, array, name, stem, *, diverging=False, value_range=None, axes=None):
    """A GeoTIFF layer of a (ny, nx) map on the run's grid (or `axes`), styled 2-98 %."""
    state = dialog._post
    axes = axes or state.axes
    crs = post_canvas.crs_for(axes, dialog.get_crs())
    tif = post_canvas.write_geotiff(np.asarray(array, float), axes, crs.toWkt(), state.root / "derived" / f"{stem}.tif")
    if value_range is None:
        finite = np.asarray(array, float)[np.isfinite(array)]
        value_range = np.percentile(finite, [2, 98]).tolist() if finite.size else (0.0, 1.0)
    return post_canvas.raster_layer(tif, name, tuple(map(float, value_range)), diverging=diverging)


def show_layers(dialog, layers, canvas=None) -> None:
    """Layers (under the basin outlines and rivers) on the shared Map over the basemap, or on `canvas`."""
    state = dialog._post
    basemap = state.widgets.get("basemap", []) if canvas is None else []
    canvas = canvas or getattr(state.ui, "mapCanvas_post", None)
    if canvas is None or not layers:
        return
    first = not canvas.layers()
    canvas.setDestinationCrs(layers[-1].crs())
    canvas.setLayers([*state.widgets.get("overlays", []), *layers, *basemap])
    if first:
        canvas.setExtent(layers[-1].extent())
    canvas.refresh()
    if basemap and "map" in state.docks:
        state.docks["map"].setWindowTitle(f"mHM post: Map: {layers[-1].name()}")


def _overlay_layers(dialog) -> list:
    """Fresh basin outlines and the river network (cut to the domain) of the open run."""
    from qgis.core import QgsVectorLayer

    from ...core.handlers.store.paths import temp_morphology_folder

    state = dialog._post
    masks = {name: np.load(state.root / "weights" / f"{name}.npy")
             for name in next(iter(state.index["variables"].values()), {}).get("basins", [])}
    layers = [post_canvas.outline_layer(masks, state.axes, dialog.get_crs())] if masks else []
    morphology = Path(temp_morphology_folder(state.run.ref.domain))
    merged = morphology / "Watersheds" / "4_watershed_merged_vector.shp"
    clip = QgsVectorLayer(str(merged), "domain", "ogr") if merged.is_file() else next(iter(layers), None)
    network = post_canvas.network_layer(morphology / "2_channel_network.shp", clip=clip)
    return [layer for layer in (*layers, network) if layer is not None]


def _basemap():
    """QGIS's OpenStreetMap XYZ tiles, under the Map dock's layers (blank offline)."""
    from qgis.core import QgsRasterLayer

    return QgsRasterLayer("type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmin=0&zmax=19",
                          "OpenStreetMap", "wms")


def _overlays(dialog) -> None:
    """Basin outlines and the river network over every map (a new run)."""
    state = dialog._post
    state.widgets["overlays"] = _overlay_layers(dialog)
    if "basemap" not in state.widgets:
        state.widgets["basemap"] = [_basemap()]
    state.widgets.pop("map_layer", None)
    state.widgets.pop("view_layers", None)
    state.widgets.pop("cell_values", None)
    _clear_main(dialog)
    state.ui.mapCanvas_post.setLayers([])  # a new run: zoom to its first map


def _zoom_to_gauge(dialog) -> None:
    state = dialog._post
    gauge = state.context.get("gauge")
    outlines = next(iter(state.widgets.get("overlays", [])), None)
    if gauge is None or outlines is None:
        return
    from qgis.core import QgsCoordinateTransform, QgsProject

    canvas = _cell_canvas(dialog)
    for feature in outlines.getFeatures():
        if feature["basin"] == str(gauge) and canvas is not None:
            box = QgsCoordinateTransform(outlines.crs(), canvas.mapSettings().destinationCrs(),
                                         QgsProject.instance()).transformBoundingBox(feature.geometry().boundingBox())
            box.scale(1.2)
            canvas.setExtent(box)
            canvas.flashGeometries([feature.geometry()], outlines.crs())
            canvas.refresh()


def clicked(dialog, x, y, button=None, canvas=None) -> None:
    """A click (in `canvas`'s CRS, else the run's) picks the cell; a right click offers
    "Why here?" for it."""
    from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject

    from ...core.post import grid

    state = dialog._post
    if state.axes is None:
        return
    if canvas is not None and canvas.mapSettings().destinationCrs() != _crs(dialog):
        point = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), _crs(dialog),
                                       QgsProject.instance()).transform(QgsPointXY(x, y))
        x, y = point.x(), point.y()
    cell = grid.cell_index(state.axes, x, y)
    if cell is None:
        return
    state.context.set("cell", cell)
    if button == Qt.MouseButton.RightButton:
        from qgis.PyQt.QtGui import QCursor
        from qgis.PyQt.QtWidgets import QMenu

        from . import post_advanced

        menu = QMenu(canvas or state.ui.mapCanvas_post)
        action = menu.addAction("Why here?")
        if menu.exec(QCursor.pos()) is action:
            post_advanced.why_here(dialog)


def _cell_values(dialog):
    """The context cell's full-run series per variable and whether more are being read:
    cached ones at once, the rest in one background pass that refreshes the inspector and plot."""
    from ...core.post import series

    state = dialog._post
    cell = state.context.get("cell")
    found = state.widgets.get("cell_values")
    if found is not None and found[0] == cell:
        return found[1:]
    values = {}
    for name in state.index["variables"]:
        cached = series.cell(state.root, name, *cell)
        if cached is not None:
            values[name] = cached.to_numpy()
    inside = np.load(state.root / "weights" / "domain.npy")[int(cell[0]), int(cell[1])] > 0
    in_file = {v.name for v in state.run.variables if v.file == "mhm" and v.present}
    missing = [name for name in state.index["variables"] if inside and name in in_file and name not in values]
    state.widgets["cell_values"] = (cell, values, bool(missing))
    if not missing:
        return values, False
    path, root = state.run.files["mhm"], state.root

    def read(task):
        from ...core.executions import post as post_exec

        return post_exec.derive(task, {"what": "cell", "path": str(path), "variables": missing,
                                       "iy": int(cell[0]), "ix": int(cell[1])}, root)

    def done(read_values):
        if state.widgets.get("cell_values", (None,))[0] == cell:
            state.widgets["cell_values"] = (cell, values | {n: np.asarray(v, "f8") for n, v in read_values.items()},
                                            False)
            _inspect(dialog)
            _cell_plot(dialog)

    work.submit(dialog, "cell", f"Reading row {cell[0]}, column {cell[1]}", read, done,
                lambda *_: state.widgets.pop("cell_values", None))
    return values, True


def _inspect(dialog) -> None:
    from ...core.post import aggregate

    state = dialog._post
    table = dialog._post.ui.tableWidget_postCellInspector
    cell = state.context.get("cell")
    _highlight(dialog)
    if cell is None:
        table.setRowCount(0)
        dialog._post.ui.label_postCell.setText("Click a cell on the map.")
        return
    iy, ix = cell
    step = int(state.context.get("current_time") or 0)
    stat, q = state.context.get("statistic") or ("instant", None)
    period = state.context.get("period")
    rows = [("Cell area", np.load(state.root / "area.npy")[iy, ix] / 1e6, "km²"),
            ("Domain coverage", np.load(state.root / "weights" / "domain.npy")[iy, ix], "-")]
    if (state.root / "sealed.npy").is_file():
        scene = np.load(state.root / "scenes.npy")[step]
        rows.append(("Sealed fraction", np.load(state.root / "sealed.npy")[scene, iy, ix], "-"))
    (values, reading), units = _cell_values(dialog), {v.name: v.units for v in state.run.variables}
    for name, info in state.index["variables"].items():
        found = values.get(name)
        allowed = stat == "instant" or stat in aggregate.map_statistics(info)
        rows.append((name, aggregate.cell_statistic(found, state.intervals, info["kind"], stat, period, q, step)
                     if found is not None and allowed else None, units.get(name, "")))
    when = f"at {str(state.intervals[step, 0])[:16]}" if stat == "instant" else "over the period"
    dialog._post.ui.label_postCell.setText(f"Row {iy}, column {ix}; {LABELS[stat].lower()} {when}")
    table.setRowCount(len(rows))
    for row, (name, value, unit) in enumerate(rows):
        text = f"{float(value):.4g}" if value is not None else "reading…" if reading and name not in values else "-"
        for column, cell_text in enumerate((name, text, unit)):
            table.setItem(row, column, QTableWidgetItem(cell_text))


def _highlight(dialog) -> None:
    """Outline the context's cell on the map in use (moved when the view changes)."""
    state = dialog._post
    band = state.widgets.pop("cell_band", None)
    if band is not None:
        band.reset()
        if band.scene() is not None:
            band.scene().removeItem(band)
    cell, canvas = state.context.get("cell"), _cell_canvas(dialog)
    if cell is not None and canvas is not None:
        state.widgets["cell_band"] = post_canvas.highlight_cell(canvas, state.axes, *cell, dialog.get_crs())


def _cell_plot(dialog) -> None:
    import pyqtgraph as pg

    from ...core.post import aggregate

    state = dialog._post
    plot = plot_widget(dialog, dialog._post.ui.widget_postCellPlot)
    plot.clear()
    if "cell_time" not in state.widgets:
        state.widgets["cell_time"] = pg.InfiniteLine(angle=90, pen=pg.mkPen("#e31a1c", width=1.5))
    plot.addItem(state.widgets["cell_time"])
    _time_line(dialog)
    cell, name = state.context.get("cell"), state.context.get("variable")
    values = _cell_values(dialog)[0] if cell is not None else {}
    if name not in values:
        return
    keep = aggregate.period_mask(state.intervals, state.context.get("period"))
    plot.plot(seconds(state.intervals[keep, 0]), values[name][keep], pen="#2c7fb8", name=name)


def _time_line(dialog) -> None:
    """The cell plot's line at the current time."""
    state = dialog._post
    line = state.widgets.get("cell_time")
    if line is not None and state.intervals is not None:
        step = int(state.context.get("current_time") or 0)
        line.setValue(seconds(state.intervals[step:step + 1, 0])[0])


def build_cache(dialog) -> None:
    """Rescan with every scanned variable's series cached."""
    from ...core.executions import post as post_exec

    state = dialog._post
    if state.root is None:
        return
    ref, wanted = state.run.ref, tuple(state.index["variables"])

    def rescan(task):
        job = post_exec.scan_job(ref, series=wanted, run=state.run)
        post_exec.scan(task, job)

    def done(_result):
        from ...core.post import cache

        state.index = cache.load_index(state.root)
        refresh(dialog, {"cell", "variable"})

    work.submit(dialog, "scan", "Building the series cache for all variables", rescan, done)


def open_in_qgis(dialog) -> None:
    state = dialog._post
    layer = state.widgets.get("map_layer")
    if layer is not None:
        post_canvas.open_in_qgis(layer, f"{layer.name()} ({state.run.ref.label})")

