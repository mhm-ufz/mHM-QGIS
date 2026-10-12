"""Real NetCDF I/O from DEM preparation through the final domain exports."""
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

pytest.importorskip("osgeo", reason="GDAL Python bindings come with QGIS or the system")
from osgeo import gdal, osr

from mhm_qgis.core.executions.morphology import commands, setup
from mhm_qgis.core.handlers.file.json import write as write_json
from mhm_qgis.core.handlers.file.netcdf.raster import copy_rasters, source, variables
from mhm_qgis.core.handlers.raster.tasks import dem_derivative_files
from mhm_qgis.core.handlers.state import domain_state, processing
from mhm_qgis.core.handlers.store.layout import (
    domain_dem_path,
    ensure_project_structure,
    input_state_path,
    morph_folder,
)
from mhm_qgis.core.handlers.store.paths import (
    data_folder,
    morph_staging_folder,
    temp_morphology_folder,
)
from mhm_qgis.externals import mhm_tools_handler


def _raster(path, values=None, *, epsg=32632, cell=100, origin=(0, 1000)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if values is None:
        values = np.add.outer(np.arange(10, 0, -1), np.arange(10, 0, -1)).astype("float64")
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float64)
    dataset.SetGeoTransform((origin[0], cell, 0, origin[1], 0, -cell))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(epsg)
    dataset.SetProjection(crs.ExportToWkt())
    dataset.GetRasterBand(1).SetNoDataValue(-9999)
    dataset.GetRasterBand(1).WriteArray(values)
    dataset = None
    return path


@pytest.mark.parametrize(("epsg", "cell", "origin"), [(32632, 100, (0, 1000)), (4326, 1 / 1200, (80, 30))])
def test_single_variable_filled_dem_is_readable_without_selection(tmp_path, epsg, cell, origin):
    import rasterio

    original = _raster(tmp_path / "source.tif", epsg=epsg, cell=cell, origin=origin)
    result = dem_derivative_files(original, tmp_path / "geometry")
    assert set(variables(result["filled_dem"])) == {"dem", "x", "y", "crs"}
    with rasterio.open(original) as expected, rasterio.open(result["filled_dem"]) as actual:
        assert actual.count == 1
        assert actual.crs == expected.crs
        assert actual.nodata == expected.nodata
        assert tuple(actual.transform) == pytest.approx(tuple(expected.transform), abs=1e-12)
        np.testing.assert_array_equal(actual.read(1), expected.read(1))
    # Exercise the actual external reader, not just a plugin mock.
    output = mhm_tools_handler.create_dem_derivative_files(result["filled_dem"], tmp_path / "second")
    assert "dem_filled" in variables(output)


def test_selected_netcdf_dem_variable_is_materialized_before_derivation(tmp_path):
    low = _raster(tmp_path / "low.tif")
    chosen = _raster(
        tmp_path / "chosen.tif",
        np.add.outer(np.arange(10, 0, -1), np.arange(10, 0, -1)) + 100,
    )
    multi = copy_rasters(
        tmp_path / "multi.nc", {"low": low, "chosen": chosen}
    )

    result = dem_derivative_files(
        source(multi, "chosen"),
        tmp_path / "geometry",
        source_crs="EPSG:32632",
        target_crs="EPSG:32632",
    )

    assert gdal.Open(result["filled_dem"]).ReadAsArray().min() > 100


def test_missing_filled_file_reuses_bundle_but_changed_pixels_recompute(tmp_path):
    original = _raster(tmp_path / "source.tif")
    folder = tmp_path / "geometry"
    result = dem_derivative_files(original, folder)
    bundle = Path(result["dem_derivatives"])
    stamp = bundle.stat().st_mtime_ns
    Path(result["filled_dem"]).unlink()

    def unexpected(*args):
        pytest.fail("The derivative bundle should have been reused")

    dem_derivative_files(original, folder, compute=unexpected)
    assert bundle.stat().st_mtime_ns == stamp
    raster = gdal.Open(str(original), gdal.GA_Update)
    values = raster.ReadAsArray() + 100
    raster.GetRasterBand(1).WriteArray(values)
    raster = None
    result = dem_derivative_files(original, folder)
    assert not result["reused"]
    np.testing.assert_array_equal(gdal.Open(result["filled_dem"]).ReadAsArray(), values)


