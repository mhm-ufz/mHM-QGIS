"""Focused tests for the path-only categorical morphology API."""
from __future__ import annotations

from pathlib import Path

import pytest

from mhm_qgis.core.handlers.state.nml_settings import load_settings, update_section
from mhm_qgis.core.handlers.store.layout import ensure_project_structure, morph_folder
from mhm_qgis.core.handlers.store.paths import morph_staging_folder
from mhm_qgis.core.morphology.layers import categorical


def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Lookup Table", "lookup_table"),
        ("lookup-table", "lookup_table"),
        ("mHM_ready", "mhm_ready"),
        (None, ""),
    ],
)
def test_modes_have_stable_api_names(value, expected):
    assert categorical.normalized_mode(value) == expected


def test_path_helpers_keep_intermediates_out_of_model_inputs(tmp_path):
    ensure_project_structure(tmp_path, "5.13")

    raw, cropped, masked = categorical.intermediate_paths(tmp_path, "soil")
    ready = categorical.ready_paths(tmp_path, "soil")

    assert raw.name == "3_soil.tif"
    assert cropped.name == "3_soil_crop.tif"
    assert masked.name == "3_soil_masked.tif"
    assert all(path.parent == Path(morph_folder(tmp_path)) for path in ready)
    assert {path.suffix.lower() for path in ready} >= {".asc", ".nc", ".tif"}


