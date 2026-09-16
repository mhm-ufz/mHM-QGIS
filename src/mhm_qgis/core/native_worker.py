"""Qt/QGIS-free subprocess entry point for native raster libraries."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from .handlers.lookup import (
    LandUseInput,
    LandUsePeriod,
    SoilHorizon,
    SoilInput,
)
from .morphology.layers.advanced_inputs import process_land_cover_input, process_soil_input
from .handlers.lookup import run_lookup_job


def _prefer_worker_termination():
    """Let Linux terminate this disposable worker before the QGIS process."""
    try:
        Path("/proc/self/oom_score_adj").write_text("500", encoding="ascii")
    except OSError:
        pass


def _land_use(value):
    return LandUseInput(
        periods=tuple(
            LandUsePeriod.from_dict(item)
            for item in value["periods"]
        ),
        lookup_table=Path(value["lookup_table"]),
        mapping_field=str(value["mapping_field"]),
        class_field=str(value["class_field"]),
    )


def _soil(value):
    return SoilInput(
        horizons=tuple(
            SoilHorizon(
                horizon=int(item["horizon"]),
                upper_depth=float(item["upper_depth"]),
                lower_depth=float(item["lower_depth"]),
                clay_layer=Path(item["clay_layer"]),
                sand_layer=Path(item["sand_layer"]),
                silt_layer=Path(item["silt_layer"]),
                bulk_density_layer=Path(item["bulk_density_layer"]),
            )
            for item in value["horizons"]
        ),
        bulk_density_unit=str(value["bulk_density_unit"]),
    )


def run(job):
    if job.get("mode") == "lookup":
        return run_lookup_job(job["job"])
    if job["kind"] == "dem-derivatives":
        # Imported here so the worker never pulls in the QGIS-only packages.
        from ..applications.mhm_tools_handler import create_dem_derivative_files

        output = create_dem_derivative_files(
            job["input_file"], job["output_folder"]
        )
        return {
            "kind": job["kind"],
            "output": str(output),
        }
    common = (job["project_folder"], job["version"])
    if job["kind"] == "lc":
        outputs = process_land_cover_input(
            *common, _land_use(job["value"]), job["filled_dem"]
        )
    elif job["kind"] == "soil":
        outputs = process_soil_input(
            *common, _soil(job["value"]), job["filled_dem"]
        )
    else:
        raise ValueError(f"Unsupported native worker kind: {job['kind']}")
    return {"kind": job["kind"], "outputs": [str(path) for path in outputs]}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        raise SystemExit("Usage: native_worker JOB_JSON RESULT_JSON")
    _prefer_worker_termination()
    job_path, result_path = map(Path, argv)
    try:
        payload = {"result": run(json.loads(job_path.read_text(encoding="utf-8")))}
        status = 0
    except Exception as error:
        payload = {"error": str(error), "traceback": traceback.format_exc()}
        status = 1
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