def test_exact_copy_rejects_misalignment_and_cancel_preserves_previous_file(tmp_path):
    original = _raster(tmp_path / "source.tif")
    output = copy_rasters(tmp_path / "dem.nc", {"dem": original})
    before = output.read_bytes()
    header = {"ncols": 10, "nrows": 10, "xllcorner": 50, "yllcorner": 0, "cellsize": 100}
    with pytest.raises(ValueError, match="aligned"):
        copy_rasters(output, {"dem": original}, header)

    class Cancelled:
        def isCanceled(self):
            return True

    with pytest.raises(RuntimeError, match="cancelled"):
        copy_rasters(output, {"dem": original}, task=Cancelled())
    assert output.read_bytes() == before
    assert not list(tmp_path.glob(".*.tmp"))


def test_native_command_produces_netcdf_in_a_subprocess(tmp_path):
    original = _raster(tmp_path / "source.tif")
    result = commands.dem_derivatives(None, {"source": str(original), "output_folder": str(tmp_path / "geometry")})
    assert Path(result["filled_dem"]).is_file()
    assert not list((tmp_path / "geometry").glob("*.tif"))


def test_hydrology_reuses_saved_direction_without_recomputing_dem(tmp_path, monkeypatch):
    import pyflwdir

    from mhm_qgis.core.handlers.raster.tasks import _flow_context

    original = _raster(tmp_path / "source.tif")
    result = dem_derivative_files(original, tmp_path / "geometry")

    def unexpected(*args, **kwargs):
        pytest.fail("Hydrology must reuse the saved directions")

    monkeypatch.setattr(pyflwdir, "from_dem", unexpected)
    context = _flow_context(result["filled_dem"])
    np.testing.assert_array_equal(context["flwdir"].to_array(), gdal.Open(result["flow_direction"]).ReadAsArray())