def test_ready_land_cover_is_published_and_recorded(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    source = _touch(tmp_path / "input" / "land_cover.asc", b"new raster")
    _touch(source.with_suffix(".prj"), b"projection")
    stale = [
        _touch(path, b"stale")
        for path in categorical.intermediate_paths(tmp_path, "lc")
    ]

    outputs = categorical.copy_ready(tmp_path, "lc", source)

    target = Path(morph_staging_folder(tmp_path)) / "lc.nc"
    assert target.read_bytes() == b"new raster"
    assert target.with_suffix(".prj").read_bytes() == b"projection"
    assert set(outputs) == {target, target.with_suffix(".prj")}
    assert all(path.exists() for path in stale)
    assert load_settings(tmp_path)["land_cover"]["output_path"] == (
        ".temp/Morphology/morph/lc.nc"
    )


def test_ready_soil_optionally_publishes_its_class_definition(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    source = _touch(tmp_path / "input" / "soil.tif", b"soil")

    first = categorical.copy_ready(tmp_path, "soil", source)
    target = Path(morph_staging_folder(tmp_path)) / "soil_class.nc"
    assert first == (target,)

    definition = _touch(tmp_path / "input" / "soil_classes.txt", b"classes")
    outputs = categorical.copy_ready(
        tmp_path, "soil", source, definition_source=definition
    )

    target_definition = target.parent / "soil_classdefinition.txt"
    assert set(outputs) == {target, target_definition}
    assert target.read_bytes() == b"soil"
    assert target_definition.read_bytes() == b"classes"


def test_ready_mode_rejects_non_raster_input(tmp_path):
    source = _touch(tmp_path / "soil.shp")

    with pytest.raises(ValueError, match="ASC, NetCDF, or TIFF"):
        categorical.copy_ready(tmp_path, "soil", source)


def test_ready_raster_is_placed_on_the_common_grid(tmp_path):
    pytest.importorskip("osgeo")
    import numpy as np
    from osgeo import gdal, osr

    ensure_project_structure(tmp_path, "6")
    source = tmp_path / "input" / "soil.tif"
    source.parent.mkdir()
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(source), 2, 2, 1, gdal.GDT_Int16
    )
    dataset.SetGeoTransform((0, 100, 0, 200, 0, -100))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(32632)
    dataset.SetProjection(crs.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(np.full((2, 2), 4, dtype="int16"))
    dataset = None

    output = categorical.copy_ready(
        tmp_path,
        "soil",
        source,
        source_crs="EPSG:32632",
        target_crs="EPSG:32632",
        target_header={
            "ncols": 4, "nrows": 4, "xllcorner": -100,
            "yllcorner": -100, "cellsize": 100,
        },
    )[0]

    values = gdal.Open(str(output)).ReadAsArray()
    assert values.shape == (4, 4)
    np.testing.assert_array_equal(values[1:3, 1:3], 4)
    assert np.all(values[[0, 3], :] == -9999)
    assert np.all(values[:, [0, 3]] == -9999)


def test_ready_lai_records_the_class_mode(tmp_path):
    ensure_project_structure(tmp_path, "6")
    source = _touch(tmp_path / "input" / "lai_class.asc", b"class raster")
    definition = _touch(tmp_path / "input" / "lai.txt", b"classes")

    outputs = categorical.copy_ready(
        tmp_path, "lai", source, definition_source=definition
    )

    folder = Path(morph_staging_folder(tmp_path))
    assert set(outputs) == {
        folder / "LAI_class.nc", folder / "LAI_classdefinition.txt"
    }
    settings = load_settings(tmp_path)["lai"]
    assert settings["mode"] == "mhm_ready"
    assert settings["time_step"] == 0
    assert settings["variable"] == "LAI_class"


def test_failed_publish_restores_the_previous_output_set(tmp_path, monkeypatch):
    ensure_project_structure(tmp_path, "5.13")
    source = _touch(tmp_path / "input" / "geology.tif", b"new raster")
    definition_source = _touch(tmp_path / "input" / "new.txt", b"new definition")
    folder = Path(morph_staging_folder(tmp_path))
    output = _touch(folder / "geology_class.tif", b"old raster")
    definition = _touch(folder / "geology_classdefinition.txt", b"old definition")

    real_replace = categorical.os.replace
    failed = False

    def fail_definition_publish(source_path, destination_path):
        nonlocal failed
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        if (
            destination_path == definition
            and source_path.name == definition.name
            and not failed
        ):
            failed = True
            raise OSError("simulated publish failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(categorical.os, "replace", fail_definition_publish)

    with pytest.raises(OSError, match="simulated"):
        categorical.copy_ready(
            tmp_path,
            "geology",
            source,
            definition_source=definition_source,
        )

    assert output.read_bytes() == b"old raster"
    assert definition.read_bytes() == b"old definition"


def test_saved_outputs_requires_the_complete_recorded_set(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    folder = Path(morph_folder(tmp_path))
    output = _touch(folder / "soil_class.asc")
    definition = folder / "soil_classdefinition.txt"
    update_section(
        tmp_path,
        "soil",
        {
            "output_path": "data/static/morph/soil_class.asc",
            "classdefinition_path": (
                "data/static/morph/soil_classdefinition.txt"
            ),
        },
    )

    assert categorical.saved_outputs(tmp_path, "soil") == ()
    _touch(definition)
    assert categorical.saved_outputs(tmp_path, "soil") == (output, definition)


L0 = {"ncols": 6, "nrows": 6, "xllcorner": -100, "yllcorner": -100, "cellsize": 100}


def _stack(path, name, dim, edges, units):
    """A ready NetCDF with one layer per period/horizon on a coarser, offset grid (200 m)."""
    import numpy as np
    from netCDF4 import Dataset

    with Dataset(path, "w") as handle:
        for axis, size in ((dim, len(edges)), ("bnds", 2), ("y", 2), ("x", 2)):
            handle.createDimension(axis, size)
        for axis, values in (("x", [100.0, 300.0]), ("y", [300.0, 100.0])):
            var = handle.createVariable(axis, "f8", (axis,))
            var.setncatts({"standard_name": f"projection_{axis}_coordinate", "units": "m", "axis": axis.upper()})
            var[:] = values
        middle = handle.createVariable(dim, "f8", (dim,))
        middle.setncatts({"units": units, "bounds": f"{dim}_bnds"})
        middle[:] = [sum(pair) / 2 for pair in edges]
        handle.createVariable(f"{dim}_bnds", "f8", (dim, "bnds"))[:] = edges
        layers = handle.createVariable(name, "i4", (dim, "y", "x"), fill_value=-9999)
        layers[:] = np.stack([np.full((2, 2), 10 + index) for index in range(len(edges))])


@pytest.mark.parametrize("version", ["5.13", "6"])
def test_ready_periods_and_horizons_are_warped_onto_l0(tmp_path, version):
    """Multi-layer ready NetCDFs are warped like a single layer (nearest, -9999 outside),
    keeping every period or horizon, and publication splits them for v5.13."""
    pytest.importorskip("osgeo")
    import numpy as np
    from netCDF4 import Dataset

    from mhm_qgis.core.handlers.file.json import write as write_json
    from mhm_qgis.core.handlers.store.layout import input_state_path
    from mhm_qgis.core.morphology.layers.advanced_l0 import publish_model_inputs

    ensure_project_structure(tmp_path, version)
    write_json(input_state_path(tmp_path), {"mhm_version": version})
    folder = tmp_path / "input"
    folder.mkdir()
    days = [[3652, 11321], [11321, 14974], [14974, 18262]]  # 1980-01-01, 2000-12-30, 2010-12-30, 2020-01-01
    _stack(folder / "lc_periods.nc", "lc", "time", days, "days since 1970-01-01")
    _stack(folder / "horizons.nc", "classes", "z", [[0, 50], [50, 150], [150, 300]], "mm")
    (folder / "soil.txt").write_text("soil classes")
    grid = {"source_crs": "EPSG:32632", "target_crs": "EPSG:32632", "target_header": L0}

    land = categorical.copy_ready(tmp_path, "lc", folder / "lc_periods.nc", variable="lc", **grid)[0]
    soil = categorical.copy_ready(tmp_path, "soil", folder / "horizons.nc", variable="classes",
                                  definition_source=folder / "soil.txt", **grid)[0]

    assert land.name == "lc_periods.nc"
    assert soil.name == "soil_horizon_class.nc"
    with Dataset(land) as handle:
        values = handle["land_cover"][:].filled(-9999)
        assert values.shape == (3, 6, 6)
        assert handle["time_bnds"][:].tolist() == days
    np.testing.assert_array_equal(values[:, 1:5, 1:5], np.broadcast_to(np.array([10, 11, 12])[:, None, None], (3, 4, 4)))
    assert np.all(values[:, [0, 5], :] == -9999)
    assert np.all(values[:, :, [0, 5]] == -9999)
    settings = load_settings(tmp_path)
    scenes = settings["land_cover"]["scenes"]
    assert [(scene["start_year"], scene["end_year"]) for scene in scenes] == [(1980, 2000), (2001, 2010), (2011, 2019)]
    assert settings["soil"]["soil_db_mode"] == 1
    assert settings["soil"]["classdefinition_path"].endswith("soil_classdefinition_iFlag_soilDB_1.txt")
    assert [horizon["lower_depth"] for horizon in settings["soil"]["horizons"]] == [50, 150, 300]

    published = {path.name for path in publish_model_inputs(tmp_path, L0)}
    if version == "6":
        assert {"lc_periods.nc", "soil_horizon_class.nc"} <= published
    else:
        assert {"soil_class_horizon_01.asc", "soil_class_horizon_03.asc"} <= published
        assert sum(name.startswith("lc_") and name.endswith(".asc") for name in published) == 3
