"""A DEM derivative replaced by a raster on the same grid that covers the whole DEM."""
import numpy as np
import pytest

gdal = pytest.importorskip("osgeo.gdal")
from osgeo import osr  # noqa: E402 -- after the importorskip

from mhm_qgis.core.handlers.file.netcdf.raster import copy_rasters, source  # noqa: E402
from mhm_qgis.core.handlers.raster.tasks import override_derivative  # noqa: E402


def _tif(path, values, cellsize=100.0, origin=(500000.0, 5000000.0)):
    rows, cols = values.shape
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), cols, rows, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((origin[0], cellsize, 0, origin[1], 0, -cellsize))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(32632)
    dataset.SetProjection(crs.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(values)
    dataset = None
    return path


def test_a_raster_on_the_dem_grid_replaces_the_derivative(tmp_path):
    copy_rasters(tmp_path / "dem_derivatives.nc", {"slope": _tif(tmp_path / "zero.tif", np.zeros((4, 5)))})
    values = np.arange(20, dtype="float32").reshape(4, 5)
    override_derivative(tmp_path, "slope", _tif(tmp_path / "slope.tif", values))
    written = gdal.Open(source(tmp_path / "dem_derivatives.nc", "slope")).ReadAsArray()
    assert np.array_equal(written, values)  # north first, as given
    with pytest.raises(ValueError, match="resolution"):
        override_derivative(tmp_path, "slope", _tif(tmp_path / "fine.tif", np.zeros((8, 10)), cellsize=50.0))
    with pytest.raises(ValueError, match="cover"):
        override_derivative(tmp_path, "slope", _tif(tmp_path / "small.tif", np.zeros((3, 5))))
