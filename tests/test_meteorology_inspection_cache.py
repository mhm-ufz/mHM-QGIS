"""Reusing an unchanged meteorology inspection keeps project load responsive."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.meteorology.forcing import cache as inspection_cache  # noqa: E402
from mhm_qgis.core.meteorology.forcing import (  # noqa: E402
    inspect_meteo_folder_cached,
    inspect_meteo_inputs_cached,
    inspection_fingerprint,
)
from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec  # noqa: E402

xr = pytest.importorskip("xarray")


def _era5(folder, month, days=2):
    """Write one hourly ERA5-Land style precipitation file."""
    folder.mkdir(parents=True, exist_ok=True)
    times = np.arange(
        np.datetime64("1980-01-01T00"),
        np.datetime64("1980-01-01T00") + np.timedelta64(24 * days, "h"),
        np.timedelta64(1, "h"),
    ) + np.timedelta64(31 * (month - 1), "D")
    xr.DataArray(
        np.zeros((times.size, 3, 4), dtype="float32"),
        dims=("valid_time", "latitude", "longitude"),
        coords={
            "valid_time": times,
            "latitude": [31.0, 30.5, 30.0],
            "longitude": [79.0, 79.5, 80.0, 80.5],
        },
        name="tp",
    ).to_dataset().to_netcdf(
        folder / f"ERA5_Land_total_precipitation_1980_{month:02d}.nc"
    )


def test_an_unchanged_folder_is_not_reopened(tmp_path, monkeypatch):
    folder = tmp_path / "pre"
    for month in (1, 2):
        _era5(folder, month)

    first = inspect_meteo_folder_cached(tmp_path, folder, "precipitation", "ERA5Land")
    assert len(first.files) == 2

    # A second call must not touch inspect_meteo_folder at all.
    def _fail(*_a, **_k):
        raise AssertionError("the inspection was repeated")

    monkeypatch.setattr(inspection_cache, "inspect_meteo_folder", _fail)
    logged = []
    second = inspect_meteo_folder_cached(
        tmp_path, folder, "precipitation", "ERA5Land", log=logged.append)

    assert second == first
    assert any("reused" in message for message in logged)


def test_a_new_file_invalidates_the_cached_inspection(tmp_path):
    folder = tmp_path / "pre"
    _era5(folder, 1)
    before, files_before = inspection_fingerprint(
        MeteoFolderSpec("precipitation", folder, "ERA5Land", None).normalized())
    inspect_meteo_folder_cached(tmp_path, folder, "precipitation", "ERA5Land")

    _era5(folder, 2)
    after, files_after = inspection_fingerprint(
        MeteoFolderSpec("precipitation", folder, "ERA5Land", None).normalized())
    assert after != before
    assert len(files_after) == len(files_before) + 1

    refreshed = inspect_meteo_folder_cached(
        tmp_path, folder, "precipitation", "ERA5Land")
    assert len(refreshed.files) == 2


def test_a_modified_file_invalidates_the_cached_inspection(tmp_path):
    folder = tmp_path / "pre"
    _era5(folder, 1)
    spec = MeteoFolderSpec("precipitation", folder, "ERA5Land", None).normalized()
    before, files = inspection_fingerprint(spec)

    os.utime(files[0], (1_000_000, 1_000_000))
    assert inspection_fingerprint(spec)[0] != before


def test_the_round_trip_preserves_every_metadata_field(tmp_path):
    folder = tmp_path / "pre"
    _era5(folder, 1)
    original = inspect_meteo_folder_cached(
        tmp_path, folder, "precipitation", "ERA5Land")
    restored = inspect_meteo_folder_cached(
        tmp_path, folder, "precipitation", "ERA5Land")

    for field in (
            "kind", "source", "shape", "x_coordinate", "y_coordinate",
            "x_resolution", "y_resolution", "resolution", "bounds", "crs",
            "unit"):
        assert getattr(restored, field) == getattr(original, field), field
    assert restored.files == original.files


def test_inspection_without_a_project_folder_still_works(tmp_path):
    folder = tmp_path / "pre"
    _era5(folder, 1)
    metadata = inspect_meteo_folder_cached(
        None, folder, "precipitation", "ERA5Land")
    assert len(metadata.files) == 1


def test_input_inspection_uses_the_folder_cache_for_each_selection(
        tmp_path, monkeypatch):
    calls = []

    def _cached(project, folder, kind, source, crs, log=None):
        calls.append((project, folder, kind, source, crs, log))
        return kind

    monkeypatch.setattr(
        inspection_cache, "inspect_meteo_folder_cached", _cached)
    log = object()
    result = inspect_meteo_inputs_cached(
        tmp_path,
        MeteoFolderSpec("precipitation", tmp_path / "pre", "ERA5Land"),
        MeteoFolderSpec("temperature", tmp_path / "temp", "mHM ready", "EPSG:4326"),
        MeteoFolderSpec("pet", tmp_path / "pet", "ERA5Land"),
        log=log,
    )

    assert result == {
        "precipitation": "precipitation",
        "temperature": "temperature",
        "pet": "pet",
    }
    assert [call[2] for call in calls] == ["precipitation", "temperature", "pet"]
    assert all(call[-1] is log for call in calls)
