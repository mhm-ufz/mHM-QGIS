"""Post-processing jobs for the native worker: the scan and small derived reads.

`scan_job` runs in the parent (a coordinator task): it resolves the run, writes the
grid products the scan needs (cells, axes, basin weights, cell areas, sealed
fraction per scene) and picks the stale variables. `scan` then runs the full-file
pass out of process and records the fingerprints. Both are Qt-free.
"""
from __future__ import annotations

import numpy as np

from ..datamodel import output_registry
from ..post import cache, catalog, grid
from .morphology.commands import native_worker

SERIES_DEFAULT = ("aET", "Q", "SM_Lall", "snowpack", "recharge")  # what the Tier A pages read


def _save(root, name, array):
    """Write `array` unless the file holds it already: its mtime feeds the fingerprints."""
    path = root / f"{name}.npy"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if np.array_equal(np.load(path), array, equal_nan=True):
            return path
    except (OSError, ValueError, TypeError):
        pass
    np.save(path, array)
    return path


def scan_job(ref, *, file="mhm", variables=None, series=SERIES_DEFAULT, budget_mb=256, values=None,
             precipitation=True) -> dict:
    """The primitive `post-scan` job for one output file of `ref`; raises when the
    series cache would not fit on the disk. `job["variables"]` is empty when all are fresh."""
    run = catalog.open_run(ref, values)
    if not run.exists(file):
        raise ValueError(f"This run has no {file} output file ({run.files.get(file) or 'not set'}).")
    root = cache.folder(ref)
    root.mkdir(parents=True, exist_ok=True)
    path = run.files[file]
    axes = grid.axes(path)
    cells = np.nonzero(grid.valid_cells(path))
    sources = [_save(root, f"cells_{file}", np.array(cells, dtype="i4")), path]
    _save(root, "x", axes.x)
    _save(root, "y", axes.y)
    registry = output_registry(run.version)["outputs"]
    specs = [{"name": v.name, "kind": v.kind or "state", "weight": v.weight,
              "amount": bool(registry.get(v.output, {}).get("balance"))}
             for v in run.variables if v.present and v.file == file and (variables is None or v.name in variables)]
    job = {"kind": "post-scan", "file": file, "path": str(path), "cache": str(root),
           "shape": list(axes.shape), "budget_mb": budget_mb, "basins": [], "sealed": False, "problems": []}
    if file == "mhm":
        domain, source = grid.domain_weights(run, axes)
        gauges, job["problems"] = grid.gauge_weights(run, axes)
        basins = {"domain": domain, **{str(gauge): weights for gauge, weights in gauges.items()}}
        job["basins"] = list(basins)
        job["domain_source"] = source
        sources += [_save(root / "weights", name, weights) for name, weights in basins.items()]
        sources.append(_save(root, "area", grid.cell_area_m2(axes)))
        scenes = grid.scene_index(run, run.bounds)
        sealed = [grid.sealed_fraction(run, axes, scene) for scene in range(int(scenes.max()) + 1)]
        if all(fraction is not None for fraction, _ in sealed):
            job["sealed"] = True
            sources += [_save(root, "sealed", np.stack([fraction for fraction, _ in sealed])),
                        _save(root, "scenes", scenes)]
        else:
            job["problems"].append("No sealed fraction (write_restart = .true. gives mHM's own): "
                                   "pervious and sealed states are not weighted.")
        forcing = run.forcing()
        if precipitation and forcing:
            job["forcing"] = {"path": str(forcing[0]), "variable": forcing[1]}
            specs.append({"name": "pre", "kind": "flux", "weight": "cell", "amount": True})
            sources.append(forcing[0])
    names = [spec["name"] for spec in specs]
    job["series"] = [name for name in names if name in series]
    fingerprints = {spec["name"]: cache.variable_fingerprint(
        sources + ([job["forcing"]["path"]] if spec["name"] == "pre" else []), spec["name"],
        spec["name"] in job["series"]) for spec in specs}
    fresh = set(names) - set(cache.stale(root, fingerprints))
    job["variables"] = [spec for spec in specs if spec["name"] not in fresh]
    job["fingerprints"] = fingerprints
    rescanned = [name for name in job["series"] if name not in fresh]
    job["series_bytes"] = cache.series_bytes(len(cells[0]), len(run.bounds), rescanned)
    cache.check_disk(root, job["series_bytes"])
    return job


def scan(task, job) -> dict:
    """Run the scan in the native worker and record what it built."""
    if not job["variables"]:
        return {}
    summary = native_worker(task, job, job["cache"])["summary"]
    index = cache.load_index(job["cache"])
    for name, info in summary.items():
        index["variables"][name] = {**info, "fingerprint": job["fingerprints"][name], "file": job["file"]}
    index["problems"] = job["problems"]
    index["domain_source"] = job.get("domain_source")
    cache.save_index(job["cache"], index)
    return summary


def derive(task, job, working_folder):
    """A `post-derive` read in the native worker (e.g. one cell's series without a cache)."""
    return native_worker(task, {**job, "kind": "post-derive"}, working_folder)["result"]
