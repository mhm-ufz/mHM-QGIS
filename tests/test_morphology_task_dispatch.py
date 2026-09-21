from __future__ import annotations

import os
import pytest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt.QtWidgets import QApplication, QPushButton  # noqa: E402

from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog  # noqa: E402
from mhm_qgis.core.executions.morphology import commands  # noqa: E402
from mhm_qgis.core.handlers.state.nml_settings import update_section  # noqa: E402
from mhm_qgis.core.handlers.store.layout import ensure_project_structure  # noqa: E402


_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QApplication.instance() or _APPLICATION or QApplication([])
    return _APPLICATION


def test_land_cover_action_uses_path_task_bridge():
    _app()
    dialog = MhmQgisDialog()
    button = QPushButton()
    calls = []
    dialog.morphology_tasks.start_categorical = (
        lambda kind, **options: calls.append((kind, options)) or True
    )

    dialog.run_background_processor_action(
        "Land Use", lambda: (_ for _ in ()).throw(AssertionError()), button
    )

    assert calls[0][0] == "lc"
    assert calls[0][1]["controls"] == (button,)
    dialog.close()


def test_execute_all_uses_managed_pipeline():
    _app()
    dialog = MhmQgisDialog()
    calls = []
    dialog.morphology_tasks.start_execute_all = lambda: calls.append(True) or True

    dialog.start_execute_all_morphology_processing()

    assert calls == [True]
    dialog.close()


def test_execute_all_completion_failure_restores_controls():
    _app()
    dialog = MhmQgisDialog()
    bridge = dialog.morphology_tasks
    states, finishes = [], []
    bridge.execute_all_active = True
    dialog.set_meteo_setup_controls_enabled = states.append

    def finish(_key, ok, message):
        if ok:
            raise RuntimeError("completion failed")
        finishes.append(message)

    dialog.finish_morphology_workflow = finish
    bridge._finish_execute_all()

    assert bridge.execute_all_active is False
    assert states == [True]
    assert finishes == ["completion failed"]
    dialog.close()


@pytest.mark.parametrize("action,variable", [
    ("Fill DEM", "filled_dem"), ("Slope", "slope"), ("Aspect", "aspect"),
    ("Flow Accumulation", "flow_accumulation"), ("Flow Direction", "flow_direction"),
])
def test_derivative_buttons_share_the_netcdf_command(action, variable):
    _app()
    dialog = MhmQgisDialog()
    calls = []
    dialog.morphology_tasks.start_dem_derivatives = lambda **options: calls.append(options) or True
    dialog.run_background_processor_action(action, lambda: None, QPushButton())
    assert calls[0]["load"] == variable
    dialog.close()


