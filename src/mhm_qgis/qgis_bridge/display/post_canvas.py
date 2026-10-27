"""The post-processing map layer: VRT wrappers, band-switch time, styles, overlays.

Renders only; every number comes from `core/post`. mHM output has no
georeferencing GDAL can see (F7), so each (run, variable) gets a VRT in the run's
cache: the placement from the axis bounds, the CRS from the setup (EPSG:4326 for
geographic runs), and one band per time step pointing into the netCDF, with no
data copied. GDAL's netCDF driver serves some files south-first (F16: Rapti), so
the row order is measured per file and the geotransform follows it; QGIS reads
both north-up (checked in P4). Time is a renderer band switch, no file I/O.
"""
from __future__ import annotations

import os
from html import escape
from pathlib import Path

import numpy as np

GROUP = "mHM post"
STOPS = 11


def _served_flipped(path, variable) -> bool:
    """Whether GDAL serves this variable south-first (F16), from its first step."""
    import netCDF4
    from osgeo import gdal

    with netCDF4.Dataset(path) as handle:
        stored = np.ma.filled(np.ma.asarray(handle[variable][0], "f8"), np.nan)
    dataset = gdal.Open(f'NETCDF:"{path}":{variable}')  # keep it: a band dies with its dataset
    band = dataset.GetRasterBand(1)
    served = band.ReadAsArray().astype("f8")
    if band.GetNoDataValue() is not None:
        served[served == band.GetNoDataValue()] = np.nan
    if np.allclose(served, stored, equal_nan=True):
        return False
    if np.allclose(served[::-1], stored, equal_nan=True):
        return True
    raise ValueError(f"GDAL reads {Path(path).name}:{variable} in an unexpected layout.")


def build_vrt(root, path, variable, axes, crs_wkt) -> Path:
    """`<root>/vrt/<variable>.vrt`, rebuilt only when the netCDF is newer."""
    import netCDF4

    target = Path(root) / "vrt" / f"{variable}.vrt"
    if target.is_file() and target.stat().st_mtime_ns >= Path(path).stat().st_mtime_ns:
        return target
    with netCDF4.Dataset(path) as handle:
        var = handle[variable]
        steps, dtype = var.shape[0], "Float32" if var.dtype == np.float32 else "Float64"
        nodata = f"<NoDataValue>{float(var._FillValue)!r}</NoDataValue>" if "_FillValue" in var.ncattrs() else ""
    dy = abs(float(axes.y_bnds[0, 1] - axes.y_bnds[0, 0]))
    left = float(axes.x_bnds.min())
    transform = ((left, axes.cell, 0, float(axes.y_bnds.min()), 0, dy) if _served_flipped(path, variable)
                 else (left, axes.cell, 0, float(axes.y_bnds.max()), 0, -dy))
    source = escape(f'NETCDF:"{path}":{variable}', quote=False)
    bands = "".join(
        f'<VRTRasterBand dataType="{dtype}" band="{i}">{nodata}<SimpleSource>'
        f'<SourceFilename relativeToVRT="0">{source}</SourceFilename><SourceBand>{i}</SourceBand>'
        f"</SimpleSource></VRTRasterBand>\n" for i in range(1, steps + 1))
    ny, nx = axes.shape
    text = (f'<VRTDataset rasterXSize="{nx}" rasterYSize="{ny}">\n<SRS>{escape(crs_wkt, quote=False)}</SRS>\n'
            f"<GeoTransform>{', '.join(f'{float(v):.17g}' for v in transform)}</GeoTransform>\n"
            f"{bands}</VRTDataset>\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    return target


def crs_for(axes, crs):
    """EPSG:4326 for geographic runs, else the setup's CRS (mHM writes none, F7)."""
    from qgis.core import QgsCoordinateReferenceSystem

    return QgsCoordinateReferenceSystem("EPSG:4326") if axes.geographic else crs


def _qdatetime(moment):
    from qgis.PyQt.QtCore import QDateTime, Qt

    return QDateTime.fromString(str(np.datetime64(moment, "s")), Qt.DateFormat.ISODate)


def set_time_ranges(layer, bounds) -> None:
    """One fixed range per band from `time_bnds`, so QGIS's own temporal tools animate it."""
    from qgis.core import Qgis, QgsDateTimeRange

    properties = layer.temporalProperties()
    properties.setMode(Qgis.RasterTemporalMode.FixedRangePerBand)
    properties.setFixedRangePerBand({index + 1: QgsDateTimeRange(_qdatetime(start), _qdatetime(end), True, False)
                                     for index, (start, end) in enumerate(bounds)})
    properties.setIsActive(True)


def style(layer, low, high, *, diverging=False, band=1) -> None:
    """A fixed colour ramp: Viridis from `low` to `high` (the scan's 2nd and 98th
    percentiles, so colours hold while animating) or RdBu centred on 0 for differences."""
    from qgis.core import (
        Qgis,
        QgsColorRampShader,
        QgsRasterShader,
        QgsSingleBandPseudoColorRenderer,
        QgsStyle,
    )

    if diverging:
        low, high = -max(abs(low), abs(high)), max(abs(low), abs(high))
    if not high > low:
        low, high = low - 0.5, low + 0.5
    ramp = QgsStyle.defaultStyle().colorRamp("RdBu" if diverging else "Viridis")
    shader = QgsColorRampShader(low, high)
    shader.setColorRampType(Qgis.ShaderInterpolationMethod.Linear)
    shader.setColorRampItemList([QgsColorRampShader.ColorRampItem(
        low + (high - low) * i / (STOPS - 1), ramp.color(i / (STOPS - 1))) for i in range(STOPS)])
    raster_shader = QgsRasterShader(low, high)
    raster_shader.setRasterShaderFunction(shader)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), band, raster_shader)
    renderer.setClassificationMin(low)
    renderer.setClassificationMax(high)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def variable_layer(vrt, name, bounds, value_range, *, diverging=False):
    """A styled, time-aware layer from a VRT made by `build_vrt` (on a task: it reads
    netCDF), opened here on the GUI thread."""
    from qgis.core import QgsRasterLayer

    layer = QgsRasterLayer(str(vrt), name, "gdal")
    if not layer.isValid():
        raise ValueError(f"Could not open {Path(vrt).name}.")
    style(layer, *value_range, diverging=diverging)
    set_time_ranges(layer, bounds)
    return layer


