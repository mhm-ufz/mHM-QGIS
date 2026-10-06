"""The data model: what a run needs (rules.yml) and what it writes (outputs.yml)."""
import json

import pytest

from mhm_qgis.core import datamodel
from mhm_qgis.core.handlers.store import layout


def _project(tmp_path, version, **namelists):
    (tmp_path / ".temp").mkdir()
    (tmp_path / ".temp" / "mhm_qgis_input_state.json").write_text(json.dumps({"mhm_version": version}))
    layout.forget_project_version()
    for name, text in namelists.items():
        (tmp_path / f"{name}.nml").write_text(text)
    return tmp_path


def test_water_balance_needs_every_storage_and_the_sealed_fraction():
    needed = datamodel.required_outputs("v6", ["water_balance"])
    for output in ("precipitation", "aET", "Q", "interception", "snowpack", "SWC_L",
                   "sealedSTW", "unsatSTW", "satSTW", "sealed_fraction"):
        assert needed[output] == ["water_balance"]


def test_output_switch_keys_per_version():
    assert datamodel.output_switches("v6", ["runoff_partition"]) == {
        "out_QD": True, "out_QIf": True, "out_QIs": True, "out_QB": True}
    assert datamodel.switch_key("v6", "SWC_L") == "out_SWC"
    assert datamodel.switch_key("v6", "aET") == "out_aET_all"
    assert datamodel.switch_key("5.13", "satSTW") == "outputflxstate(8)"
    assert datamodel.switch_key("5.13", "discharge") is None
    assert datamodel.switch_key("5.13", "Qrouted") is None
    assert datamodel.switch_key("v6", "Qrouted") == "output_mrm.out_Qrouted"


def test_a_switched_off_output_and_an_inactive_process_are_reported(tmp_path):
    pytest.importorskip("nml_tools.gui.model")
    project = _project(
        tmp_path, "6",
        mhm="&config_processes\n  baseflow = 0\n/\n",
        outputs="&output_mhm\n  output_frequency = -1\n  out_satSTW = .false.\n/\n",
    )
    findings = {f.input: f for f in datamodel.check_outputs(project, ["water_balance"])}
    assert not findings["satSTW"].ok
    assert "enable output `satSTW` (out_satSTW)" in findings["satSTW"].message
    assert "baseflow must be case 1" in findings["satSTW"].message
    assert datamodel.output_step_days(project) == 1
    unavailable = datamodel.available_analyses(project, period_days=200)
    assert unavailable["water_balance"]
    assert "enable output `aET` (out_aET_all)" in unavailable["climatology"]
    assert "needs at least 365 days of output" in unavailable["climatology"]


def test_v513_output_step_counts_model_steps(tmp_path):
    pytest.importorskip("nml_tools.gui.model")
    project = _project(
        tmp_path, "5.13",
        mhm="&mainconfig_mhm_mrm\n  timestep = 24\n/\n",
        mhm_outputs="&NLoutputResults\n  timeStep_model_outputs = 2\n  outputFlxState(8) = .true.\n/\n",
    )
    assert datamodel.output_step_days(project) == 2
    assert "needs daily or finer output" in datamodel.available_analyses(project)["signatures"]


def _extra(version, processes):
    always = set(datamodel.required_inputs(version, {}))
    return set(datamodel.required_inputs(version, processes)) - always


@pytest.mark.parametrize(("case", "inputs"), [
    (-2, {"pet"}),
    (-1, {"pet", "lai_class|lai_gridded"}),
    (1, {"tavg", "tmin", "tmax"}),
    (2, {"tavg", "netrad", "lai_class|lai_gridded"}),
    (3, {"tavg", "netrad", "eabs", "wind", "lai_class|lai_gridded"}),
])
def test_v6_pet_cases_read_their_forcing(case, inputs):
    assert _extra("6", {"pet": case}) == inputs


@pytest.mark.parametrize(("case", "inputs"), [
    (0, {"pet"}), (1, {"tmin", "tmax"}), (2, {"netrad"}), (3, {"netrad", "eabs", "wind"}),
])
def test_v513_reads_tavg_lai_and_latlon_every_run(case, inputs):
    assert {"pre", "tavg", "dem", "latlon", "lai_class|lai_gridded"} <= set(datamodel.required_inputs("5.13", {}))
    assert _extra("5.13", {"pet": case}) == inputs


def test_process_dependencies_and_unavailable_cases_are_reported():
    messages = datamodel.unmet_needs("6", {"temperature_routing": 1, "routing": 0, "pet": 0, "interception": 1})
    assert any("temperature routing case 1 needs routing case 2 or 3" in m for m in messages)
    assert any("interception case 1 needs PET" in m for m in messages)
    assert datamodel.unmet_needs("6", {"routing": 1}) == [
        "routing case 1 is not available in mHM 6 (valid: 0, 2, 3)."]
    assert datamodel.unmet_needs("5.13", {"routing": 1}) == []


def test_feasible_cases_and_meteo_variables():
    assert datamodel.feasible_cases("6", {"pet"})["pet"] == [-2, 0]
    rich = {"pet", "tavg", "tmin", "tmax", "netrad", "eabs", "wind", "lai_class"}
    assert datamodel.feasible_cases("6", rich)["pet"] == [-2, -1, 0, 1, 2, 3]
    assert datamodel.meteo_variables("6", {"pet": 3}) == ["pre", "tavg", "netrad", "eabs", "windspeed"]


def test_v513_process_cases_come_from_processcase_indices(tmp_path):
    pytest.importorskip("nml_tools.gui.model")
    project = _project(tmp_path, "5.13", mhm=(
        "&processSelection\n  processCase = 1, 1, 1, 1, 0, 1, 1, 3, 1, 0, 0\n/\n"))
    processes = datamodel.current_processes(project)
    assert processes["pet"] == 0
    assert processes["routing"] == 3
    assert processes["baseflow"] == 1
    assert processes["temperature_routing"] == 0


def test_check_reports_a_missing_forcing_with_its_reason(tmp_path):
    project = _project(tmp_path, "6")
    findings = {f.input: f for f in datamodel.check(project, {"pet": 1})}
    assert not findings["tmin"].ok
    assert findings["tmin"].message == (
        "Missing tmin (Minimum air temperature), needed by PET case 1 (Hargreaves-Samani). "
        "Meteorology tab: add variable tmin.")
    (project / "data" / "meteo").mkdir(parents=True)
    (project / "data" / "meteo" / "tmin.nc").write_bytes(b"")
    assert {f.input: f for f in datamodel.check(project, {"pet": 1})}["tmin"].ok
