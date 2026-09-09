"""Real NetCDF I/O from DEM preparation through the final domain exports."""
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset
from osgeo import gdal, osr

from mhm_qgis.applications import mhm_tools_handler
from mhm_qgis.core.executions.morphology import commands, setup
from mhm_qgis.core.grid import header_from_geotransform
from mhm_qgis.core.handlers.file.json import write as write_json
from mhm_qgis.core.handlers.file.netcdf.raster import copy_rasters, source, variables
from mhm_qgis.core.handlers.raster.tasks import dem_derivative_files
from mhm_qgis.core.handlers.state import domain_state, processing
from mhm_qgis.core.handlers.store.layout import (
    MERGED_MASK_NAME, domain_dem_path, ensure_project_structure, input_state_path, morph_folder,
)
from mhm_qgis.core.handlers.store.paths import geometry_folder, morph_staging_folder
from mhm_qgis.core.morphology.layers.domain_dem import DOMAIN_INPUT_GROUPS


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


@pytest.mark.parametrize("epsg,cell,origin", [(32632, 100, (0, 1000)), (4326, 1 / 1200, (80, 30))])
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


@pytest.mark.parametrize("version", ["5.13", "6"])
def test_setup_publishes_then_links_then_replaces_domain_dem(tmp_path, version):
    ensure_project_structure(tmp_path, version)
    write_json(input_state_path(tmp_path), {"mhm_version": version})
    folder = Path(geometry_folder(tmp_path))
    original = _raster(tmp_path / "source.tif")
    result = dem_derivative_files(original, folder)
    bundle_bytes = Path(result["dem_derivatives"]).read_bytes()
    gauges = np.full((10, 10), -9999.0)
    gauges[4, 4] = 280
    copy_rasters(folder / "idgauges.nc", {"idgauges": _raster(tmp_path / "gauges.tif", gauges)})
    keep = np.zeros((10, 10))
    keep[:, 2:8] = 1
    mask = _raster(folder / "Watersheds" / MERGED_MASK_NAME, keep)
    domain_state.save_state(tmp_path, {
        "definition_mode": "domain_delineator", "outlet_order": ["001", "002"],
        "outlets": {name: {"is_domain": True, "mask_path": str(mask)} for name in ("001", "002")},
    })
    for name in ("3_land_use.tif", "3_soil.tif", "3_geology_processed.tif"):
        _raster(folder / name, np.full((10, 10), 2.0))
    staging = Path(morph_staging_folder(tmp_path))
    staging.mkdir(parents=True, exist_ok=True)
    for name in ("soil_classdefinition.txt", "geology_classdefinition.txt"):
        (staging / name).write_text("class definition", encoding="utf-8")
    header = {"ncols": 12, "nrows": 12, "xllcorner": -100, "yllcorner": -100, "cellsize": 100, "nodata_value": -9999}
    request = setup.SetupRequest(str(tmp_path), {level: header for level in ("L0", "L1", "L11", "L2")}, crs="EPSG:32632")
    master = Path(morph_folder(tmp_path))
    target = Path(domain_dem_path(tmp_path, "001"))
    target.parent.mkdir(parents=True, exist_ok=True)
    # An existing shared DEM link must be replaced, not followed, by either writer.
    shared_dem = tmp_path / "shared-dem"
    shared_dem.write_bytes(b"master must not change")
    target.symlink_to(shared_dem)
    messages = []

    def log(message):
        messages.append(message)
        if version == "5.13" and message.endswith("Link domain inputs"):
            assert (master / "idgauges.asc").is_file()
            assert not (target.parent / "idgauges.asc").exists()

    assert setup.run(request, log=log)
    assert shared_dem.read_bytes() == b"master must not change"
    assert target.is_file() and not target.is_symlink()
    assert Path(result["dem_derivatives"]).read_bytes() == bundle_bytes
    assert processing.workflow(tmp_path, "morph_setup_link_domains")["status"] == "completed"
    assert messages.index(next(m for m in messages if m.endswith("Publish model inputs"))) < messages.index(next(m for m in messages if m.endswith("Link domain inputs"))) < messages.index(next(m for m in messages if m.endswith("Write domain DEMs")))
    raster = gdal.Open(str(target))
    assert header_from_geotransform(raster.GetGeoTransform(), raster.RasterXSize, raster.RasterYSize)["ncols"] == 12
    values = raster.ReadAsArray()
    assert np.all(values[0] == -9999)
    assert np.all(values[:, :3] == -9999)
    np.testing.assert_array_equal(values[1:11, 3:9], gdal.Open(result["filled_dem"]).ReadAsArray()[:, 2:8])
    raster = None
    if version == "5.13":
        for _label, names in DOMAIN_INPUT_GROUPS:
            assert (target.parent / names[0]).is_file()
        assert (target.parent / "slope.prj").is_file()
        assert target.with_suffix(".prj").is_file()
        assert (target.parent / "soil_class.asc").resolve() == master / "soil_class.asc"
        for name in ("001", "002"):
            directory = Path(domain_dem_path(tmp_path, name)).parent
            for filename in ("idgauges.asc", "idgauges.prj"):
                assert (directory / filename).is_symlink()
                assert (directory / filename).resolve() == master / filename
            assert gdal.Open(str(directory / "idgauges.asc")).ReadAsArray()[5, 5] == 280
        classes = gdal.Open(str(master / "soil_class.asc")).ReadAsArray()
    else:
        assert "dem" in variables(master / "input.nc")
        assert not (target.parent / "slope.asc").exists()
        classes = gdal.Open(source(master / "input.nc", "soil_class")).ReadAsArray()
    assert classes[0, 0] == 1
    assert classes[5, 2] == 2  # Outside the watershed: classes must stay valid.


def test_link_stage_fails_for_missing_published_inputs(tmp_path):
    domain_state.save_state(tmp_path, {"outlet_order": ["001"], "outlets": {"001": {"is_domain": True}}})
    with pytest.raises(FileNotFoundError, match="Published domain inputs missing"):
        setup.link_domains(setup.SetupRequest(str(tmp_path), {}))