def raster_layer(path, name, value_range, *, diverging=False):
    """A styled layer of a derived GeoTIFF."""
    from qgis.core import QgsRasterLayer

    layer = QgsRasterLayer(str(path), name, "gdal")
    if not layer.isValid():
        raise ValueError(f"Could not open {Path(path).name}.")
    style(layer, *value_range, diverging=diverging)
    return layer


def show_step(layer, band) -> None:
    """Show one time step: a renderer band switch, colours unchanged."""
    layer.renderer().setInputBand(int(band))
    layer.triggerRepaint()


def write_geotiff(array, axes, crs_wkt, path) -> Path:
    """A derived map (north first, NaN missing) as a georeferenced float32 GeoTIFF."""
    from osgeo import gdal

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ny, nx = axes.shape
    handle = gdal.GetDriverByName("GTiff").Create(str(path), nx, ny, 1, gdal.GDT_Float32,
                                                  ["COMPRESS=DEFLATE"])
    dy = abs(float(axes.y_bnds[0, 1] - axes.y_bnds[0, 0]))
    handle.SetGeoTransform((float(axes.x_bnds.min()), axes.cell, 0, float(axes.y_bnds.max()), 0, -dy))
    handle.SetProjection(crs_wkt)
    band = handle.GetRasterBand(1)
    band.SetNoDataValue(float("nan"))
    band.WriteArray(np.asarray(array, "f4"))
    handle = None  # closes and flushes
    return path


def outline_layer(masks, axes, crs, name="Basins", threshold=0.5):
    """Outlines (no fill) of each basin's L1 cells with coverage above `threshold`."""
    from osgeo import gdal, ogr
    from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer

    from ..layers.compat import qgs_field

    crs = crs_for(axes, crs)
    layer = QgsVectorLayer(f"MultiPolygon?crs={crs.authid() or 'EPSG:4326'}", name, "memory")
    if not crs.authid():
        layer.setCrs(crs)
    layer.dataProvider().addAttributes([qgs_field("basin", "String")])
    layer.updateFields()
    ny, nx = axes.shape
    dy = abs(float(axes.y_bnds[0, 1] - axes.y_bnds[0, 0]))
    for basin, weights in masks.items():
        raster = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Byte)
        raster.SetGeoTransform((float(axes.x_bnds.min()), axes.cell, 0, float(axes.y_bnds.max()), 0, -dy))
        raster.GetRasterBand(1).WriteArray((np.asarray(weights) > threshold).astype("u1"))
        source = ogr.GetDriverByName("Memory").CreateDataSource("")
        polygons = source.CreateLayer("p")
        polygons.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
        gdal.Polygonize(raster.GetRasterBand(1), raster.GetRasterBand(1), polygons, 0)
        parts = [QgsGeometry.fromWkt(feature.GetGeometryRef().ExportToWkt()) for feature in polygons]
        if parts:
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.unaryUnion(parts))
            feature.setAttribute("basin", str(basin))
            layer.dataProvider().addFeature(feature)
    symbol = layer.renderer().symbol()
    symbol.symbolLayer(0).setBrushStyle(_no_brush())
    symbol.symbolLayer(0).setStrokeWidth(0.6)
    layer.updateExtents()
    return layer


def _no_brush():
    from qgis.PyQt.QtCore import Qt

    return Qt.BrushStyle.NoBrush


