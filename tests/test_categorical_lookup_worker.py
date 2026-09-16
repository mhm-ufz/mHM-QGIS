"""Categorical lookup work must run outside QGIS so an OOM cannot kill it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



REPO = Path(__file__).resolve().parent.parent


def _job(work):
    return {
        "mode": "lookup",
        "kind": "geology",
        "job": {
            "kind": "geology",
            "geometry": str(work),
            "input_file": str(work / "missing.shp"),
            "filled_dem": str(work / "missing.tif"),
            "output": str(work / "3_geology_processed.tif"),
            "definition": str(work / "geology_classdefinition.txt"),
            "metadata": str(work / "geology_class_metadata.json"),
            "lookup_table": str(work / "missing.csv"),
            "mapping_field": "ID",
            "class_field": "CLASS",
            "is_vector": True,
            "input_crs": "EPSG:4326",
            "dem_crs": "EPSG:4326",
            "removals": [],
        },
    }


def _run_worker(work, payload):
    job_file, result_file = work / "job.json", work / "result.json"
    job_file.write_text(json.dumps(payload), encoding="utf-8")
    process = subprocess.run(
        [sys.executable, "-m", "mhm_qgis.core.native_worker",
         str(job_file), str(result_file)],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")),
    )
    return process, result_file


def test_the_lookup_module_imports_without_qgis():
    """It runs in a child process, where `processing` is unavailable."""
    process = subprocess.run(
        [sys.executable, "-c",
         "import mhm_qgis.core.handlers.lookup.job as m; print(m.run_lookup_job.__name__)"],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")),
    )
    assert process.returncode == 0, process.stderr
    assert "run_lookup_job" in process.stdout


def test_relocated_worker_imports_without_gui_dependencies():
    process = subprocess.run(
        [sys.executable, "-c",
         "import sys; "
         "sys.modules.update({name: None for name in ('qgis', 'PyQt5', 'PyQt6', 'PySide6', 'mhm_qgis.qt')}); "
         "from mhm_qgis.core.native_worker import run; assert callable(run); "
         "from mhm_qgis.core.handlers.store.morphology import resolve_display_output; "
         "assert callable(resolve_display_output)"],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")),
    )
    assert process.returncode == 0, process.stderr
    assert not (REPO / "src/mhm_qgis/native_worker.py").exists()
    assert not (REPO / "src/mhm_qgis/qt/threads/native_worker.py").exists()
    assert not (REPO / "src/mhm_qgis/task_coordinator.py").exists()


def test_native_command_launches_the_relocated_worker(tmp_path):
    from unittest.mock import Mock
    from mhm_qgis.core.executions.morphology.commands import native_worker

    task = Mock()
    task.isCanceled.return_value = False
    with pytest.raises(RuntimeError, match="Unsupported native worker kind"):
        native_worker(task, {"kind": "nonsense", "project_folder": str(tmp_path),
                             "version": "5.13"}, tmp_path)
    task.setProgress.assert_called_once_with(5)
    assert not list(tmp_path.iterdir())


def test_the_worker_dispatches_a_lookup_job_and_reports_failure_as_data(tmp_path):
    process, result_file = _run_worker(tmp_path, _job(tmp_path))

    # A bad input must come back as a recorded error, never as a crash.
    assert process.returncode == 1
    assert result_file.is_file()
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert "error" in payload
    assert "run_lookup_job" in payload["traceback"]


def test_the_worker_asks_the_kernel_to_kill_it_before_qgis():
    """Geology formatting peaked at 5.5 GiB and OOM-killed the QGIS process."""
    process = subprocess.run(
        [sys.executable, "-c",
         "from mhm_qgis.core.native_worker import _prefer_worker_termination as f; f();"
         "print(open('/proc/self/oom_score_adj').read().strip())"],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")),
    )
    assert process.returncode == 0, process.stderr
    assert int(process.stdout.strip()) == 500


def test_an_unknown_worker_mode_is_rejected(tmp_path):
    payload = {
        "kind": "nonsense",
        "project_folder": str(tmp_path),
        "version": "5.13",
    }
    process, result_file = _run_worker(tmp_path, payload)

    assert process.returncode == 1
    assert "Unsupported native worker kind" in json.loads(
        result_file.read_text(encoding="utf-8"))["error"]


def test_the_bridge_sends_lookup_jobs_to_the_worker():
    """The in-process lookup runner must be gone, or QGIS is exposed again."""
    import inspect

    from mhm_qgis.core.executions.morphology import commands
    pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")
    from mhm_qgis.qt.objects import morphology_tasks

    source = inspect.getsource(commands.lookup)
    assert "native_worker" in source
    assert morphology_tasks._run_lookup is commands.lookup
    # The Qt scheduler must not expose the heavy formatter directly.
    assert not hasattr(morphology_tasks, "prepare_categorical_file")
