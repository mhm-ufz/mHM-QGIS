"""Tests for historical land-use and soil-horizon manifest records."""
from __future__ import annotations

import csv

import pytest

from mhm_qgis.core.handlers.lookup import (
    LandUseInput,
    LandUsePeriod,
    SoilHorizon,
    SoilInput,
    write_land_use_manifest,
    write_soil_manifest,
)


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"raster")
    return path


def test_writes_land_use_period_manifest(tmp_path):
    lookup = _touch(tmp_path / "lookup.csv")
    first = _touch(tmp_path / "lc_1990.tif")
    second = _touch(tmp_path / "lc_2000.tif")
    value = LandUseInput(
        (
            LandUsePeriod(1990, 1999, first),
            LandUsePeriod(2000, 2010, second),
        ),
        lookup,
        "source",
        "class",
    )

    output = write_land_use_manifest(value, tmp_path / "format-data.csv")

    with output.open(newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [
            ["StartDateTime", "EndDateTime", "FilePath"],
            ["1990-01-01T00:00:00", "2000-01-01T00:00:00", "lc_1990.tif"],
            ["2000-01-01T00:00:00", "2011-01-01T00:00:00", "lc_2000.tif"],
        ]


def test_land_use_periods_must_be_continuous(tmp_path):
    value = LandUseInput(
        (
            LandUsePeriod(1990, 1999, _touch(tmp_path / "a.tif")),
            LandUsePeriod(2001, 2010, _touch(tmp_path / "b.tif")),
        ),
        _touch(tmp_path / "lookup.csv"),
        "source",
        "class",
    )

    with pytest.raises(ValueError, match="gap-free"):
        write_land_use_manifest(value, tmp_path / "format-data.csv")


def test_datetime_and_year_boundaries_are_continuous_and_simulation_bounded(tmp_path):
    from mhm_qgis.core.handlers.lookup import validate_land_use_input
    raster, lookup = _touch(tmp_path / "a.tif"), _touch(tmp_path / "lookup.csv")
    value = LandUseInput((LandUsePeriod("2000", "2000-06-01T12:00", raster),
                          LandUsePeriod("2000-06-01T12:00", "2001", raster)),
                         lookup, "source", "class")
    validate_land_use_input(value, simulation_start="2000", simulation_end="2001", version="6")
    assert value.periods[0].end_time == value.periods[1].start_time
    manifest = write_land_use_manifest(value, tmp_path / "dates.csv").read_text()
    assert "2000-06-01T12:00:00" in manifest
    validate_land_use_input(value, version="5.13")
    with pytest.raises(ValueError, match="before the simulation"):
        validate_land_use_input(value, simulation_start="2000-02-01")
    with pytest.raises(ValueError, match="after the simulation"):
        validate_land_use_input(value, simulation_end="2000-12-31")


def test_writes_soil_unit_column_and_horizons(tmp_path):
    files = [_touch(tmp_path / f"layer_{index}.tif") for index in range(8)]
    value = SoilInput(
        (
            SoilHorizon(1, 0, 50, *files[:4]),
            SoilHorizon(2, 50, 200, *files[4:]),
        ),
        "kg/m3",
    )

    output = write_soil_manifest(value, tmp_path / "format-data.csv")
    lines = output.read_text(encoding="utf-8").splitlines()

    # No preamble: mHM-tools reads the manifest with a plain read_csv, so the
    # header must be line 0 and the unit repeats on every row.
    assert lines[0] == (
        "Horizon,Upper Depth,Lower Depth,Clay Layer,Sand Layer,"
        "Silt Layer,Bulk Density Layer,Bulk Density Unit"
    )
    assert lines[1] == (
        "1,0,50,layer_0.tif,layer_1.tif,layer_2.tif,layer_3.tif,kg/m3"
    )
    assert lines[2] == (
        "2,50,200,layer_4.tif,layer_5.tif,layer_6.tif,layer_7.tif,kg/m3"
    )
    assert len(lines) == 3


def test_soil_horizons_must_start_at_zero_and_be_contiguous(tmp_path):
    files = [_touch(tmp_path / f"layer_{index}.tif") for index in range(4)]

    with pytest.raises(ValueError, match="start at depth 0"):
        write_soil_manifest(
            SoilInput((SoilHorizon(1, 10, 50, *files),), "g/cm3"),
            tmp_path / "format-data.csv",
        )


def test_soil_state_exposes_lower_depths(tmp_path):
    from mhm_qgis.core.native_worker import _soil

    files = [_touch(tmp_path / f"layer_{index}.tif") for index in range(4)]
    value = SoilInput((SoilHorizon(1, 0, 300, *files),), "g/cm3")

    assert value.lower_depths == (300,)
    assert value.as_dict()["lower_depths"] == [300]
    saved = value.as_dict()
    del saved["composition_step"], saved["bulk_density_step"]
    assert _soil(saved) == value


@pytest.mark.parametrize("field", ["composition_step", "bulk_density_step"])
@pytest.mark.parametrize("step", [0, -1, float("nan"), float("inf")])
def test_soil_grouping_steps_must_be_positive_and_finite(field, step):
    from mhm_qgis.core.handlers.lookup import validate_soil_input

    with pytest.raises(ValueError, match="step must be a positive finite number"):
        validate_soil_input(SoilInput((), "g/cm3", **{field: step}))