@pytest.mark.parametrize(("version", "category_format"), [("5.13", "nc"), ("6", "nc")])
@pytest.mark.parametrize("catchments", [False, True])
def test_execute_all_publishes_one_domain_on_prescanned_grid(tmp_path, version, category_format, catchments):  # noqa: PLR0915
    from mhm_qgis.core.handlers.state import nml_settings

    ensure_project_structure(tmp_path, version)
    write_json(input_state_path(tmp_path), {"mhm_version": version})
    folder = Path(temp_morphology_folder(tmp_path))
    header = {"ncols": 12, "nrows": 12, "xllcorner": -100, "yllcorner": -100,
              "cellsize": 100, "nodata_value": -9999}
    original = _raster(tmp_path / "source.tif")
    result = dem_derivative_files(original, folder, header=header)
    bundle_bytes = Path(result["dem_derivatives"]).read_bytes()
    native = gdal.Open(str(original)).ReadAsArray()
    filled = gdal.Open(result["filled_dem"]).ReadAsArray()
    assert filled.shape == (12, 12)
    np.testing.assert_array_equal(filled[1:11, 1:11], native)
    assert np.all(filled[0] == -9999)
    keep = filled != -9999
    if catchments:
        keep = np.zeros((12, 12), dtype=bool)
        state = {"outlets": {}}
        for index, columns in enumerate(((2, 4), (7, 9)), 1):
            cells = np.zeros((12, 12))
            cells[2:10, columns[0]:columns[1]] = index
            keep |= cells != 0
            path = _raster(folder / f"catchment{index}.tif", cells, origin=(-100, 1100))
            picked = {"x": 50, "y": 50, "crs": "EPSG:32632"}
            state["outlets"][str(index)] = {"is_domain": True, "picked": picked, "confidence": 2,
                "delineation": {"raster_path": str(path), "mask_value": index, "cell_center": [50, 50],
                    "catchment_area_m2": 160000, "picked": picked,
                    "fingerprint": domain_state.delineation_fingerprint(result["filled_dem"], 50, 50)}}
        domain_state.save_state(tmp_path, state)

    watershed = _raster(
        folder / "Watersheds" / "4_watershed_merged_mask.tif",
        keep.astype("int32"),
        origin=(-100, 1100),
    )
    state = domain_state.load_state(tmp_path)
    state["merged_mask_path"] = str(watershed)
    domain_state.save_state(tmp_path, state)

    gauges = np.full((12, 12), -9999.0)
    gauges[5, 3] = 280
    copy_rasters(folder / "idgauges.nc", {"idgauges": _raster(tmp_path / "gauges.tif", gauges, origin=(-100, 1100))})
    staging = Path(morph_staging_folder(tmp_path))
    for name in ("lc", "soil_class", "geology_class"):
        # Early common-grid formatting can leave nodata outside the original DEM.
        values = np.where(filled != -9999, 2, -9999)
        path = _raster(staging / f"{name}.tif", values, origin=(-100, 1100))
        if category_format == "nc":
            tif = path
            path = copy_rasters(path.with_suffix(".nc"), {"category": path})
            tif.unlink()
            # A previous format must not override the newly configured NetCDF.
            old = Path(morph_folder(tmp_path)) / f"{name}.asc"
            from mhm_qgis.core.handlers.raster.tasks import write_domain_dem_ascii
            write_domain_dem_ascii(result["filled_dem"], old, header, None)
        if name != "lc":
            (staging / f"{name}definition.txt").write_text("class definition")
        section = {"lc": "land_cover", "soil_class": "soil", "geology_class": "geology"}[name]
        nml_settings.update_section(tmp_path, section, {
            "output_path": nml_settings.relative_workspace_path(tmp_path, path)})
    request = setup.SetupRequest(str(tmp_path), dict.fromkeys(("L0", "L1", "L11", "L2"), header),
                                 crs="EPSG:32632")
    latlon = Path(data_folder(tmp_path)) / "latlon.nc"
    latlon.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(latlon, "w") as coordinates:
        coordinates.createDimension("y", 12)
        coordinates.createDimension("x", 12)
        coordinates.createVariable("lat_l0", "f8", ("y", "x"))[:] = 0
        coordinates.createVariable("lon_l0", "f8", ("y", "x"))[:] = 0
    setup.mask(request)
    target = Path(domain_dem_path(tmp_path, "ignored"))
    target.parent.mkdir(parents=True, exist_ok=True)
    old = tmp_path / "old-dem"
    old.write_bytes(b"preserve old symlink target")
    target.symlink_to(old)
    assert setup.run(request)
    assert old.read_bytes() == b"preserve old symlink target"
    assert not target.is_symlink()
    assert Path(result["dem_derivatives"]).read_bytes() == bundle_bytes
    assert not (folder / "dem_derivatives_crop.nc").exists()
    values = gdal.Open(str(target)).ReadAsArray()
    np.testing.assert_array_equal(values[keep], filled[keep])
    if version == "5.13":
        assert np.all(values[~keep] == -9999)
    else:
        np.testing.assert_array_equal(values, filled)
    master = Path(morph_folder(tmp_path))
    if version == "5.13":
        assert gdal.Open(str(master / "idgauges.asc")).ReadAsArray()[5, 3] == 280
        classes = gdal.Open(str(master / "soil_class.asc")).ReadAsArray()
    else:
        assert "dem" in variables(master / "input.nc")
        assert "idgauges" not in variables(master / "input.nc")
        assert gdal.Open(str(master / "idgauges.asc")).ReadAsArray()[5, 3] == 280
        classes = gdal.Open(source(master / "input.nc", "soil_class")).ReadAsArray()
    # mHM takes fdir's cells as its grid: the published fdir is masked, input.nc keeps it whole.
    full_fdir = gdal.Open(source(result["dem_derivatives"], "fdir")).ReadAsArray()
    if version == "5.13":
        fdir = gdal.Open(str(master / "fdir.asc")).ReadAsArray()
    else:
        fdir = gdal.Open(source(master / "fdir.nc", "fdir")).ReadAsArray()
        np.testing.assert_array_equal(gdal.Open(source(master / "input.nc", "fdir")).ReadAsArray(), full_fdir)
        registered = nml_settings.load_settings(tmp_path)["namelist_values"]["main"]["config_input"]
        assert registered["fdir_path"][0].endswith("fdir.nc")
        assert registered["fdir_var"] == ["fdir"]
    np.testing.assert_array_equal(fdir[keep], full_fdir[keep])
    assert np.all(fdir[~keep] == -9999)
    assert classes[0, 0] == -9999
    assert classes[5, 5] == 2  # Full class coverage, even outside selected catchments.
    expected_suffix = "soil_class.asc" if version == "5.13" else "soil_class.nc"
    assert nml_settings.load_settings(tmp_path)["soil"]["output_path"].endswith(expected_suffix)
    domains = nml_settings.load_settings(tmp_path)["domains"]
    assert len(domains) == 1
    assert domains[0]["name"] == "master"
    assert len(processing.section(tmp_path, "domains", [])) == 1
    assert set(processing.section(tmp_path, "workflows", {})).isdisjoint(
        {"execute_all_link_domains", "execute_all_crop", "execute_all_domain_dems"})
