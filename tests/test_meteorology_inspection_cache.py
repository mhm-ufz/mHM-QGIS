"""Reusing an unchanged meteorology inspection keeps project load responsive."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.meteorology.forcing import (
    MeteoFolderSpec,
)
from mhm_qgis.core.meteorology.forcing import cache as inspection_cache
from mhm_qgis.core.meteorology.forcing import (
    inspect_meteo_folder_cached,
    inspect_meteo_inputs_cached,
    inspection_fingerprint,
)

xr = pytest.importorskip("xarray")


def _era5(folder, month, days=2):
    """Write one hourly ERA5-Land style precipitation file."""
    folder.mkdir(parents=True, exist_ok=True)
    times = np.arange(
        np.datetime64("1980-01-01T00"),
        np.datetime64("1980-01-01T00") + np.timedelta64(24 * days, "h"),
        np.timedelta64(1, "h"),
    ) + np.timedelta64(days * (month - 1), "D")
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

    first = inspect_meteo_folder_cached(tmp_path, _spec(folder))
    assert len(first.files) == 2

    # A second call must not touch inspect_meteo_folder at all.
    def _fail(*_a, **_k):
        raise AssertionError("the inspection was repeated")

    monkeypatch.setattr(inspection_cache, "inspect_meteo_folder", _fail)
    logged = []
    second = inspect_meteo_folder_cached(
        tmp_path, _spec(folder), log=logged.append)

    assert second == first
    assert second.start_time == first.start_time


def test_a_new_file_invalidates_the_cached_inspection(tmp_path):
    folder = tmp_path / "pre"
    _era5(folder, 1)
    before, files_before = inspection_fingerprint(
        _spec(folder))
    inspect_meteo_folder_cached(tmp_path, _spec(folder))

    _era5(folder, 2)
    after, files_after = inspection_fingerprint(
        _spec(folder))
    assert after != before
    assert len(files_after) == len(files_before) + 1

    refreshed = inspect_meteo_folder_cached(
        tmp_path, _spec(folder))
    assert len(refreshed.files) == 2


def test_a_modified_file_invalidates_the_cached_inspection(tmp_path):
    folder = tmp_path / "pre"
    _era5(folder, 1)
    spec = _spec(folder)
    before, files = inspection_fingerprint(spec)

    os.utime(files[0], (1_000_000, 1_000_000))
    assert inspection_fingerprint(spec)[0] != before


def test_the_round_trip_preserves_every_metadata_field(tmp_path):
    folder = tmp_path / "pre"
    _era5(folder, 1)
    original = inspect_meteo_folder_cached(
        tmp_path, _spec(folder))
    restored = inspect_meteo_folder_cached(
        tmp_path, _spec(folder))

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
        None, _spec(folder))
    assert len(metadata.files) == 1



def _spec(folder):
    return MeteoFolderSpec("pre", folder, unit="m", temporal_resolution="Hourly").normalized()


def test_options_invalidate_cached_inspection(tmp_path):
    from dataclasses import replace
    _era5(tmp_path, 1)
    spec = _spec(tmp_path)
    first = inspection_fingerprint(spec)[0]
    for changed in (replace(spec, unit="mm"), replace(spec, temporal_resolution="Daily"),
                    replace(spec, cumulative=True, cumulative_resolution="Daily")):
        assert inspection_fingerprint(changed)[0] != first


def test_input_inspection_uses_the_folder_cache_for_each_selection(tmp_path, monkeypatch):
    calls = []
    def cached(project, spec, log=None):
        calls.append(spec)
        return spec.kind
    monkeypatch.setattr(inspection_cache, "inspect_meteo_folder_cached", cached)
    specs = [MeteoFolderSpec(kind, tmp_path / kind) for kind in ("pre", "tavg", "pet")]
    assert inspect_meteo_inputs_cached(tmp_path, specs) == {s.kind: s.kind for s in specs}
    assert calls == specs