def network_layer(path, name="River network", clip=None):
    """The channel network (`2_channel_network.shp`) as a blue line overlay, cut to the
    `clip` polygons when given; None if absent."""
    from qgis.core import QgsVectorLayer
    from qgis.PyQt.QtGui import QColor

    from ..processing import run

    if not Path(path).is_file():
        return None
    layer = QgsVectorLayer(str(path), name, "ogr")
    if clip is not None:
        result = run("native:clip", {"INPUT": layer, "OVERLAY": clip, "OUTPUT": "memory:"})
        if result is not None:
            layer = result["OUTPUT"]
            layer.setName(name)
    layer.renderer().symbol().setColor(QColor("#1f78b4"))
    return layer


def gauge_layer(gauges, crs, field="value", name="Gauges"):
    """Gauge points coloured by `field` (graduated) and sized by catchment area.

    `gauges`: dicts with id, x, y (in `crs`), value and area."""
    from qgis.core import (
        QgsClassificationEqualInterval,
        QgsFeature,
        QgsGeometry,
        QgsGraduatedSymbolRenderer,
        QgsMarkerSymbol,
        QgsPointXY,
        QgsProperty,
        QgsStyle,
        QgsVectorLayer,
    )

    from ..layers.compat import qgs_field

    layer = QgsVectorLayer("Point", name, "memory")
    layer.setCrs(crs)
    layer.dataProvider().addAttributes([qgs_field("id", "Int"), qgs_field(field, "Double"),
                                        qgs_field("area", "Double")])
    layer.updateFields()
    for gauge in gauges:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(gauge["x"], gauge["y"])))
        feature.setAttributes([int(gauge["id"]), float(gauge.get("value", np.nan)), float(gauge.get("area", 0))])
        layer.dataProvider().addFeature(feature)
    layer.updateExtents()
    areas = [float(g.get("area", 0)) for g in gauges] or [0]
    symbol = QgsMarkerSymbol.createSimple({})
    symbol.setDataDefinedSize(QgsProperty.fromExpression(
        f'scale_linear("area", {min(areas)}, {max(areas) or 1}, 2.5, 7)'))
    renderer = QgsGraduatedSymbolRenderer(field)
    renderer.setSourceSymbol(symbol)  # every class copies it, size included
    renderer.setClassificationMethod(QgsClassificationEqualInterval())
    renderer.updateClasses(layer, 5)
    renderer.updateColorRamp(QgsStyle.defaultStyle().colorRamp("RdYlGn"))
    layer.setRenderer(renderer)
    return layer


def highlight_cell(canvas, axes, iy, ix, crs):
    """Outline one L1 cell on `canvas`; returns the rubber band (remove with `reset`)."""
    from qgis.core import Qgis, QgsGeometry, QgsRectangle
    from qgis.gui import QgsRubberBand
    from qgis.PyQt.QtGui import QColor

    band = QgsRubberBand(canvas, Qgis.GeometryType.Polygon)
    band.setColor(QColor(255, 255, 255, 0))
    band.setStrokeColor(QColor("#e31a1c"))
    band.setWidth(2)
    band.setToGeometry(QgsGeometry.fromRect(QgsRectangle(axes.x_bnds[ix].min(), axes.y_bnds[iy].min(),
                                                         axes.x_bnds[ix].max(), axes.y_bnds[iy].max())),
                       crs_for(axes, crs))
    return band


def flash_gauge(canvas, x, y, crs) -> None:
    from qgis.core import QgsGeometry, QgsPointXY

    canvas.flashGeometries([QgsGeometry.fromPointXY(QgsPointXY(x, y))], crs)


def link_extents(canvases) -> None:
    """Keep the canvases on one centre and scale (Compare), also when a splitter resizes
    them; a guard stops the echo."""
    from qgis.PyQt.QtCore import QEvent, QObject, QTimer

    class KeepScale(QObject):
        def eventFilter(self, canvas, event):
            if event.type() == QEvent.Type.Resize:  # a scale-locked canvas keeps its scale on resize
                canvas.setScaleLocked(True)
                QTimer.singleShot(0, lambda: canvas.setScaleLocked(False))
            return False

    busy = []

    def follow(source):
        if busy or source.scaleLocked():  # resizing: each canvas keeps its own centre and scale
            return
        busy.append(True)
        try:
            for canvas in canvases:
                if canvas is not source:
                    canvas.setExtent(source.extent())
                    canvas.zoomScale(source.scale(), True)
                    canvas.refresh()
        finally:
            busy.clear()

    for canvas in canvases:
        canvas.installEventFilter(KeepScale(canvas))
        canvas.extentsChanged.connect(lambda canvas=canvas: follow(canvas))


def open_in_qgis(layer, name=None, group_name=GROUP):
    """Copy a layer, with its style, into the QGIS project under a group ("mHM post")."""
    from qgis.core import QgsMapLayerStyle, QgsProject

    clone = layer.clone()
    clone.setName(name or layer.name())
    style = QgsMapLayerStyle()  # clone() drops the fixed classification; the style keeps it
    style.readFromLayer(layer)
    style.writeToLayer(clone)
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    group = root.findGroup(group_name) or root.insertGroup(0, group_name)
    project.addMapLayer(clone, False)
    group.addLayer(clone)
    return clone
