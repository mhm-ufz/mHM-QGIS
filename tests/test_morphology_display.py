"""Tests for the compact morphology display registry."""

from pathlib import Path

from mhm_qgis.core.handlers.state.nml_settings import update_section
from mhm_qgis.core.handlers.store.layout import ensure_project_structure, morph_folder
from mhm_qgis.core.handlers.store.morphology import (
    land_cover_periods,
    resolve_display_output,
)
from mhm_qgis.core.handlers.store.paths import temp_morphology_folder


def test_historical_land_cover_resolves_the_selected_period(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    morph = Path(morph_folder(tmp_path))
    first = morph / "lc_1990_1999.asc"
    second = morph / "lc_2000_2010.asc"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    update_section(
        tmp_path,
        "land_cover",
        {
            "scenes": [
                {
                    "start_year": 1990,
                    "end_year": 1999,
                    "output_path": "data/static/morph/lc_1990_1999.asc",
                },
                {
                    "start_year": 2000,
                    "end_year": 2010,
                    "output_path": "data/static/morph/lc_2000_2010.asc",
                },
            ]
        },
    )

    assert len(land_cover_periods(tmp_path)) == 2
    assert resolve_display_output(
        tmp_path, "land_cover", year=2005
    ).path == second


def test_display_prefers_masked_then_cropped_then_raw(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    geometry = Path(temp_morphology_folder(tmp_path))
    raw = geometry / "dem_derivatives.nc"
    crop = geometry / "dem_derivatives_crop.nc"
    masked = geometry / "dem_derivatives_masked.nc"
    assert resolve_display_output(tmp_path, "dem") is None
    from netCDF4 import Dataset

    for path in (raw, crop, masked):
        variable = "dem_filled" if path == raw else "dem"
        with Dataset(path, "w") as dataset:
            dataset.createDimension("x", 2)
            dataset.createDimension("y", 2)
            dataset.createVariable(variable, "f8", ("y", "x"))
        output = resolve_display_output(tmp_path, "dem")
        assert output.path == path
        assert output.variable == variable
    assert resolve_display_output(tmp_path, "unknown") is None


def test_domain_mask_prefers_vector_to_raster(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    watersheds = Path(temp_morphology_folder(tmp_path)) / "Watersheds"
    watersheds.mkdir(exist_ok=True)
    raster = watersheds / "4_watershed_DEM.tif"
    raster.touch()
    assert resolve_display_output(tmp_path, "domain_mask").is_raster
    vector = watersheds / "4_watershed_merged_vector.shp"
    vector.touch()
    output = resolve_display_output(tmp_path, "domain_mask")
    assert output.path == vector
    assert not output.is_raster


def test_published_classes_win_over_configured_raw_inputs(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    morph = Path(morph_folder(tmp_path))
    geometry = Path(temp_morphology_folder(tmp_path))
    for key, raw_name, final_name in (
        ("soil", "3_soil.tif", "soil_class.asc"),
        ("land_cover", "3_land_use.tif", "lc.asc"),
    ):
        raw = geometry / raw_name
        raw.touch()
        update_section(tmp_path, key, {"output_path": str(raw)})
        published = morph / final_name
        published.touch()
        assert resolve_display_output(tmp_path, key).path == published
