"""Export of the shown view (P8): its tables, plots and maps into one folder. No report.

Tables are written as shown (CSV; only the thousands separators go), plots through
pyqtgraph's exporters (PNG, SVG and their curves as CSV), and maps as GeoTIFF and CF
netCDF by GDAL on a task: an instant map's VRT reads the run's netCDF output.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from qgis.PyQt.QtWidgets import QFileDialog, QMenu, QTableWidget

from ..workflows import post as work


def bind(dialog) -> None:
    from . import post_context

    menu = QMenu(dialog.toolButton_postExport)
    menu.addAction("Export the shown view…").triggered.connect(lambda: export_view(dialog))
    menu.addAction("Archive the current run…").triggered.connect(lambda: post_context.archive(dialog))
    dialog.toolButton_postExport.setMenu(menu)


def _stem(*parts) -> str:
    return re.sub(r"[^\w.-]+", "_", "_".join(str(part) for part in parts if part)).strip("_")


def _cell(text):
    """A shown number without thousands separators (text stays as it is)."""
    plain = text.replace(",", "")
    try:
        float(plain)
    except ValueError:
        return text
    return plain


def write_table(table, path) -> Path:
    # ponytail: the shown precision (3 significant digits in most tables); keep the
    # values in the items' UserRole if full precision is ever needed.
    def header(item, fallback):
        return item.text() if item is not None else fallback

    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["", *(header(table.horizontalHeaderItem(c), str(c)) for c in range(table.columnCount()))])
        for r in range(table.rowCount()):
            writer.writerow([header(table.verticalHeaderItem(r), str(r + 1)),
                             *(_cell(table.item(r, c).text()) if table.item(r, c) else ""
                               for c in range(table.columnCount()))])
    return Path(path)


def _plots(dock, out, name) -> list[Path]:
    import pyqtgraph as pg
    from pyqtgraph import exporters

    written = []
    for view in dock.findChildren(pg.GraphicsView):
        items = [view.getPlotItem()] if isinstance(view, pg.PlotWidget) else \
            [item for item in view.ci.items if isinstance(item, pg.PlotItem)]
        items = [item for item in items if item.listDataItems()]
        if not view.isVisible() or not items:
            continue
        stem = out / _stem(name, view.parent().objectName().removeprefix("widget_post"))
        whole = items[0] if isinstance(view, pg.PlotWidget) else view.scene()
        exporters.ImageExporter(whole).export(f"{stem}.png")
        exporters.SVGExporter(whole).export(f"{stem}.svg")
        written += [Path(f"{stem}.png"), Path(f"{stem}.svg")]
        for index, item in enumerate(items):
            target = f"{stem}{f'_{index + 1}' if len(items) > 1 else ''}.csv"
            exporters.CSVExporter(item).export(target)
            written.append(Path(target))
    return written


def _rasters(dialog, dock, name) -> list[tuple]:
    """(source, band, stem) of the raster layers on the map canvases of a panel or dock."""
    from qgis.core import QgsRasterLayer
    from qgis.gui import QgsMapCanvas

    found = []
    for canvas in dock.findChildren(QgsMapCanvas):
        for layer in canvas.layers():
            if not isinstance(layer, QgsRasterLayer) or layer.providerType() != "gdal":  # not the basemap
                continue
            found.append(_raster(dialog, layer, name))
    return found


def _raster(dialog, layer, name) -> tuple:
    """(source, band, stem) of a raster layer: an instant map's shown band, with its time."""
    from ...qgis_bridge.layers.compat import input_band

    instant = layer.customProperty("mhm_qgis/instant", False)
    band = input_band(layer.renderer()) if instant else 1
    when = str(dialog._post.intervals[band - 1, 0])[:16] if instant else ""
    return layer.source(), band, _stem(name, layer.name(), when)


def export_view(dialog, folder=None) -> list[Path]:
    """Write the shown panel's and docks' tables and plots now, and their maps (with
    Map Explorer's, on the main canvas) on a task."""
    state = dialog._post
    if not state.workspace or state.root is None:
        dialog.label_postStatus.setText("Open a run and a view first: nothing is shown to export.")
        return []
    if folder is None:
        folder = QFileDialog.getExistingDirectory(dialog, "Export the shown view to", str(dialog.project_folder or ""))
        if not folder:
            return []
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    written, rasters = [], []
    for name, shown in (*state.panels.items(), *state.docks.items()):
        if not shown.isVisible():
            continue
        written += [write_table(table, out / f"{_stem(name, table.objectName().removeprefix('tableWidget_post'))}.csv")
                    for table in shown.findChildren(QTableWidget) if table.rowCount() and table.isVisible()]
        written += _plots(shown, out, name)
        rasters += _rasters(dialog, shown, name)
    explorer = state.widgets.get("map_layer")
    if state.view == "map_explorer" and explorer is not None:
        rasters += [_raster(dialog, explorer, "map_explorer")]

    def translate(_task):
        from osgeo import gdal

        files = []
        for source, band, stem in rasters:
            for driver, suffix in (("GTiff", "tif"), ("netCDF", "nc")):  # netCDF: CF, with the CRS
                target = out / f"{stem}.{suffix}"
                dataset = gdal.Translate(str(target), source, format=driver, bandList=[band])
                if dataset is None:
                    raise ValueError(f"Could not write {target.name}: {gdal.GetLastErrorMsg()}")
                dataset = None  # closes and flushes
                files.append(target)
        return files

    def done(files):
        dialog.log_message(f"Exported {len(written) + len(files)} files of the {state.view} view to {out}.")

    work.submit(dialog, "export", "Exporting the maps", translate, done)
    return written