def test_saved_land_cover_and_soil_outputs_are_reusable(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    morph = tmp_path / "mhm-qgis/data/master/static/morph"
    land_cover = morph / "lc_2000_2010.asc"
    soil = morph / "soil_class.asc"
    definition = morph / "soil_classdefinition.txt"
    for path in (land_cover, soil, definition):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    update_section(
        tmp_path,
        "land_cover",
        {"scenes": [{"output_path": "data/master/static/morph/lc_2000_2010.asc"}]},
    )
    update_section(
        tmp_path,
        "soil",
        {
            "output_path": "data/master/static/morph/soil_class.asc",
            "classdefinition_path": (
                "data/master/static/morph/soil_classdefinition.txt"
            ),
        },
    )

    assert commands.categorical_outputs(tmp_path, "lc") == (land_cover,)
    assert commands.categorical_outputs(tmp_path, "soil") == (soil, definition)


def test_execute_all_stage_order_uses_saved_hydrology_inputs():
    """The LAI stage must be in the sequence the button actually runs.

    This used to scrape the source text of `start_execute_all` for its lambdas,
    because the order was only expressed as a list of closures. The order is
    data now, so assert on the data.
    """
    from mhm_qgis.core.executions.morphology import EXECUTE_ALL_STAGES

    order = [stage.command for stage in EXECUTE_ALL_STAGES]
    assert order == [
        "dem_derivatives", "gauge_position", "morph_mask", "categorical",
        "categorical", "categorical", "lai", "publication",
    ]
    assert "hydrology" not in order and "snap_points" not in order
    # The three categorical stages are lc, soil, geology -- in that order.
    assert [s.args[0] for s in EXECUTE_ALL_STAGES if s.command == "categorical"] == [
        "lc", "soil", "geology",
    ]


def test_lai_stage_is_skipped_without_a_netcdf_selection():
    _app()
    dialog = MhmQgisDialog()
    bridge = dialog.morphology_tasks
    submitted = []
    bridge.coordinator.submit = lambda *a, **k: submitted.append(a) or True
    dialog.categorical_input_mode = lambda _kind: ""
    done = []

    assert bridge.start_lai(done=done.append) is True
    assert submitted == []          # nothing queued
    assert done == [None]           # the pipeline still advances
    dialog.close()


def test_lai_stage_submits_a_background_task_when_selected(tmp_path, monkeypatch):
    from mhm_qgis.qt.objects import morphology_tasks

    _app()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    bridge = dialog.morphology_tasks
    submitted = []
    bridge.coordinator.submit = (
        lambda key, label, fn, **k: submitted.append((key, label, k)) or True
    )
    dialog.categorical_input_mode = lambda _kind: "NetCDF"
    dialog.lai_netcdf_config = lambda: {
        "input_path": "/tmp/lai.nc",
        "target_timestep": "Monthly Gridded Data",
    }
    monkeypatch.setattr(morphology_tasks.lai, "task_options", lambda *_a, **_k: {
        "source_path": "/tmp/lai.nc",
        "source_variable": None,
        "output_path": str(tmp_path / "staged" / "lai_dem.nc"),
        "filled_dem": "/tmp/dem.tif",
        "dem_crs": "EPSG:4326",
        "target_timestep": "Monthly Gridded Data",
    })

    assert bridge.start_lai() is True
    key, label, options = submitted[0]
    assert key == "execute-all-lai"
    assert "LAI" in label
    # It must run on the file resource and be cancellable like the other stages.
    assert options["resource"] == "morphology-files"
    assert options["task_aware"] is True
    dialog.close()


def test_unchanged_lai_inputs_reuse_the_staged_file(tmp_path, monkeypatch):
    """The 153 GiB staged LAI must not be regenerated when nothing changed."""
    from mhm_qgis.core.handlers.state.cache import fingerprint, store_payload

    _app()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    bridge = dialog.morphology_tasks
    submitted = []
    bridge.coordinator.submit = lambda *a, **k: submitted.append(a) or True

    source = tmp_path / "lai_src.nc"
    dem = tmp_path / "dem.tif"
    staged = tmp_path / "staged" / "lai_dem.nc"
    for path in (source, dem):
        path.write_text("x", encoding="utf-8")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("staged", encoding="utf-8")

    options = {
        "source_path": str(source),
        "source_variable": None,
        "output_path": str(staged),
        "filled_dem": str(dem),
        "dem_crs": "EPSG:4326",
        "target_timestep": "Monthly Gridded Data",
    }
    from mhm_qgis.qt.objects import morphology_tasks

    dialog.categorical_input_mode = lambda _kind: "NetCDF"
    dialog.lai_netcdf_config = lambda: {"input_path": str(source)}
    monkeypatch.setattr(
        morphology_tasks.lai, "task_options", lambda *_a, **_k: dict(options)
    )

    # Record the fingerprint the stage will compute for these inputs.
    digest = fingerprint(
        (options["source_path"], options["filled_dem"]),
        {
            "source_variable": None,
            "target_timestep": "Monthly Gridded Data",
            "method": "bilinear",
            "output_path": str(staged),
        },
    )
    store_payload(tmp_path, "stages", "lai", digest, {"outputs": [str(staged)]})

    done = []
    assert bridge.start_lai(done=done.append) is True
    assert submitted == []                       # no resample queued

    # Editing an input makes it newer than the output, which invalidates both
    # the fingerprint and the adoption heuristic, so the work is queued again.
    staged_mtime = staged.stat().st_mtime
    os.utime(source, (staged_mtime + 10, staged_mtime + 10))
    assert bridge.start_lai() is True
    assert len(submitted) == 1
    dialog.close()


def test_a_missing_staged_file_forces_the_lai_resample(tmp_path, monkeypatch):
    from mhm_qgis.core.handlers.state.cache import fingerprint, store_payload

    _app()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    bridge = dialog.morphology_tasks
    submitted = []
    bridge.coordinator.submit = lambda *a, **k: submitted.append(a) or True

    source = tmp_path / "lai_src.nc"
    dem = tmp_path / "dem.tif"
    for path in (source, dem):
        path.write_text("x", encoding="utf-8")
    staged = tmp_path / "staged" / "lai_dem.nc"      # recorded but deleted

    options = {
        "source_path": str(source), "source_variable": None,
        "output_path": str(staged), "filled_dem": str(dem),
        "dem_crs": "EPSG:4326",
        "target_timestep": "Long Term Mean Monthly Gridded Data",
    }
    from mhm_qgis.qt.objects import morphology_tasks

    dialog.categorical_input_mode = lambda _kind: "NetCDF"
    dialog.lai_netcdf_config = lambda: {"input_path": str(source)}
    monkeypatch.setattr(
        morphology_tasks.lai, "task_options", lambda *_a, **_k: dict(options)
    )
    digest = fingerprint(
        (options["source_path"], options["filled_dem"]),
        {
            "source_variable": None,
            "target_timestep": "Long Term Mean Monthly Gridded Data",
            "method": "bilinear",
            "output_path": str(staged),
        },
    )
    store_payload(tmp_path, "stages", "lai", digest, {"outputs": [str(staged)]})

    assert bridge.start_lai() is True
    assert len(submitted) == 1                   # deleted output -> rework
    dialog.close()


def test_geology_outputs_already_on_disk_are_adopted_not_rebuilt(tmp_path):
    """A project prepared before the cache existed must not rebuild geology."""
    _app()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    bridge = dialog.morphology_tasks
    submitted = []
    bridge.coordinator.submit = lambda *a, **k: submitted.append(a) or True
    messages = []
    dialog.log_message = messages.append

    inputs = {}
    for name in ("geology_input.shp", "lookup.csv"):
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        inputs[name] = path
    inputs["1_dem_filled.tif"] = bridge.filled_dem_path
    Path(bridge.filled_dem_path).parent.mkdir(parents=True, exist_ok=True)
    Path(bridge.filled_dem_path).write_text("x", encoding="utf-8")
    outputs = {}
    for name in ("3_geology_processed.tif", "geology_classdefinition.txt",
                 "geology_class_metadata.json"):
        path = tmp_path / name
        path.write_text("y", encoding="utf-8")
        outputs[name] = path
    # Outputs postdate every input.
    newest = max(Path(path).stat().st_mtime for path in inputs.values())
    for path in outputs.values():
        os.utime(path, (newest + 10, newest + 10))

    job = {
        "kind": "geology",
        "input_file": str(inputs["geology_input.shp"]),
        "filled_dem": str(inputs["1_dem_filled.tif"]),
        "lookup_table": str(inputs["lookup.csv"]),
        "output": str(outputs["3_geology_processed.tif"]),
        "definition": str(outputs["geology_classdefinition.txt"]),
        "metadata": str(outputs["geology_class_metadata.json"]),
        "mapping_field": "ID",
        "class_field": "CLASS",
        "is_vector": True,
        "input_crs": "EPSG:4326",
        "dem_crs": "EPSG:4326",
    }
    dialog.categorical_input_mode = lambda _kind: "lookup_table"
    dialog.uses_advanced_categorical_input = lambda _kind: False
    bridge._lookup_job = lambda _kind: dict(job)

    assert bridge.start_categorical("geology", reuse_existing=True) is True
    assert submitted == []                                  # nothing rebuilt
    assert any("Adopted the existing geology output" in m for m in messages)

    # The adoption is recorded, so the next run reuses without re-adopting.
    from mhm_qgis.core.handlers.state.cache import load_state

    assert "geology" in load_state(tmp_path)["stages"]
    messages.clear()
    assert bridge.start_categorical("geology", reuse_existing=True) is True
    assert submitted == []
    assert any("Reusing the prepared output" in m for m in messages)
    dialog.close()
