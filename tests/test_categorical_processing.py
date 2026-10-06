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
