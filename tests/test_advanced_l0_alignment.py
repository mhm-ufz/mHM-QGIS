"""Advanced categorical inputs must be placed on the common L0 grid, never resampled."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.handlers.file.ascii.morphology import (
    pad_dataset_to_header,
    pad_l0_file_to_header,
)
from mhm_qgis.core.handlers.state.nml_settings import update_section
from mhm_qgis.core.handlers.store.layout import morph_folder
from mhm_qgis.core.handlers.store.paths import morph_staging_folder
from mhm_qgis.core.morphology.layers import CATEGORICAL_PAD_VALUE
from mhm_qgis.core.morphology.layers.advanced_l0 import (
    missing_model_inputs,
    publish_model_inputs,
    staged_files,
)

xr = pytest.importorskip("xarray")


# Filled-DEM sized grid: 2 columns by 2 rows starting at (200, 200).
SOURCE_X = [250.0, 350.0]
SOURCE_Y = [350.0, 250.0]
SOURCE_VALUES = np.array([[1.0, 2.0], [3.0, 4.0]])
# Expanded common L0 extent: one extra cell on every side.
L0_HEADER = {
    "ncols": 4,
    "nrows": 4,
    "xllcorner": 100.0,
    "yllcorner": 100.0,
    "cellsize": 100.0,
    "nodata_value": -9999.0,
    "unit": "m",
}


def _dataset(x_values=None, y_values=None, values=None, name="soil_class"):
    return xr.DataArray(
        SOURCE_VALUES if values is None else values,
        dims=("y", "x"),
        coords={
            "y": np.asarray(SOURCE_Y if y_values is None else y_values, dtype="float64"),
            "x": np.asarray(SOURCE_X if x_values is None else x_values, dtype="float64"),
        },
        name=name,
    ).to_dataset()


def test_source_is_placed_on_the_expanded_extent_and_padded_with_nodata():
    padded = pad_dataset_to_header(_dataset(), L0_HEADER, integer=True)
    values = padded["soil_class"].sortby("y", ascending=False).sortby("x").values

    assert values.shape == (4, 4)
    np.testing.assert_array_equal(values[1:3, 1:3], SOURCE_VALUES)
    outside = np.ones(values.shape, dtype=bool)
    outside[1:3, 1:3] = False
    assert np.all(values[outside] == -9999)


def test_a_pad_value_fills_the_expansion_without_changing_the_nodata():
    padded = pad_dataset_to_header(
        _dataset(), L0_HEADER, integer=True, pad_value=CATEGORICAL_PAD_VALUE
    )
    values = padded["soil_class"].sortby("y", ascending=False).sortby("x").values

    np.testing.assert_array_equal(values[1:3, 1:3], SOURCE_VALUES)
    outside = np.ones(values.shape, dtype=bool)
    outside[1:3, 1:3] = False
    assert np.all(values[outside] == CATEGORICAL_PAD_VALUE)
    assert padded["soil_class"].attrs["nodata_value"] == -9999


def test_extra_dimensions_such_as_soil_horizons_are_preserved():
    horizons = np.stack([SOURCE_VALUES, SOURCE_VALUES + 10.0])
    dataset = xr.DataArray(
        horizons,
        dims=("horizon", "y", "x"),
        coords={
            "horizon": [1, 2],
            "y": np.asarray(SOURCE_Y, dtype="float64"),
            "x": np.asarray(SOURCE_X, dtype="float64"),
        },
        name="soil_class",
    ).to_dataset()

    padded = pad_dataset_to_header(dataset, L0_HEADER, integer=True)
    values = padded["soil_class"].sortby("y", ascending=False).sortby("x").values

    assert values.shape == (2, 4, 4)
    np.testing.assert_array_equal(values[0, 1:3, 1:3], SOURCE_VALUES)
    np.testing.assert_array_equal(values[1, 1:3, 1:3], SOURCE_VALUES + 10.0)


def test_a_misaligned_source_is_rejected_instead_of_resampled():
    shifted = _dataset(x_values=[300.0, 400.0])

    with pytest.raises(ValueError, match="not aligned to the L0 grid"):
        pad_dataset_to_header(shifted, L0_HEADER)


def test_a_file_already_on_the_l0_grid_is_left_untouched(tmp_path):
    path = tmp_path / "soil_horizon_class.nc"
    x_values = [150.0, 250.0, 350.0, 450.0]
    y_values = [450.0, 350.0, 250.0, 150.0]
    values = np.arange(16, dtype="float64").reshape(4, 4)
    _dataset(x_values, y_values, values).to_netcdf(path)
    before = path.read_bytes()

    assert pad_l0_file_to_header(path, L0_HEADER, integer=True) == path
    assert path.read_bytes() == before


def _staged_nc(project, name, header=None, variable="soil_class"):
    from osgeo import gdal, osr

    from mhm_qgis.core.handlers.file.netcdf.raster import copy_rasters

    header = header or L0_TARGET
    values = np.arange(header["nrows"] * header["ncols"]).reshape(
        header["nrows"], header["ncols"]
    )
    folder = Path(morph_staging_folder(project))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    raster = folder / f".{path.stem}.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(raster), header["ncols"], header["nrows"], 1, gdal.GDT_Int32
    )
    dataset.SetGeoTransform((
        header["xllcorner"], header["cellsize"], 0,
        header["yllcorner"] + header["nrows"] * header["cellsize"],
        0, -header["cellsize"],
    ))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(32632)
    dataset.SetProjection(crs.ExportToWkt())
    dataset.GetRasterBand(1).SetNoDataValue(-9999)
    dataset.GetRasterBand(1).WriteArray(values)
    dataset = None
    copy_rasters(path, {variable: raster})
    raster.unlink()
    return path


def _staged_soil_horizons(project, count=2):
    from pyproj import CRS

    folder = Path(morph_staging_folder(project))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "soil_horizon_class.nc"
    header = L0_TARGET
    x = header["xllcorner"] + (
        np.arange(header["ncols"]) + 0.5
    ) * header["cellsize"]
    y = header["yllcorner"] + (
        np.arange(header["nrows"])[::-1] + 0.5
    ) * header["cellsize"]
    values = np.arange(count * header["nrows"] * header["ncols"]).reshape(
        count, header["nrows"], header["ncols"]
    )
    dataset = xr.DataArray(
        values,
        dims=("horizon", "y", "x"),
        coords={"horizon": range(1, count + 1), "y": y, "x": x},
        name="soil_class",
    ).to_dataset()
    crs = CRS.from_epsg(32632)
    dataset["x"].attrs = {
        "axis": "X", "standard_name": "projection_x_coordinate", "units": "m"
    }
    dataset["y"].attrs = {
        "axis": "Y", "standard_name": "projection_y_coordinate", "units": "m"
    }
    dataset["soil_class"].attrs["grid_mapping"] = "crs"
    dataset["soil_class"].encoding["_FillValue"] = -9999
    dataset["crs"] = xr.DataArray(0, attrs={
        **crs.to_cf(),
        "spatial_ref": crs.to_wkt(),
        "GeoTransform": "0 100 0 400 0 -100",
    })
    dataset.to_netcdf(path)
    return path


L0_TARGET = {
    "ncols": 4, "nrows": 4, "xllcorner": 0.0, "yllcorner": 0.0,
    "cellsize": 100.0, "unit": "m", "nodata_value": -9999.0,
}


def test_exact_netcdf_is_converted_and_definitions_are_carried(tmp_path):
    _staged_nc(tmp_path, "soil_class.nc")
    definition = Path(morph_staging_folder(tmp_path)) / "soil_classdefinition.txt"
    definition.write_text("1 sand\n", encoding="utf-8")

    published = publish_model_inputs(tmp_path, L0_TARGET)

    master = Path(morph_folder(tmp_path))
    assert sorted(path.name for path in published) == [
        "soil_class.asc", "soil_classdefinition.txt",
    ]
    # Keep the uncropped raster for later changes to the common extent.
    assert [path.name for path in staged_files(tmp_path)] == ["soil_class.nc", "soil_classdefinition.txt"]
    assert (master / "soil_classdefinition.txt").read_text(encoding="utf-8") == "1 sand\n"
    header = (master / "soil_class.asc").read_text(encoding="utf-8").splitlines()
    assert header[0].split()[1] == "4"
    assert header[1].split()[1] == "4"


def test_v5_soil_horizons_are_published_as_ascii_layers(tmp_path):
    _staged_soil_horizons(tmp_path)
    definition = Path(morph_staging_folder(tmp_path)) / (
        "soil_classdefinition_iFlag_soilDB_1.txt"
    )
    definition.write_text("definition\n", encoding="utf-8")
    update_section(tmp_path, "soil", {
        "output_path": ".temp/Morphology/morph/soil_horizon_class.nc",
        "classdefinition_path": (
            ".temp/Morphology/morph/soil_classdefinition_iFlag_soilDB_1.txt"
        ),
        "horizons": [{"lower_depth": 100}, {"lower_depth": 300}],
    })

    published = publish_model_inputs(tmp_path, L0_TARGET)

    assert sorted(path.name for path in published) == [
        "soil_class_horizon_01.asc",
        "soil_class_horizon_02.asc",
        "soil_classdefinition_iFlag_soilDB_1.txt",
    ]
    from mhm_qgis.core.handlers.state.nml_settings import load_settings

    soil = load_settings(tmp_path)["soil"]
    assert soil["output_path"] == "data/static/morph/soil_class_horizon_01.asc"
    assert soil["classdefinition_path"] == (
        "data/static/morph/soil_classdefinition_iFlag_soilDB_1.txt"
    )
    assert missing_model_inputs(tmp_path) == []


def test_publication_rejects_a_grid_that_is_not_the_common_extent(tmp_path):
    _staged_nc(tmp_path, "soil_class.nc", L0_TARGET)
    small = {**L0_TARGET, "ncols": 2, "nrows": 2, "xllcorner": 100, "yllcorner": 100}
    with pytest.raises(ValueError, match="does not match the common L0 grid"):
        publish_model_inputs(tmp_path, small)


def test_publication_does_not_invent_class_values_for_missing_cells(tmp_path):
    source = {**L0_TARGET, "ncols": 2, "nrows": 2, "xllcorner": 100, "yllcorner": 100}
    _staged_nc(tmp_path, "soil_class.nc", source)
    with pytest.raises(ValueError, match="does not match the common L0 grid"):
        publish_model_inputs(tmp_path, L0_TARGET)


def test_publishing_repoints_the_namelist_handoff_at_data_master(tmp_path):
    _staged_nc(tmp_path, "soil_class.nc")
    _staged_nc(tmp_path, "lc.nc", variable="land_cover")
    definition = Path(morph_staging_folder(tmp_path)) / "soil_classdefinition.txt"
    definition.write_text("1 sand\n", encoding="utf-8")

    # Execute All recorded the staged locations.
    update_section(tmp_path, "soil", {
        "output_path": ".temp/Morphology/morph/soil_class.nc",
        "classdefinition_path": ".temp/Morphology/morph/soil_classdefinition.txt",
    })
    update_section(tmp_path, "land_cover", {
        "output_path": "",
        "scenes": [{"output_path": ".temp/Morphology/morph/lc.asc"}],
    })

    publish_model_inputs(tmp_path, L0_TARGET)

    from mhm_qgis.core.handlers.state.nml_settings import load_settings

    settings = load_settings(tmp_path)
    assert settings["soil"]["output_path"] == (
        "data/static/morph/soil_class.asc"
    )
    assert settings["soil"]["classdefinition_path"] == (
        "data/static/morph/soil_classdefinition.txt"
    )
    assert settings["land_cover"]["scenes"][0]["output_path"] == (
        "data/static/morph/lc.asc"
    )
    # Every recorded path now resolves.
    assert missing_model_inputs(tmp_path) == []


def test_missing_model_inputs_reports_paths_that_do_not_resolve(tmp_path):
    update_section(tmp_path, "soil", {
        "output_path": "data/static/morph/soil_class.asc",
    })
    update_section(tmp_path, "lai", {
        "output_path": "data/lai/lai.nc",
    })

    missing = missing_model_inputs(tmp_path)
    assert sorted(missing) == [
        "data/lai/lai.nc",
        "data/static/morph/soil_class.asc",
    ]


def test_an_unexpected_staged_file_is_left_alone(tmp_path):
    folder = Path(morph_staging_folder(tmp_path))
    folder.mkdir(parents=True, exist_ok=True)
    stray = folder / "scratch.tmp"
    stray.write_text("x", encoding="utf-8")
    _staged_nc(tmp_path, "soil_class.nc")

    published = publish_model_inputs(tmp_path, L0_TARGET)

    assert [path.name for path in published] == ["soil_class.asc"]
    assert stray.is_file()          # not moved into data/


def test_publishing_an_empty_staging_folder_is_a_no_op(tmp_path):
    assert publish_model_inputs(tmp_path, L0_TARGET) == []
    assert staged_files(tmp_path) == []


def test_a_path_left_pointing_at_staging_is_repointed_to_master(tmp_path):
    """A reused stage may record staging while the file is already published."""
    master = Path(morph_folder(tmp_path))
    master.mkdir(parents=True, exist_ok=True)
    (master / "geology_classdefinition.txt").write_text("1 rock\n", encoding="utf-8")
    update_section(tmp_path, "geology", {
        "classdefinition_path": ".temp/Morphology/morph/geology_classdefinition.txt",
    })
    assert missing_model_inputs(tmp_path)          # stale path does not resolve

    publish_model_inputs(tmp_path, L0_TARGET)      # nothing staged to move

    from mhm_qgis.core.handlers.state.nml_settings import load_settings

    assert load_settings(tmp_path)["geology"]["classdefinition_path"] == (
        "data/static/morph/geology_classdefinition.txt"
    )
    assert missing_model_inputs(tmp_path) == []


def test_republishing_rewrites_nothing_unchanged_and_ignores_stale_staged_copies(tmp_path):
    """Publication used to copy its own ASCII output back into staging and then
    export every such grid twice, first from the stale copy."""
    import shutil

    _staged_soil_horizons(tmp_path)
    update_section(tmp_path, "soil", {
        "output_path": ".temp/Morphology/morph/soil_horizon_class.nc",
        "horizons": [{"lower_depth": 100}, {"lower_depth": 300}],
    })
    publish_model_inputs(tmp_path, L0_TARGET)
    staging = Path(morph_staging_folder(tmp_path))
    horizon = Path(morph_folder(tmp_path)) / "soil_class_horizon_01.asc"
    assert [path.name for path in staged_files(tmp_path)] == ["soil_horizon_class.nc"]

    shutil.copy2(horizon, staging / horizon.name)  # What an older version left behind.
    old = horizon.stat().st_mtime_ns - 10**9
    os.utime(horizon, ns=(old, old))
    messages = []
    publish_model_inputs(tmp_path, L0_TARGET, log=messages.append)

    assert horizon.stat().st_mtime_ns == old  # Unchanged: kept, not rewritten.
    assert "Skipping soil_class_horizon_01.asc: a staged NetCDF produces it." in messages
