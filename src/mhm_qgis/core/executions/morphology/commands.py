"""QGIS-free commands used by morphology task schedulers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from ....externals.mhm_tools_handler import prepare_lai_file
from ...handlers.raster.tasks import (
    dem_derivative_files,
    hydrology_files,
)
from ...handlers.state.cache import (
    cached_payload,
    fingerprint,
    load_state,
    outputs_newer_than_inputs,
    outputs_present,
    store_payload,
)
from ...handlers.store.paths import temp_morphology_folder
from ...morphology.layers.advanced_l0 import publish_model_inputs
from ...morphology.layers.categorical import copy_ready, saved_outputs


def dem_derivatives(task, options):
    """Prepare the DEM derivatives in a disposable native process."""

    def compute(prepared, staging, _log):
        result = native_worker(
            task,
            {
                "kind": "dem-derivatives",
                "input_file": str(prepared),
                "output_folder": str(staging),
            },
            Path(staging).parent,
        )
        return Path(result["output"])

    return dem_derivative_files(task=task, compute=compute, **options)


def hydrology(task, options):
    return hydrology_files(task=task, **options)


def gauge_position(task, options):
    result = native_worker(
        task,
        {"kind": "gauge-position", **options},
        Path(options["output_path"]).parent,
    )
    return result["output"]


def lai(task, options, messages):
    return prepare_lai_file(
        options["source_path"],
        options["filled_dem"],
        options["output_path"],
        output_temporal_resolution=options["target_timestep"],
        source_variable=options.get("source_variable"),
        dem_crs=options.get("dem_crs") or None,
        resampling=options.get("method") or "bilinear",
        task=task,
        log=messages.append,
    )


def lookup(task, job):
    result = native_worker(
        task,
        {"mode": "lookup", "kind": job["kind"], "job": job},
        job["geometry"],
    )
    return {
        "kind": result["kind"],
        "output": result["output"],
        "definition": result.get("definition", ""),
        "metadata": result.get("metadata", ""),
    }


def advanced(task, job):
    payload = dict(job)
    payload["value"] = job["value"].as_dict()
    result = native_worker(task, payload, temp_morphology_folder(job["project_folder"]))
    return {"kind": result["kind"], "outputs": tuple(result["outputs"])}


def ready(task, job):
    """Place and publish one mHM-ready categorical raster."""
    staged = copy_ready(
        job["project_folder"],
        job["kind"],
        job["input_path"],
        definition_source=job.get("classdefinition_path", ""),
        variable=job.get("variable", ""),
        source_crs=job.get("source_crs", ""),
        target_crs=job["target_crs"],
        target_header=job["target_header"],
        task=task,
    )
    published = (
        publish_model_inputs(job["project_folder"], job["target_header"])
        if job.get("publish")
        else ()
    )
    return {
        "kind": job["kind"],
        "outputs": tuple(map(str, staged)),
        "published": tuple(map(str, published)),
    }


def stage_digest(inputs, config):
    return fingerprint(inputs, config)


def reusable_stage(project_folder, stage, digest, *, inputs=(), outputs=()):
    """Return reusable output metadata, adopting safe existing outputs."""
    if not project_folder:
        return None
    payload = cached_payload(project_folder, "stages", stage, digest)
    if outputs_present(payload):
        return payload
    if stage in load_state(project_folder).get("stages", {}):
        return None  # A changed configuration cannot adopt its own stale outputs.
    candidates = [str(path) for path in outputs if path]
    if (
        not candidates
        or not all(Path(path).is_file() for path in candidates)
        or not outputs_newer_than_inputs(inputs, candidates)
    ):
        return None
    record_stage(project_folder, stage, digest, candidates)
    return {"outputs": candidates, "adopted": True}


def record_stage(project_folder, stage, digest, outputs):
    paths = [str(path) for path in outputs if path and Path(path).is_file()]
    if project_folder and paths:
        store_payload(
            project_folder,
            "stages",
            stage,
            digest,
            {"outputs": paths},
        )


def categorical_outputs(project_folder, kind):
    return saved_outputs(project_folder, kind)


def native_worker(task, payload, working_folder):
    """Run one native job without risking the long-lived QGIS process."""
    if task is not None and task.isCanceled():
        raise RuntimeError("Task cancelled.")
    if task is not None:
        task.setProgress(5)
    with TemporaryDirectory(
        prefix="mhm_qgis_native_", dir=str(working_folder)
    ) as temporary:
        temporary = Path(temporary)
        job_file = temporary / "job.json"
        result_file = temporary / "result.json"
        job_file.write_text(json.dumps(payload), encoding="utf-8")
        environment = os.environ.copy()
        package_parent = str(Path(__file__).resolve().parents[4])
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (package_parent, environment.get("PYTHONPATH", ""))
            if item
        )
        log_file = temporary / "worker.log"
        with log_file.open("w", encoding="utf-8") as log_stream:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mhm_qgis.core.native_worker",
                    str(job_file),
                    str(result_file),
                ],
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            while process.poll() is None:
                if task is not None and task.isCanceled():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise RuntimeError("Task cancelled.")
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    continue
        log = log_file.read_text(encoding="utf-8", errors="replace").strip()
        if not result_file.is_file():
            raise RuntimeError(
                f"Native raster worker failed: {log or f'exit status {process.returncode}'}"
            )
        response = json.loads(result_file.read_text(encoding="utf-8"))
        if process.returncode or response.get("error"):
            raise RuntimeError(
                response.get("traceback") or response.get("error") or log
            )
        result = response["result"]
    if task is not None:
        task.setProgress(100)
    return result


__all__ = [
    "advanced",
    "categorical_outputs",
    "dem_derivatives",
    "gauge_position",
    "hydrology",
    "lai",
    "lookup",
    "native_worker",
    "ready",
    "record_stage",
    "reusable_stage",
    "stage_digest",
]
