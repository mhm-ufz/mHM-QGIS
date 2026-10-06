"""The v6 morphology bundle must match the mHM example's input.nc layout."""
from __future__ import annotations

import os

import numpy as np
import pytest

pytest.importorskip("osgeo", reason="GDAL Python bindings come with QGIS or the system")
from osgeo import gdal, osr

from mhm_qgis.core.handlers.file.netcdf.raster import copy_rasters, source

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.morphology.layers.mask import write_morph_mask
from mhm_qgis.core.morphology.layers.morph_input_nc import (
    MORPH_VARIABLES,
    write_morph_input_nc,
)

xr = pytest.importorskip("xarray")

L0 = {
    "ncols": 4, "nrows": 3, "xllcorner": 100.0, "yllcorner": 200.0,
    "cellsize": 10.0, "nodata_value": -9999.0,
}


def _raster(path, values, nodata=-9999, epsg=32632):
    rows, columns = values.shape
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(path), columns, rows, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((100.0, 10.0, 0, 230.0, 0, -10.0))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(epsg)
    dataset.SetProjection(crs.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(values.astype("float32"))
    dataset.GetRasterBand(1).SetNoDataValue(nodata)
    dataset = None
    return path


def _masked_set(folder, names=None):
    """Stage a bundle and return the name -> source map `write` would build."""
    folder.mkdir(parents=True, exist_ok=True)
    written = {}
    base = np.arange(12, dtype="float64").reshape(3, 4)
    for index, (name, stem, _dtype, _attrs) in enumerate(MORPH_VARIABLES):
        if names is not None and name not in names:
            continue
        written[name] = _raster(folder / f"{stem}_masked.tif", base + index * 100)
    bundle = folder / "dem_derivatives_masked.nc"
    derivatives = {name: path for name, path in written.items() if name not in {"soil_class", "geology_class"}}
    if derivatives:
        copy_rasters(bundle, derivatives)
    return {name: (source(bundle, name) if name in derivatives else path)
            for name, path in written.items()}


def test_input_nc_has_the_example_structure(tmp_path):
    layers = _masked_set(tmp_path)
    output = write_morph_input_nc(
        tmp_path / "input.nc", layers, L0,
        crs_string="EPSG:32632", title="Static morphology inputs for test",
    )

    with xr.open_dataset(output, mask_and_scale=False) as result:
        # LAI_class joins the common bundle when a class input was prepared.
        morphology = [
            name for name in result.data_vars
            if not name.endswith("_bnds")
        ]
        assert sorted(morphology) == [
            "LAI_class", "aspect", "dem", "facc", "fdir",
            "geology_class", "slope", "soil_class",
        ]
        # The `coordinates` attribute promotes lon/lat, matching the example.
        assert sorted(result.coords) == ["lat", "lon", "x", "y"]
        assert result["x"].attrs["bounds"] == "x_bnds"
        assert result["dem"].dims == ("y", "x")
        assert result["dem"].shape == (3, 4)
        assert result.attrs["Conventions"] == "CF-1.8"
        assert result.attrs["title"] == "Static morphology inputs for test"
        # Axes carry the projected coordinates and their bounds.
        np.testing.assert_allclose(result["x"].values, [105.0, 115.0, 125.0, 135.0])
        np.testing.assert_allclose(result["y"].values, [225.0, 215.0, 205.0])
        np.testing.assert_allclose(result["x_bnds"].values[0], [100.0, 110.0])
        np.testing.assert_allclose(result["y_bnds"].values[0], [230.0, 220.0])
        assert result["dem"].attrs["units"] == "m"
        assert result["fdir"].dtype == np.int32
        assert result["dem"].dtype == np.float64

    # xarray folds `coordinates` into encoding on read, so check the raw file:
    # mHM reads the attribute itself.
    from netCDF4 import Dataset

    with Dataset(output) as raw:
        assert raw.variables["dem"].coordinates == "lat lon"
        assert raw.variables["dem"].missing_value == -9999
        assert raw.variables["fdir"].getncattr("_FillValue") == -9999


def test_values_are_copied_verbatim_from_the_masked_rasters(tmp_path):
    layers = _masked_set(tmp_path)
    output = write_morph_input_nc(
        tmp_path / "input.nc", layers, L0,
        crs_string="EPSG:32632")

    base = np.arange(12, dtype="float64").reshape(3, 4)
    with xr.open_dataset(output, mask_and_scale=False) as result:
        for index, (name, _stem, _dtype, _attrs) in enumerate(MORPH_VARIABLES):
            np.testing.assert_array_equal(
                result[name].values, (base + index * 100).astype(
                    result[name].dtype))


def test_a_geographic_project_writes_lon_lat_without_transforming(tmp_path):
    geographic = {
        "ncols": 2, "nrows": 2, "xllcorner": 79.0, "yllcorner": 30.0,
        "cellsize": 0.1,
    }
    folder = tmp_path / "geo"
    folder.mkdir()
    path = folder / "source.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), 2, 2, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((79.0, 0.1, 0, 30.2, 0, -0.1))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(4326)
    dataset.SetProjection(crs.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(np.ones((2, 2), dtype="float32"))
    dataset = None

    copy_rasters(folder / "dem_derivatives_masked.nc", {"dem": path})
    output = write_morph_input_nc(
        tmp_path / "input.nc", {"dem": source(folder / "dem_derivatives_masked.nc", "dem")}, geographic,
        crs_string="EPSG:4326")

    with xr.open_dataset(output) as result:
        # lon/lat equal the axes themselves when the project is already lon/lat.
        np.testing.assert_allclose(result["lon"].values[0], result["x"].values)
        np.testing.assert_allclose(result["lat"].values[:, 0], result["y"].values)


def test_missing_layers_are_simply_omitted(tmp_path):
    layers = _masked_set(tmp_path, names={"dem", "facc"})
    output = write_morph_input_nc(
        tmp_path / "input.nc", layers, L0,
        crs_string="EPSG:32632")

    with xr.open_dataset(output) as result:
        assert "dem" in result
        assert "facc" in result
        assert "slope" not in result
        assert "soil_class" not in result


def test_morph_mask_uses_selected_dem_cells_and_l0_coordinates(tmp_path):
    values = np.arange(12, dtype="float32").reshape(3, 4)
    values[1, 2] = -9999
    raster = _raster(tmp_path / "dem.tif", values)
    derivatives = copy_rasters(
        tmp_path / "dem_derivatives_masked.nc", {"dem": raster})
    lat = np.arange(12, dtype="float64").reshape(3, 4) + 40
    lon = np.arange(12, dtype="float64").reshape(3, 4) + 5
    from netCDF4 import Dataset
    with Dataset(tmp_path / "latlon.nc", "w") as dataset:
        dataset.createDimension("y", 3)
        dataset.createDimension("x", 4)
        dataset.createVariable("lat_l0", "f8", ("y", "x"))[:] = lat
        dataset.createVariable("lon_l0", "f8", ("y", "x"))[:] = lon

    output = write_morph_mask(tmp_path / "morph_mask.nc", derivatives,
                              tmp_path / "latlon.nc")

    with xr.open_dataset(output) as result:
        expected = np.ones((3, 4), dtype="int8")
        expected[1, 2] = 0
        np.testing.assert_array_equal(result["mask"].values, expected)
        np.testing.assert_array_equal(result["lat"].values, lat)
        np.testing.assert_array_equal(result["lon"].values, lon)


def test_writing_without_any_layer_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="No morphology rasters"):
        write_morph_input_nc(tmp_path / "input.nc", {}, L0)


def test_a_failed_write_leaves_no_partial_file(tmp_path):
    layers = _masked_set(tmp_path, names={"dem"})
    layers["slope"] = tmp_path / "absent.tif"

    with pytest.raises(RuntimeError, match="Could not open the morphology raster"):
        write_morph_input_nc(tmp_path / "input.nc", layers, L0,
                             crs_string="EPSG:32632")
    assert not (tmp_path / "input.nc").exists()
    assert not list(tmp_path.glob(".*.tmp"))
