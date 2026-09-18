"""Tests for the period and horizon mHM-tools adapter calls."""
from __future__ import annotations

import sys
from types import ModuleType

from mhm_qgis.externals.mhm_tools_handler import (
    lai_time_step,
    prepare_land_cover_periods,
    prepare_lai_file,
    prepare_soil_horizons,
)


def _install_fake(monkeypatch, **functions):
    package = ModuleType("mhm_tools")
    pre = ModuleType("mhm_tools.pre")
    for name, function in functions.items():
        setattr(pre, name, function)
    package.pre = pre
    monkeypatch.setitem(sys.modules, "mhm_tools", package)
    monkeypatch.setitem(sys.modules, "mhm_tools.pre", pre)


def test_land_cover_period_adapter_uses_public_api(tmp_path, monkeypatch):
    calls = []

    # Signature copied from mhm_tools.pre.format_lc_periods, so a keyword the
    # adapter renames is a test failure rather than a runtime TypeError.
    def format_lc_periods(
        input_file,
        dem_file,
        output_path,
        lookup_table,
        mapping_field,
        class_field,
        output_type="nc",
        **kwargs,
    ):
        calls.append(dict(kwargs, input_file=input_file, output_path=output_path))
        output = output_path / "lc_periods.nc"
        output.write_bytes(b"nc")
        return (output,)

    _install_fake(monkeypatch, format_lc_periods=format_lc_periods)
    output = prepare_land_cover_periods(
        tmp_path / "format-data.csv",
        tmp_path / "dem.tif",
        tmp_path / "output",
        tmp_path / "lookup.csv",
        "source",
        "class",
        output_type="nc",
    )

    assert [path.name for path in output] == ["lc_periods.nc"]
    assert calls[0]["input_file"] == tmp_path / "format-data.csv"
    assert calls[0]["resampling"] == "auto"


def test_soil_horizon_adapter_returns_data_and_definition(tmp_path, monkeypatch):
    calls = []

    # Signature copied from mhm_tools.pre.format_soil_horizons.
    def format_soil_horizons(
        input_file, dem_file, output_path, output_type="nc", **kwargs
    ):
        calls.append(dict(kwargs, input_file=input_file, output_path=output_path))
        data = output_path / "soil_class.asc"
        definition = output_path / "soil_classdefinition.txt"
        data.write_bytes(b"asc")
        definition.write_text("definition", encoding="utf-8")
        return data, definition

    _install_fake(monkeypatch, format_soil_horizons=format_soil_horizons)
    data, definition = prepare_soil_horizons(
        tmp_path / "format-data.csv",
        tmp_path / "dem.tif",
        tmp_path / "output",
        output_type="asc",
    )

    assert data.name == "soil_class.asc"
    assert definition.name == "soil_classdefinition.txt"
    assert calls[0]["input_file"] == tmp_path / "format-data.csv"
    assert calls[0]["composition_step"] == 5.0
    assert calls[0]["bulk_density_step"] == 0.1


def test_lai_adapter_uses_the_mhm_tools_api(tmp_path, monkeypatch):
    calls = []
    format_lai = ModuleType("mhm_tools.pre.format_lai")

    def format_lai_netcdf_file(input_file, dem_file, output_file, **kwargs):
        calls.append((input_file, dem_file, output_file, kwargs))
        output_file.write_bytes(b"nc")
        return output_file

    format_lai.format_lai_netcdf_file = format_lai_netcdf_file
    format_lai.lai_time_step = lambda resolution: {
        "long-term-mean-monthly": 1
    }[resolution]
    monkeypatch.setitem(sys.modules, "mhm_tools.pre.format_lai", format_lai)

    output = prepare_lai_file(
        tmp_path / "source.nc",
        tmp_path / "dem.tif",
        tmp_path / "lai.nc",
        output_temporal_resolution="monthly",
        dem_crs="EPSG:4326",
    )

    assert output == tmp_path / "lai.nc"
    assert calls[0][3]["output_temporal_resolution"] == "monthly"
    assert calls[0][3]["dem_crs"] == "EPSG:4326"
    assert lai_time_step("long-term-mean-monthly") == 1
