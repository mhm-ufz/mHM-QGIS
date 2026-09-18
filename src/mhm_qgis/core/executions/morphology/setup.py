"""Morphology publication commands with explicit, path-only inputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from ....externals.mhm_tools_handler import create_latlon_file
from ...handlers.file.netcdf.raster import copy_rasters, source, variables
from ...handlers.raster.tasks import _cancelled, _target_mask, write_domain_dem_ascii
from ...handlers.state import processing
from ...handlers.store import registry
from ...handlers.store.layout import is_v6, morph_folder
from ...handlers.store.paths import geometry_folder, master_data_folder, workspace_folder
from ...morphology.layers import lai
from ...morphology.layers.advanced_l0 import missing_model_inputs, publish_model_inputs
from ...morphology.layers.morph_input_nc import write_morph_input_nc
from ...morphology.layers.mask import write_morph_mask
from . import PUBLICATION_STAGES
from .delineation import domain_mask


@dataclass(frozen=True)
class SetupRequest:
    """Everything needed by Morphology publication, detached from the dialog."""

    project_folder: str
    headers: Mapping[str, Mapping]
    crs: str = ""
    lai_source: str = ""
    lai_timestep: str = lai.DEFAULT_TIMESTEP
    workflow: str = "execute_all"


def _derivative_sources(folder, suffix=""):
    bundle = folder / f"dem_derivatives{suffix}.nc"
    names = variables(bundle)
    layers = {
        name: source(bundle, "dem_filled" if name == "dem" and not suffix else name)
        for name in ("dem", "slope", "aspect", "facc", "fdir", "idgauges")
        if ("dem_filled" if name == "dem" and not suffix else name) in names
    }
    if not suffix and (folder / "idgauges.nc").is_file():
        layers["idgauges"] = str(folder / "idgauges.nc")
    return layers


def _model_sources(folder):
    """Only the DEM is watershed-masked; the rest are already model-ready."""
    layers = _derivative_sources(folder)
    layers.update(_derivative_sources(folder, "_masked"))
    return layers


def mask(request: SetupRequest, *, log=None, task=None) -> tuple[Path, ...]:
    """Mask the domain DEM while retaining full coverage for every other layer."""
    l0 = dict(request.headers["L0"])
    folder = Path(geometry_folder(request.project_folder))
    filled = folder / "filled_dem.nc"
    watershed = domain_mask(task, request.project_folder, filled, request.crs)
    outputs = []
    derivatives = _derivative_sources(folder)
    if "dem" not in derivatives:
        raise FileNotFoundError("Prepare the NetCDF DEM derivatives before masking.")
    keep = _target_mask(watershed, l0, request.crs, filled)
    output = copy_rasters(folder / "dem_derivatives_masked.nc", {"dem": derivatives["dem"]},
                          l0, reference_path=filled, mask=keep, task=task)
    outputs.append(output)
    _record(request.project_folder, output, "aligned L0 NetCDF mask")
    if request.lai_source:
        output = lai.publish_to_l0(
            request.project_folder,
            l0,
            request.crs,
            source=request.lai_source,
            target_timestep=request.lai_timestep,
            log=log,
        )
        outputs.append(output)
        _record(request.project_folder, output)
    if not outputs:
        raise ValueError("No morphology rasters were found to mask.")
    return tuple(outputs)


def latlon(request: SetupRequest, *, log=None, task=None) -> tuple[Path, ...]:
    """Write the common multilevel coordinate file for either mHM version."""
    headers = request.headers
    output = Path(master_data_folder(request.project_folder)) / "latlon.nc"
    output.parent.mkdir(parents=True, exist_ok=True)
    create_latlon_file(
        out_file=output,
        level0=dict(headers["L0"]),
        level1=headers["L1"]["cellsize"],
        level11=headers["L11"]["cellsize"],
        level2=headers["L2"]["cellsize"],
        crs=request.crs or None,
        log=log,
    )
    _record(request.project_folder, output)
    return (output,)


def write(request: SetupRequest, *, log=None, task=None) -> tuple[Path, ...]:
    """Write v5 ASCII inputs or the single v6 morphology NetCDF."""
    folder = Path(geometry_folder(request.project_folder))
    destination = Path(morph_folder(request.project_folder))
    destination.mkdir(parents=True, exist_ok=True)
    if is_v6(request.project_folder):
        from ...handlers.state.nml_settings import load_settings
        settings = load_settings(request.project_folder)
        layers = _model_sources(folder)
        for name, section in (
            ("soil_class", "soil"),
            ("geology_class", "geology"),
            ("LAI_class", "lai"),
        ):
            record = settings.get(section, {})
            if name == "LAI_class" and record.get("mode") != "mhm_ready":
                continue
            configured = record.get("output_path")
            candidates = ([Path(workspace_folder(request.project_folder)) / configured] if configured else
                          [destination / f"{name}{suffix}" for suffix in (".nc", ".asc")])
            for candidate in candidates:
                if candidate.is_file():
                    layers[name] = (source(candidate, name) if candidate.suffix == ".nc" and name in variables(candidate)
                                    else str(candidate))
                    break
        if not layers:
            raise ValueError("No masked morphology rasters were found for input.nc.")
        output = write_morph_input_nc(
            destination / "input.nc",
            layers,
            request.headers["L0"],
            crs_string=request.crs or None,
            reference_path=folder / "filled_dem.nc",
            title="Static morphology inputs prepared by mhm_qgis",
            log=log,
        )
        _record(request.project_folder, output)
        # The single model's mask/DEM is independent of the shared bundle.
        dem = copy_rasters(destination / "dem.nc", {"dem": layers["dem"]},
                           request.headers["L0"], reference_path=folder / "filled_dem.nc", task=task)
        _record(request.project_folder, dem)
        latlon_path = Path(master_data_folder(request.project_folder)) / "latlon.nc"
        morph_mask = write_morph_mask(
            destination / "morph_mask.nc",
            folder / "dem_derivatives_masked.nc",
            latlon_path,
        )
        _record(request.project_folder, morph_mask)
        from ...handlers.state.nml_outputs import register_morphology
        register_morphology(
            request.project_folder, bundle=output, dem=dem,
            mask=morph_mask, latlon=latlon_path)
        return (Path(output), Path(dem), Path(morph_mask))

    outputs = []
    for name, uri in _model_sources(folder).items():
        output = write_domain_dem_ascii(uri, destination / f"{name}.asc",
                                        request.headers["L0"], None, reference_path=folder / "filled_dem.nc", task=task)
        outputs.append(Path(output))
    if not outputs:
        raise ValueError("No masked morphology rasters were found to export.")
    for output in outputs:
        _record(request.project_folder, output, "mhm_tools.common.file_handler")
    return tuple(outputs)


def publish(request: SetupRequest, *, log=None, task=None) -> tuple[Path, ...]:
    outputs = tuple(
        Path(path)
        for path in publish_model_inputs(
            request.project_folder, request.headers["L0"], log=log
        )
    )
    for output in outputs:
        _record(request.project_folder, output)
    missing = missing_model_inputs(request.project_folder)
    if missing:
        raise FileNotFoundError(
            "Inputs recorded in nml-settings.json are missing: "
            + ", ".join(sorted(missing))
        )
    return outputs


_COMMANDS: dict[str, Callable] = {
    "mask": mask,
    "latlon": latlon,
    "write": write,
    "publish": publish,
}


def run(request: SetupRequest, *, log=None, task=None) -> bool:
    """Run the canonical Morphology publication plan synchronously."""
    processing.mark_workflow(request.project_folder, request.workflow, "running")
    key = request.workflow
    try:
        active = PUBLICATION_STAGES
        for index, stage in enumerate(active, start=1):
            key = f"{request.workflow}_{stage.command}"
            _cancelled(task)
            _say(log, f"Morphology publication {index}/{len(active)}: {stage.label}")
            processing.mark_workflow(request.project_folder, key, "running")
            _COMMANDS[stage.command](request, log=log, task=task)
            _cancelled(task)
            processing.mark_workflow(request.project_folder, key, "completed")
    except Exception as error:
        processing.mark_workflow(request.project_folder, key, "failed", str(error))
        processing.mark_workflow(
            request.project_folder, request.workflow, "failed", str(error)
        )
        raise
    processing.mark_workflow(
        request.project_folder,
        request.workflow,
        "completed",
        "Morphology publication completed successfully.",
    )
    return True


def _record(project_folder, path, algorithm=None) -> None:
    registry.register(
        project_folder,
        path,
        name=Path(path).name,
        loaded=False,
        algorithm=algorithm,
    )


def _say(log, message) -> None:
    if log:
        log(message)


__all__ = [
    "SetupRequest",
    "latlon",
    "mask",
    "publish",
    "run",
    "write",
]
