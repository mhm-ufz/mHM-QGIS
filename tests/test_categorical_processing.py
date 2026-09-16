"""Focused tests for the path-only categorical morphology API."""
from __future__ import annotations

from pathlib import Path

import pytest

from mhm_qgis.core.handlers.state.nml_settings import load_settings, update_section
from mhm_qgis.core.handlers.store.layout import ensure_project_structure, morph_folder
from mhm_qgis.core.morphology.layers import categorical
from mhm_qgis.core.handlers.store.paths import morph_staging_folder


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

    target = Path(morph_staging_folder(tmp_path)) / "lc.asc"
    assert target.read_bytes() == b"new raster"
    assert target.with_suffix(".prj").read_bytes() == b"projection"
    assert set(outputs) == {target, target.with_suffix(".prj")}
    assert all(path.exists() for path in stale)
    assert load_settings(tmp_path)["land_cover"]["output_path"] == (
        "Z Temp/Morphology/morph/lc.asc"
    )


def test_ready_soil_requires_and_publishes_its_class_definition(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    source = _touch(tmp_path / "input" / "soil.tif", b"soil")

    with pytest.raises(FileNotFoundError, match="class-definition"):
        categorical.copy_ready(tmp_path, "soil", source)

    definition = _touch(tmp_path / "input" / "soil_classes.txt", b"classes")
    outputs = categorical.copy_ready(
        tmp_path, "soil", source, definition_source=definition
    )

    target = Path(morph_staging_folder(tmp_path)) / "soil_class.tif"
    target_definition = target.parent / "soil_classdefinition.txt"
    assert set(outputs) == {target, target_definition}
    assert target.read_bytes() == b"soil"
    assert target_definition.read_bytes() == b"classes"


def test_ready_mode_rejects_non_raster_input(tmp_path):
    source = _touch(tmp_path / "soil.shp")

    with pytest.raises(ValueError, match="ASC, NetCDF, or TIFF"):
        categorical.copy_ready(tmp_path, "soil", source)


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
            "output_path": "data/master/static/morph/soil_class.asc",
            "classdefinition_path": (
                "data/master/static/morph/soil_classdefinition.txt"
            ),
        },
    )

    assert categorical.saved_outputs(tmp_path, "soil") == ()
    _touch(definition)
    assert categorical.saved_outputs(tmp_path, "soil") == (output, definition)
