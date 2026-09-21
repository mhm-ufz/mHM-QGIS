"""Tests for the project-local processing-state API."""
from __future__ import annotations

from pathlib import Path

from mhm_qgis.core.handlers.state import processing
from mhm_qgis.core.handlers.state.meteo_outputs import MeteorologyOutputState
from mhm_qgis.core.handlers.store.layout import workspace_folder


def test_processing_state_is_saved_in_workspace(tmp_path: Path) -> None:
    expected = Path(workspace_folder(tmp_path)) / processing.STATE_FILENAME

    assert processing.state_path(tmp_path) == expected
    processing.overlay(tmp_path, {"version": processing.STATE_VERSION})

    assert expected.is_file()


def test_meteorology_state_and_output_keys_use_workspace(tmp_path: Path) -> None:
    state = MeteorologyOutputState(tmp_path)
    workspace = Path(workspace_folder(tmp_path))
    output = workspace / "data" / "meteo" / "pre" / "pre.nc"

    assert state.state_path() == processing.state_path(tmp_path)
    assert state.output_key(output) == "data/meteo/pre/pre.nc"
    state.save({"version": 1, "outputs": {}})

    assert state.state_path().is_file()


def test_grid_contract_is_persisted_and_revalidated_on_resume(tmp_path: Path) -> None:
    from mhm_qgis.core.grid import aligned_l0_l2_headers

    l0_header, l2_header = aligned_l0_l2_headers(
        (99.0, 191.0, 199.0, 291.0),
        {"xllcorner": 100.0, "yllcorner": 200.0, "cellsize": 10.0, "unit": "m"},
        multiplier=3,
        unit="m",
    )

    saved = processing.save_grid(tmp_path, l0_header, l2_header, 3)
    contract = processing.saved_grid(tmp_path)

    assert saved["l2_ratio_to_l0"] == 3
    assert contract["l0_header"] == l0_header
    assert contract["l2_header"] == l2_header
    assert contract["l2_ratio_to_l0"] == 3


def test_an_inconsistent_saved_grid_contract_is_discarded(tmp_path: Path) -> None:
    processing.overlay(
        tmp_path,
        {
            "grid": {
                "l0_header": {
                    "ncols": 9,
                    "nrows": 9,
                    "xllcorner": 70.0,
                    "yllcorner": 170.0,
                    "cellsize": 10.0,
                },
                "l2_header": {
                    "ncols": 5,
                    "nrows": 3,
                    "xllcorner": 70.0,
                    "yllcorner": 170.0,
                    "cellsize": 30.0,
                },
                "l2_ratio_to_l0": 3,
            }
        },
    )
    messages = []

    assert processing.saved_grid(tmp_path, log=messages.append) is None
    assert any("grid contract" in message for message in messages)


def test_workflow_updates_preserve_sections_other_writers_own(tmp_path: Path) -> None:
    from mhm_qgis.core.handlers.state.cache import cached_payload, store_payload

    processing.mark_workflow(tmp_path, "execute_all_morphology", "running")
    store_payload(tmp_path, "stages", "geology", "d1", {"outputs": ["g.tif"]})
    store_payload(tmp_path, "meteo_inspection", "pre", "d2", {"files": []})

    processing.mark_workflow(tmp_path, "execute_all_morphology", "completed")

    assert cached_payload(tmp_path, "stages", "geology", "d1") == {
        "outputs": ["g.tif"]
    }
    assert cached_payload(tmp_path, "meteo_inspection", "pre", "d2") == {
        "files": []
    }
    assert processing.workflow(tmp_path, "execute_all_morphology")["status"] == "completed"


def test_a_corrupt_state_file_does_not_lose_the_new_write(tmp_path: Path) -> None:
    path = processing.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    processing.mark_workflow(tmp_path, "execute_all_morphology", "completed")

    assert processing.workflow(tmp_path, "execute_all_morphology")["status"] == "completed"
