"""Focused tests for the public morphology execution plans."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mhm_qgis.core.executions import morphology
from mhm_qgis.core.executions.morphology import setup
from mhm_qgis.core.handlers.state import processing
from mhm_qgis.core.handlers.store.layout import input_state_path


HEADERS = {
    level: {
        "ncols": 6,
        "nrows": 4,
        "xllcorner": 0.0,
        "yllcorner": 0.0,
        "cellsize": cellsize,
    }
    for level, cellsize in (("L0", 1.0), ("L1", 2.0), ("L11", 2.0), ("L2", 3.0))
}


def _request(tmp_path, workflow="meteo_morph_setup"):
    return setup.SetupRequest(
        project_folder=str(tmp_path), headers=HEADERS, workflow=workflow
    )


def _recording_commands(monkeypatch, *, fail=""):
    calls = []

    def command(name):
        def run(_request, *, log=None):
            calls.append(name)
            if name == fail:
                raise RuntimeError(f"{name} failed")
            return ()

        return run

    monkeypatch.setattr(
        setup,
        "_COMMANDS",
        {stage.command: command(stage.command) for stage in morphology.MORPH_SETUP_STAGES},
    )
    return calls


def test_combined_setup_runs_the_public_plan_in_order(tmp_path, monkeypatch):
    calls = _recording_commands(monkeypatch)
    messages = []

    assert setup.run(_request(tmp_path), log=messages.append)

    # Publication must precede per-domain DEM creation because it supplies the
    # shared morphology files linked into each domain.
    assert calls == [
        "crop",
        "mask",
        "latlon",
        "write",
        "publish",
        "link_domains",
        "domain_dems",
    ]
    assert calls.index("publish") < calls.index("link_domains") < calls.index("domain_dems")
    assert processing.workflow(tmp_path, "meteo_morph_setup")["status"] == "completed"


def test_each_setup_command_records_its_own_completed_status(tmp_path, monkeypatch):
    _recording_commands(monkeypatch)

    setup.run(_request(tmp_path))

    workflows = processing.section(tmp_path, "workflows", {})
    expected = [
        f"meteo_morph_setup_{stage.command}"
        for stage in morphology.MORPH_SETUP_STAGES
    ]
    assert [name for name in expected if name in workflows] == expected
    assert all(workflows[name]["status"] == "completed" for name in expected)


def test_combined_setup_stops_at_the_first_failed_command(tmp_path, monkeypatch):
    calls = _recording_commands(monkeypatch, fail="latlon")

    with pytest.raises(RuntimeError, match="latlon failed"):
        setup.run(_request(tmp_path))

    assert calls == ["crop", "mask", "latlon"]
    workflow = processing.workflow(tmp_path, "meteo_morph_setup")
    assert workflow["status"] == "failed"
    assert "latlon failed" in workflow["message"]
    assert processing.workflow(tmp_path, "meteo_morph_setup_write") == {}


def test_v6_plan_omits_the_v5_only_latlon_command(tmp_path, monkeypatch):
    path = Path(input_state_path(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mhm_version": "6"}), encoding="utf-8")
    calls = _recording_commands(monkeypatch)

    setup.run(_request(tmp_path))

    assert "latlon" not in calls
    assert calls == ["crop", "mask", "write", "publish", "link_domains", "domain_dems"]


def test_execute_all_plan_contains_every_user_visible_command():
    stages = morphology.EXECUTE_ALL_STAGES
    assert [stage.command for stage in stages] == [
        "dem_derivatives",
        "categorical",
        "categorical",
        "categorical",
        "lai",
        "hydrology",
        "snap_points",
        "gauge_position",
    ]
    assert [stage.args for stage in stages if stage.command == "categorical"] == [
        ("lc",),
        ("soil",),
        ("geology",),
    ]
