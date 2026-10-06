"""Focused tests for the QGIS-free elevation-band API."""
from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mhm_qgis.core.morphology import elevation_bands
from mhm_qgis.core.morphology.elevation_bands import bands, land_cover


def _reference(array, nodata=-9999):
    return {
        "array": np.asarray(array),
        "nodata": nodata,
        "geotransform": (0.0, 10.0, 0.0, 20.0, 0.0, -10.0),
        "projection": "",
        "rows": 2,
        "cols": 3,
    }


def test_elevation_range_rounds_outward_to_requested_width():
    assert elevation_bands.nice_step(13) == 20
    assert elevation_bands.elevation_range_from_window_width(121, 289, 50) == (
        100,
        300,
        4,
    )
    assert elevation_bands.elevation_range_from_window_width(1, 2, 0) is None


def test_create_elevation_bands_writes_rasters_and_summary(tmp_path, monkeypatch):
    dem = _reference([[100, 149, 150], [199, 200, -9999]])
    watershed = _reference([[1, 1, 1], [1, 0, 0]], nodata=0)
    references = {"dem.tif": dem, "4_watershed_alpha.tif": watershed}
    monkeypatch.setattr(
        bands,
        "_dependencies",
        lambda: (np, SimpleNamespace(GDT_Int16=3)),
    )
    monkeypatch.setattr(
        bands,
        "_read_raster",
        lambda path, **_kwargs: references[Path(path).name],
    )

    written = {}

    def write(path, array, _reference, **_kwargs):
        path = Path(path)
        path.write_bytes(b"raster")
        written[path.name] = array.copy()
        return path

    monkeypatch.setattr(bands, "_write_raster", write)
    result = elevation_bands.create_elevation_bands(
        "dem.tif",
        ["4_watershed_alpha.tif"],
        tmp_path,
        50,
    )

    assert result.count == 2
    assert result.rasters == (tmp_path / "elevation_bands_alpha.tif",)
    assert written["elevation_bands_alpha.tif"].tolist() == [
        [1, 1, 2],
        [2, 0, 0],
    ]
    with result.summary.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["area_cells"] for row in rows] == ["2", "2"]
    assert [row["area_m2"] for row in rows] == ["200.000000", "200.000000"]


def test_land_cover_report_uses_class_names(tmp_path, monkeypatch):
    pytest.importorskip("osgeo")
    band_path = tmp_path / "elevation_bands_alpha.tif"
    band_path.write_bytes(b"raster")
    band_reference = _reference([[1, 1, 2], [2, 0, 0]], nodata=0)
    land_cover_reference = _reference([[10, 20, 10], [20, -9999, -9999]])
    monkeypatch.setattr(
        land_cover,
        "_dependencies",
        lambda: (np, SimpleNamespace()),
    )
    monkeypatch.setattr(
        land_cover,
        "_read_raster",
        lambda path, **_kwargs: (
            band_reference
            if Path(path).name.startswith("elevation_bands_")
            else land_cover_reference
        ),
    )

    result = elevation_bands.create_band_land_cover_details(
        "land_cover.tif",
        tmp_path,
        class_names={10: "Forest", 20: "Grass land"},
    )

    with result.report.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert result.row_count == 2
    assert "land_cover_class_10_Forest_area" in rows[0]
    assert "land_cover_class_20_Grass_land_area" in rows[0]
    assert rows[0]["land_cover_class_10_Forest_area"] == "100.000000"
    assert rows[0]["land_cover_class_20_Grass_land_area"] == "100.000000"
