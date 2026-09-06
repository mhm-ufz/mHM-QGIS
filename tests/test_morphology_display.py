"""Tests for the compact morphology display registry."""

from pathlib import Path

from mhm_qgis.core.handlers.store.morphology import land_cover_periods, resolve_display_output
from mhm_qgis.core.handlers.state.nml_settings import update_section
from mhm_qgis.core.handlers.store.layout import ensure_project_structure, morph_folder
from mhm_qgis.core.handlers.store.paths import geometry_folder


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
    geometry = Path(geometry_folder(tmp_path))
    raw = geometry / "1_dem_filled.tif"
    crop = geometry / "1_dem_filled_crop.tif"
    masked = geometry / "1_dem_filled_masked.tif"
    assert resolve_display_output(tmp_path, "dem") is None
    for path in (raw, crop, masked):
        path.touch()
        assert resolve_display_output(tmp_path, "dem").path == path
    assert resolve_display_output(tmp_path, "unknown") is None


def test_domain_mask_prefers_vector_to_raster(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    watersheds = Path(geometry_folder(tmp_path)) / "Watersheds"
    watersheds.mkdir(exist_ok=True)
    raster = watersheds / "4_watershed_DEM.tif"
    raster.touch()
    assert resolve_display_output(tmp_path, "domain_mask").is_raster
    vector = watersheds / "4_watershed_merged_vector.shp"
    vector.touch()
    output = resolve_display_output(tmp_path, "domain_mask")
    assert output.path == vector
    assert not output.is_raster
