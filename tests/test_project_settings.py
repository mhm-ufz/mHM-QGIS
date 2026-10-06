import pytest

from mhm_qgis.core.grid.distance import local_distance_crs
from mhm_qgis.core.handlers.state import settings
from mhm_qgis.core.handlers.store.layout import ensure_project_structure


def test_settings_are_copied_once_and_reloaded_per_project(tmp_path):
    ensure_project_structure(tmp_path)
    path = tmp_path / "settings.yaml"
    assert path.read_bytes() == settings.PACKAGED_SETTINGS.read_bytes()
    assert settings.read(tmp_path)["max_snap_buffer_distance"] == 1000
    path.write_text("max_snap_buffer_distance: 250 # metres\ngrid_alignment_gap_limit: 0.02\n")
    ensure_project_structure(tmp_path)
    assert settings.read(tmp_path) == {"max_snap_buffer_distance": 250, "grid_alignment_gap_limit": 0.02}
    assert settings.read(tmp_path / "another")["max_snap_buffer_distance"] == 1000
    for invalid in ("-1", "nan", "inf", "wrong", ""):
        path.write_text(f"max_snap_buffer_distance: {invalid}\n")
        with pytest.raises(ValueError, match="max_snap_buffer_distance"):
            settings.read(tmp_path)


def test_distance_crs_handles_latitude_and_projected_feet():
    from pyproj import Transformer

    text, factor = local_distance_crs("EPSG:4326", 10, 60)
    assert factor == 1
    x, y = Transformer.from_crs("EPSG:4326", text, always_xy=True).transform(10.01, 60)
    assert (x*x + y*y)**0.5 == pytest.approx(558, abs=1)
    _, factor = local_distance_crs("EPSG:2263", 1000000, 200000)
    assert factor == pytest.approx(0.3048006096)
