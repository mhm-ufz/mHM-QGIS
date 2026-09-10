"""Prepared meteorology forcing is reused only when it matches the target grid."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.handlers.store.layout import expected_meteo_outputs  # noqa: E402
from mhm_qgis.core.meteorology.reuse import (  # noqa: E402
    output_state_key,
    required_meteo_variables,
    stale_meteo_variables,
)

L2_HEADER = {
    "ncols": 111,
    "nrows": 51,
    "xllcorner": 78.99958333333333,
    "yllcorner": 25.999583333333334,
    "cellsize": 0.1,
    "nodata_value": -9999.0,
}
HEADER_TEXT = (
    "ncols         {ncols}\n"
    "nrows         {nrows}\n"
    "xllcorner     {xllcorner}\n"
    "yllcorner     {yllcorner}\n"
    "cellsize      {cellsize}\n"
    "NODATA_value  -9999.0\n"
)


def _prepare(project, variables, header=None):
    """Write forcing files and their processing-state entries."""
    expected = expected_meteo_outputs(project)
    outputs = {}
    for variable in variables:
        paths = expected[variable]
        paths["netcdf"].parent.mkdir(parents=True, exist_ok=True)
        paths["netcdf"].write_bytes(b"netcdf")
        paths["header"].write_text(
            HEADER_TEXT.format(**(header or L2_HEADER)), encoding="utf-8"
        )
        for path in (paths["netcdf"], paths["header"]):
            outputs[output_state_key(project, path)] = {"exists": True}
    return outputs


def test_required_variables_include_pet_only_when_it_is_selected():
    assert required_meteo_variables(False) == ("pre", "tavg", "tmin", "tmax")
    assert required_meteo_variables(True) == ("pre", "tavg", "tmin", "tmax", "pet")


def test_recorded_forcing_on_the_same_grid_is_reused(tmp_path):
    variables = required_meteo_variables(False)
    outputs = _prepare(tmp_path, variables)

    assert stale_meteo_variables(tmp_path, outputs, L2_HEADER, variables) == {}


def test_a_changed_l2_grid_forces_every_variable_to_rebuild(tmp_path):
    variables = required_meteo_variables(False)
    outputs = _prepare(tmp_path, variables)

    stale = stale_meteo_variables(
        tmp_path, outputs, {**L2_HEADER, "ncols": 112}, variables
    )
    assert sorted(stale) == sorted(variables)


def test_selecting_pet_rebuilds_only_the_missing_variable(tmp_path):
    outputs = _prepare(tmp_path, required_meteo_variables(False))

    stale = stale_meteo_variables(
        tmp_path, outputs, L2_HEADER, required_meteo_variables(True)
    )
    assert list(stale) == ["pet"]
    assert "not recorded as prepared" in stale["pet"]


def test_a_file_deleted_outside_the_plugin_is_rebuilt(tmp_path):
    variables = required_meteo_variables(False)
    outputs = _prepare(tmp_path, variables)
    Path(expected_meteo_outputs(tmp_path)["tavg"]["netcdf"]).unlink()

    stale = stale_meteo_variables(tmp_path, outputs, L2_HEADER, variables)
    assert list(stale) == ["tavg"]
    assert "missing on disk" in stale["tavg"]


def test_an_unrecorded_file_is_not_reused_even_when_it_exists(tmp_path):
    variables = required_meteo_variables(False)
    _prepare(tmp_path, variables)

    stale = stale_meteo_variables(tmp_path, {}, L2_HEADER, variables)
    assert sorted(stale) == sorted(variables)


@pytest.mark.parametrize("variable", ["pre", "tmin"])
def test_a_header_written_for_another_grid_is_rejected(tmp_path, variable):
    variables = required_meteo_variables(False)
    outputs = _prepare(tmp_path, variables)
    expected_meteo_outputs(tmp_path)[variable]["header"].write_text(
        HEADER_TEXT.format(**{**L2_HEADER, "cellsize": 0.0999996}),
        encoding="utf-8",
    )

    stale = stale_meteo_variables(tmp_path, outputs, L2_HEADER, variables)
    assert list(stale) == [variable]
