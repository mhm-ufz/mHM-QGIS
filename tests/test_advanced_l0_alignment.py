"""Advanced categorical inputs must be placed on the common L0 grid, never resampled."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.handlers.file.ascii.morphology import (  # noqa: E402
    pad_dataset_to_header,
    pad_l0_file_to_header,
)
from mhm_qgis.core.morphology.layers.advanced_l0 import (  # noqa: E402
    missing_model_inputs,
    pad_spec_for,
    publish_model_inputs,
    staged_files,
)
from mhm_qgis.core.morphology.layers import (  # noqa: E402
    CATEGORICAL_PAD_VALUE,
    LAI_PAD_VALUE,
)
from mhm_qgis.core.handlers.state.nml_settings import update_section  # noqa: E402
from mhm_qgis.core.handlers.store.paths import morph_staging_folder, workspace_folder
from mhm_qgis.core.handlers.store.layout import morph_folder  # noqa: E402

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


def _staged_asc(project, name, ncols=2, nrows=2, xll=100.0, yll=100.0, cell=100.0):
    """Write a staged ASCII grid as Execute All would."""
    folder = Path(morph_staging_folder(project))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    rows = "\n".join(
        " ".join(str(row * ncols + column + 1) for column in range(ncols))
        for row in range(nrows)
    )
    path.write_text(
        f"ncols         {ncols}\nnrows         {nrows}\n"
        f"xllcorner     {xll}\nyllcorner     {yll}\n"
        f"cellsize      {cell}\nNODATA_value  -9999\n{rows}\n",
        encoding="utf-8",
    )
    return path


L0_TARGET = {
    "ncols": 4, "nrows": 4, "xllcorner": 0.0, "yllcorner": 0.0,
    "cellsize": 100.0, "unit": "m", "nodata_value": -9999.0,
}


def test_rasters_are_padded_then_moved_and_definitions_carried(tmp_path):
    _staged_asc(tmp_path, "soil_class.asc")
    definition = Path(morph_staging_folder(tmp_path)) / "soil_classdefinition.txt"
    definition.write_text("1 sand\n", encoding="utf-8")

    published = publish_model_inputs(tmp_path, L0_TARGET)

    master = Path(morph_folder(tmp_path))
    assert sorted(path.name for path in published) == [
        "soil_class.asc", "soil_classdefinition.txt",
    ]
    # Keep the uncropped raster for later changes to the common extent.
    assert [path.name for path in staged_files(tmp_path)] == ["soil_class.asc"]
    assert (master / "soil_classdefinition.txt").read_text(encoding="utf-8") == "1 sand\n"
    header = (master / "soil_class.asc").read_text(encoding="utf-8").splitlines()
    assert header[0].split()[1] == "4" and header[1].split()[1] == "4"


def test_reused_legacy_products_shrink_then_expand_without_losing_cells(tmp_path):
    from mhm_qgis.core.handlers.file.ascii.pad import read_ascii_header
    from mhm_qgis.core.handlers.store.morphology import resolve_display_output

    master = Path(morph_folder(tmp_path))
    master.mkdir(parents=True, exist_ok=True)
    names = ("soil_class.asc", "lc_2015_2015.asc", "lc_2016_2016.asc")
    originals = {}
    for name in names:
        path = _staged_asc(tmp_path, name, ncols=4, nrows=4, xll=0, yll=0)
        originals[name] = path.read_text().splitlines()[6:]
        path.replace(master / name)  # Older publication discarded staging.
    update_section(tmp_path, "soil", {"output_path": str(master / names[0])})
    update_section(tmp_path, "land_cover", {"scenes": [
        {"start_year": year, "end_year": year, "output_path": str(master / name)}
        for year, name in zip((2015, 2016), names[1:])
    ]})
    small = {**L0_TARGET, "ncols": 2, "nrows": 2, "xllcorner": 100, "yllcorner": 100}
    publish_model_inputs(tmp_path, small)
    for name in names:
        assert read_ascii_header(master / name)["ncols"] == 2
        assert (master / name).read_text().splitlines()[6:] == ["6 7", "10 11"]
    assert resolve_display_output(tmp_path, "soil").path == master / names[0]
    assert resolve_display_output(tmp_path, "land_cover", year=2016).path == master / names[2]
    assert publish_model_inputs(tmp_path, small) == []
    publish_model_inputs(tmp_path, L0_TARGET)
    for name in names:
        assert (master / name).read_text().splitlines()[6:] == originals[name]


def test_class_layers_are_padded_with_a_class_and_lai_with_zero(tmp_path):
    """Padding onto L0 must never leave nodata inside the model domain."""
    assert pad_spec_for(Path("lc_2015_2015.asc")) == (CATEGORICAL_PAD_VALUE, True)
    assert pad_spec_for(Path("soil_class.asc")) == (CATEGORICAL_PAD_VALUE, True)
    assert pad_spec_for(Path("soil_horizon_class.nc")) == (CATEGORICAL_PAD_VALUE, True)
    assert pad_spec_for(Path("geology_class.asc")) == (CATEGORICAL_PAD_VALUE, True)
    assert pad_spec_for(Path("lai.nc")) == (LAI_PAD_VALUE, False)
    # An unrecognised raster keeps the historical nodata padding.
    assert pad_spec_for(Path("dem.asc")) == (None, True)

    _staged_asc(tmp_path, "soil_class.asc")
    publish_model_inputs(tmp_path, L0_TARGET)

    lines = (
        (Path(morph_folder(tmp_path)) / "soil_class.asc")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    # The source occupies the two centre cells of the 4x4 target.
    assert lines[5].split() == ["NODATA_value", "-9999"]
    rows = [line.split() for line in lines[6:] if line.strip()]
    assert rows[0] == [str(CATEGORICAL_PAD_VALUE)] * 4
    assert rows[3] == [str(CATEGORICAL_PAD_VALUE)] * 4
    assert rows[1] == [str(CATEGORICAL_PAD_VALUE), "1", "2", str(CATEGORICAL_PAD_VALUE)]
    assert rows[2] == [str(CATEGORICAL_PAD_VALUE), "3", "4", str(CATEGORICAL_PAD_VALUE)]


def test_publishing_repoints_the_namelist_handoff_at_data_master(tmp_path):
    _staged_asc(tmp_path, "soil_class.asc")
    _staged_asc(tmp_path, "lc_2015_2015.asc")
    definition = Path(morph_staging_folder(tmp_path)) / "soil_classdefinition.txt"
    definition.write_text("1 sand\n", encoding="utf-8")

    # Execute All recorded the staged locations.
    update_section(tmp_path, "soil", {
        "output_path": "Z Temp/Morphology/morph/soil_class.asc",
        "classdefinition_path": "Z Temp/Morphology/morph/soil_classdefinition.txt",
    })
    update_section(tmp_path, "land_cover", {
        "output_path": "",
        "scenes": [{"output_path": "Z Temp/Morphology/morph/lc_2015_2015.asc"}],
    })

    publish_model_inputs(tmp_path, L0_TARGET)

    from mhm_qgis.core.handlers.state.nml_settings import load_settings

    settings = load_settings(tmp_path)
    assert settings["soil"]["output_path"] == (
        "data/master/static/morph/soil_class.asc"
    )
    assert settings["soil"]["classdefinition_path"] == (
        "data/master/static/morph/soil_classdefinition.txt"
    )
    assert settings["land_cover"]["scenes"][0]["output_path"] == (
        "data/master/static/morph/lc_2015_2015.asc"
    )
    # Every recorded path now resolves.
    assert missing_model_inputs(tmp_path) == []


def test_missing_model_inputs_reports_paths_that_do_not_resolve(tmp_path):
    update_section(tmp_path, "soil", {
        "output_path": "data/master/static/morph/soil_class.asc",
    })
    update_section(tmp_path, "lai", {
        "output_path": "data/master/lai/lai.nc",
    })

    missing = missing_model_inputs(tmp_path)
    assert sorted(missing) == [
        "data/master/lai/lai.nc",
        "data/master/static/morph/soil_class.asc",
    ]


def test_an_unexpected_staged_file_is_left_alone(tmp_path):
    folder = Path(morph_staging_folder(tmp_path))
    folder.mkdir(parents=True, exist_ok=True)
    stray = folder / "scratch.tmp"
    stray.write_text("x", encoding="utf-8")
    _staged_asc(tmp_path, "soil_class.asc")

    published = publish_model_inputs(tmp_path, L0_TARGET)

    assert [path.name for path in published] == ["soil_class.asc"]
    assert stray.is_file()          # not moved into data/master


def test_publishing_an_empty_staging_folder_is_a_no_op(tmp_path):
    assert publish_model_inputs(tmp_path, L0_TARGET) == []
    assert staged_files(tmp_path) == []


def test_a_path_left_pointing_at_staging_is_repointed_to_master(tmp_path):
    """A reused stage may record staging while the file is already published."""
    master = Path(morph_folder(tmp_path))
    master.mkdir(parents=True, exist_ok=True)
    (master / "geology_classdefinition.txt").write_text("1 rock\n", encoding="utf-8")
    update_section(tmp_path, "geology", {
        "classdefinition_path": "Z Temp/Morphology/morph/geology_classdefinition.txt",
    })
    assert missing_model_inputs(tmp_path)          # stale path does not resolve

    publish_model_inputs(tmp_path, L0_TARGET)      # nothing staged to move

    from mhm_qgis.core.handlers.state.nml_settings import load_settings

    assert load_settings(tmp_path)["geology"]["classdefinition_path"] == (
        "data/master/static/morph/geology_classdefinition.txt"
    )
    assert missing_model_inputs(tmp_path) == []
