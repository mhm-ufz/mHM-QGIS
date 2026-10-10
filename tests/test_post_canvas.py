"""Post P4: the map layer (VRT placement, band-switch time, colours, overlays)."""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from osgeo import gdal
from post_fixtures import make_run
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QSize

from mhm_qgis.core.post import catalog, grid
from mhm_qgis.qgis_bridge.display import post_canvas

RAPTI = Path("/home/bashyal/Works/Rapti/mhm-plugin/output/mHM_Fluxes_States.nc")
UTM = QgsCoordinateReferenceSystem("EPSG:32644")


def _values(layer, band=None):
    """The layer's pixels as QGIS reads them for its extent, north first."""
    provider, extent = layer.dataProvider(), layer.extent()
    block = provider.block(band or layer.renderer().inputBand(), extent, layer.width(), layer.height())
    return np.array([[np.nan if block.isNoData(r, c) else block.value(r, c) for c in range(layer.width())]
                     for r in range(layer.height())])


def _layer(tmp_path, geographic=False, path=None):
    path = path or make_run(tmp_path, geographic=geographic, days=10).files["mhm"]
    axes = grid.axes(path)
    bounds = catalog.read_bounds(path)
    vrt = post_canvas.build_vrt(tmp_path / "cache", path, "aET", axes, post_canvas.crs_for(axes, UTM).toWkt())
    return post_canvas.variable_layer(vrt, "aET", bounds, (0.0, 5.0)), axes, path


def _stored(path, step):
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        return np.ma.filled(np.ma.asarray(handle["aET"][step], "f8"), np.nan)


@pytest.mark.parametrize("geographic", [False, True])
def test_vrt_places_every_step(tmp_path, geographic):
    layer, axes, path = _layer(tmp_path, geographic)
    extent = layer.extent()
    expected = [axes.x_bnds.min(), axes.y_bnds.min(), axes.x_bnds.max(), axes.y_bnds.max()]
    np.testing.assert_allclose([extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
                               expected)
    assert layer.crs().authid() == ("EPSG:4326" if geographic else "EPSG:32644")
    assert layer.bandCount() == 10
    np.testing.assert_allclose(_values(layer, 4), _stored(path, 3), equal_nan=True)
    ranges = layer.temporalProperties().fixedRangePerBand()
    assert ranges[1].begin().toString("yyyy-MM-dd") == "2000-01-01"
    assert ranges[10].end().toString("yyyy-MM-dd") == "2000-01-11"


@pytest.mark.skipif(not RAPTI.is_file(), reason="Rapti run not present")
def test_a_file_gdal_serves_south_first_is_placed_north_up(tmp_path):
    assert post_canvas._served_flipped(RAPTI, "aET")  # F16
    layer, _, _ = _layer(tmp_path, path=RAPTI)
    np.testing.assert_allclose(_values(layer, 1), _stored(RAPTI, 0), equal_nan=True)


def test_band_switch_renders_with_fixed_colours(tmp_path):
    layer, _, _ = _layer(tmp_path)
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(layer.crs())
    settings.setExtent(layer.extent())
    settings.setOutputSize(QSize(60, 40))

    def render():
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        return job.renderedImage()

    first = render()
    post_canvas.show_step(layer, 7)
    assert layer.renderer().inputBand() == 7
    assert (layer.renderer().classificationMin(), layer.renderer().classificationMax()) == (0.0, 5.0)
    assert render() != first


def test_diverging_style_is_centred_on_zero(tmp_path):
    layer, _, _ = _layer(tmp_path)
    post_canvas.style(layer, -1.0, 3.0, diverging=True)
    assert (layer.renderer().classificationMin(), layer.renderer().classificationMax()) == (-3.0, 3.0)


def test_derived_geotiff_round_trips(tmp_path):
    path = make_run(tmp_path, days=5).files["mhm"]
    axes = grid.axes(path)
    array = np.arange(24, dtype="f4").reshape(4, 6)
    array[0, 0] = np.nan
    tif = post_canvas.write_geotiff(array, axes, UTM.toWkt(), tmp_path / "derived" / "sum.tif")
    handle = gdal.Open(str(tif))
    assert handle.GetGeoTransform() == (500000.0, 2000.0, 0.0, 3000000.0, 0.0, -2000.0)
    np.testing.assert_array_equal(handle.ReadAsArray(), array)
    assert grid.cell_index(axes, 501000.0, 2999000.0) == (0, 0)
    assert grid.cell_index(axes, 511999.0, 2992001.0) == (3, 5)
    assert grid.cell_index(axes, 499999.0, 2999000.0) is None


def test_overlays_and_project_handoff(tmp_path):
    truth = make_run(tmp_path, days=5)
    axes = grid.axes(truth.files["mhm"])
    mask = truth.mask.astype(float)
    outlines = post_canvas.outline_layer({"domain": mask}, axes, UTM)
    assert outlines.featureCount() == 1
    extent = outlines.extent()
    assert (extent.xMinimum(), extent.yMaximum()) == (500000.0, 3000000.0)
    gauges = post_canvas.gauge_layer([{"id": 1, "x": 501000, "y": 2999000, "value": 0.7, "area": 10},
                                      {"id": 2, "x": 505000, "y": 2995000, "value": -0.2, "area": 40}], UTM, "NSE")
    assert gauges.featureCount() == 2
    assert len(gauges.renderer().ranges()) == 5
    assert post_canvas.network_layer(tmp_path / "missing.shp") is None
    layer, _, _ = _layer(tmp_path / "v")
    clone = post_canvas.open_in_qgis(layer, "aET (run)")
    group = QgsProject.instance().layerTreeRoot().findGroup(post_canvas.GROUP)
    assert group.findLayer(clone.id()) is not None
    assert clone.renderer().classificationMax() == 5.0
    QgsProject.instance().removeMapLayer(clone.id())


def test_canvases_follow_each_other(tmp_path):
    canvases = [QgsMapCanvas() for _ in range(3)]
    for canvas in canvases:
        canvas.setDestinationCrs(UTM)
        canvas.resize(200, 100)
    post_canvas.link_extents(canvases)
    target = QgsRectangle(500000, 2990000, 520000, 3000000)
    canvases[1].setExtent(target)
    for canvas in canvases:
        assert canvas.extent().center().x() == pytest.approx(target.center().x())
    truth = make_run(tmp_path, days=5)
    band = post_canvas.highlight_cell(canvases[0], grid.axes(truth.files["mhm"]), 1, 2, UTM)
    assert band.asGeometry().boundingBox().xMinimum() == 504000
    for canvas in canvases:
        canvas.deleteLater()
