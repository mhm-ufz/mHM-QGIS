"""Sampling a masked raster onto the L0 header must not invent nodata.

`align_dataset_to_header` used `xarray.interp`, which has no extrapolation
domain. The source y-centres come from the GDAL geotransform top-down while the
target ones are built bottom-up, so the two disagree by about 3.6e-15 in
float64 -- enough to push the bottom row outside the domain and turn the whole
row into nodata in every written layer.
"""
from __future__ import annotations

import os

import numpy as np
import pytest
from osgeo import gdal, osr

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.handlers.file.ascii.morphology import (  # noqa: E402
    _read_raster,
    align_dataset_to_header,
    file_matches_header,
)
from mhm_qgis.core.grid import header_center_coordinates  # noqa: E402

xr = pytest.importorskip("xarray")


# The geographic grid from the project that exposed this: the target y vector
# lands ~3.6e-15 below the source one, so only the bottom row falls out.
CELLSIZE = 0.0025
XLL = 78.99958333333333
YLL = 25.997916666666665
HEADER = {
    "ncols": 4, "nrows": 4, "xllcorner": XLL, "yllcorner": YLL,
    "cellsize": CELLSIZE, "nodata_value": -9999.0, "unit": "deg",
}


def _raster(path, values, *, header=None, cellsize=None, xll=None, yll=None,
            dtype=gdal.GDT_Float32):
    """Write a GeoTIFF on the header grid, as the crop and mask steps do.

    The coordinates must come back through rioxarray rather than being built by
    hand: it derives cell centres as ``affine * affine.translation(0.5, 0.5)``,
    which is not bit-identical to the obvious formula, and that difference is
    the whole point of this module.
    """
    header = HEADER if header is None else header
    values = np.asarray(values)
    rows, cols = values.shape
    cellsize = float(header["cellsize"]) if cellsize is None else cellsize
    xll = float(header["xllcorner"]) if xll is None else xll
    yll = float(header["yllcorner"]) if yll is None else yll
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), cols, rows, 1, dtype)
    dataset.SetGeoTransform((xll, cellsize, 0.0, yll + rows * cellsize, 0.0, -cellsize))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(4326)
    dataset.SetProjection(crs.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(values)
    dataset.GetRasterBand(1).SetNoDataValue(-9999)
    dataset = None
    return path


def _aligned(path, *, header=None, integer=False):
    dataset = _read_raster(path, None)
    try:
        result = align_dataset_to_header(
            dataset, HEADER if header is None else header, integer=integer
        )
    finally:
        dataset.close()
    name = list(result.data_vars)[0]
    return result[name]


def test_the_bottom_row_is_not_turned_into_nodata(tmp_path):
    """The regression: one ulp of coordinate drift used to cost a whole row."""
    values = np.arange(1, 17, dtype="float32").reshape(4, 4)
    aligned = _aligned(_raster(tmp_path / "geology.tif", values))

    assert int((aligned.values == -9999).sum()) == 0
    np.testing.assert_array_equal(aligned.values, values)


def test_an_on_grid_source_is_reproduced_cell_for_cell(tmp_path):
    """Every real input is already on the L0 grid, so nothing may move."""
    values = np.arange(1, 17, dtype="int16").reshape(4, 4)
    integer_out = _aligned(_raster(tmp_path / "classes.tif", values,
                                   dtype=gdal.GDT_Int16), integer=True)
    np.testing.assert_array_equal(integer_out.values, values)
    assert integer_out.dtype == np.dtype("int32")

    floats = (np.arange(16, dtype="float32").reshape(4, 4) + 0.5)
    float_out = _aligned(_raster(tmp_path / "slope.tif", floats))
    np.testing.assert_array_equal(float_out.values, floats)
    assert float_out.dtype.kind == "f"


def test_class_values_are_never_blended(tmp_path):
    """Nearest sampling only: a mean of classes 1 and 9 is not a class."""
    values = np.array([[1, 9, 1, 9]] * 4, dtype="int16")
    aligned = _aligned(
        _raster(tmp_path / "lc.tif", values, dtype=gdal.GDT_Int16), integer=True
    )

    assert set(np.unique(aligned.values).tolist()) <= {1, 9}


def test_the_raster_is_not_flipped(tmp_path):
    """North stays north: the target y vector is descending, the source is not."""
    values = np.arange(16, dtype="float32").reshape(4, 4)
    aligned = _aligned(_raster(tmp_path / "dem.tif", values))

    assert aligned.values[0, 0] == 0
    assert aligned.values[-1, -1] == 15


def test_the_exact_header_coordinates_are_restored(tmp_path):
    """Nearest sampling returns the source labels; the header ones must win."""
    aligned = _aligned(
        _raster(tmp_path / "dem.tif", np.ones((4, 4), dtype="float32"))
    )
    target_x, target_y = header_center_coordinates(HEADER)

    np.testing.assert_array_equal(aligned["y"].values, target_y)
    np.testing.assert_array_equal(aligned["x"].values, target_x)
    assert np.all(np.diff(aligned["y"].values) < 0)


def test_cf_axis_attributes_identify_xc_yc_coordinates(tmp_path):
    """mHM-tools does not list the xc/yc names written by its LAI formatter."""
    target_x, target_y = header_center_coordinates(HEADER)
    dataset = xr.Dataset(
        {"lai": (("time", "yc", "xc"), np.ones((1, 4, 4)))},
        coords={
            "time": [1],
            "yc": ("yc", target_y, {"axis": "Y"}),
            "xc": ("xc", target_x, {"axis": "X"}),
        },
    )
    path = tmp_path / "lai.nc"
    dataset.to_netcdf(path)

    assert file_matches_header(path, HEADER)


def test_a_coarser_source_is_upsampled_instead_of_framed_in_nodata(tmp_path):
    """interp() ringed a coarser source with nodata; nearest sampling does not."""
    coarse = _raster(
        tmp_path / "coarse.tif",
        np.array([[1, 2], [3, 4]], dtype="float32"),
        cellsize=CELLSIZE * 2,
    )

    aligned = _aligned(coarse)

    assert int((aligned.values == -9999).sum()) == 0
    np.testing.assert_array_equal(
        aligned.values,
        [[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 4, 4], [3, 3, 4, 4]],
    )


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("shifted", {"yll": YLL + CELLSIZE / 2}),
        ("short", {"rows": 3}),
    ],
)
def test_a_source_that_does_not_cover_the_header_is_still_rejected(
    tmp_path, name, kwargs
):
    """Snapping to the nearest label must not paper over a real mismatch."""
    rows = kwargs.pop("rows", 4)
    path = _raster(
        tmp_path / f"{name}.tif",
        np.ones((rows, 4), dtype="float32"),
        **kwargs,
    )

    with pytest.raises(ValueError, match="does not cover the target L0 extent"):
        _aligned(path)
