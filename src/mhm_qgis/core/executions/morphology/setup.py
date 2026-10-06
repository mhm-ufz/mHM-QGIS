"""Morphology publication commands with explicit, path-only inputs."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ....externals.mhm_tools_handler import create_latlon_file
from ...handlers.file.netcdf.raster import copy_rasters, source, variables
from ...handlers.raster.tasks import (
    TaskShare,
    _cancelled,
    _progress,
    _target_mask,
    write_domain_dem_ascii,
)
from ...handlers.state import processing
from ...handlers.state.domain_state import load_state as load_domain_state
from ...handlers.state.domain_state import resolve_output_path
from ...handlers.store import registry
from ...handlers.store.layout import domain_dem_path, is_v6, morph_folder
from ...handlers.store.paths import (
    data_folder,
    lai_dem_staging_path,
    temp_morphology_folder,
    workspace_folder,
)
from ...morphology.layers import lai
from ...morphology.layers.advanced_l0 import (
    RASTER_SUFFIXES,
    missing_model_inputs,
    publish_model_inputs,
    staged_files,
)
from ...morphology.layers.mask import write_morph_mask
from ...morphology.layers.morph_input_nc import write_morph_input_nc
from . import PUBLICATION_STAGES


@dataclass(frozen=True)
class SetupRequest:
    """Everything needed by Morphology publication, detached from the dialog."""

    project_folder: str
    headers: Mapping[str, Mapping]
    crs: str = ""
    lai_source: str = ""
    lai_timestep: str = lai.DEFAULT_TIMESTEP
    workflow: str = "execute_all_morphology"


def _derivative_sources(folder, suffix=""):
    bundle = folder / f"dem_derivatives{suffix}.nc"
    names = variables(bundle)
    layers = {
        name: source(bundle, "dem_filled" if name == "dem" and not suffix else name)
        for name in ("dem", "slope", "aspect", "facc", "fdir")
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
    """Write the saved-domain mask and a masked DEM; leave all other layers whole."""
    l0 = dict(request.headers["L0"])
    folder = Path(temp_morphology_folder(request.project_folder))
    filled = folder / "filled_dem.nc"
    state = load_domain_state(request.project_folder)
    saved = state.get("merged_mask_path")
    if not saved:
        raise FileNotFoundError(
            "Save the domain in Domain Delineator before Execute All."
        )
    watershed = resolve_output_path(request.project_folder, saved)
    if not watershed.is_file():
        raise FileNotFoundError(
            "The saved merged watershed is missing; save Domain Delineator again."
        )
    derivatives = _derivative_sources(folder)
    if "dem" not in derivatives:
        raise FileNotFoundError("Prepare the NetCDF DEM derivatives before masking.")
    keep = _target_mask(watershed, l0, request.crs, filled)
    _progress(task, 30)
    masked = copy_rasters(
        folder / "dem_derivatives_masked.nc",
        {"dem": derivatives["dem"]},
        l0,
        reference_path=filled,
        mask=keep,
        task=task,
    )
    _record(request.project_folder, masked, "aligned L0 NetCDF mask")
    _progress(task, 60)
    latlon_path = Path(data_folder(request.project_folder)) / "latlon.nc"
    if not latlon_path.is_file():
        raise FileNotFoundError("Create latlon.nc before Execute All Morphology.")
    morph_mask = write_morph_mask(
        Path(morph_folder(request.project_folder)) / "morph_mask.nc",
        masked,
        latlon_path,
    )
    _record(request.project_folder, morph_mask)
    _progress(task, 90)
    from ...handlers.state import nml_settings

    nml_settings.sync_domain_settings(request.project_folder)
    processing.save_domains(request.project_folder, [{
        "domain_id": 1,
        "outlet_id": "",
        "name": "master",
        "mask": str(watershed),
        "directory": morph_folder(request.project_folder),
        "dem_path": domain_dem_path(request.project_folder, "master"),
    }])
    return Path(masked), Path(morph_mask)


def latlon(request: SetupRequest, *, log=None, task=None) -> tuple[Path, ...]:
    """Write the common multilevel coordinate file for either mHM version."""
    headers = request.headers
    output = Path(data_folder(request.project_folder)) / "latlon.nc"
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
    folder = Path(temp_morphology_folder(request.project_folder))
    destination = Path(morph_folder(request.project_folder))
    destination.mkdir(parents=True, exist_ok=True)
    if is_v6(request.project_folder):
        from ...handlers.state.nml_settings import load_settings
        settings = load_settings(request.project_folder)
        layers = _derivative_sources(folder)
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
            raise ValueError("No morphology rasters were found for input.nc.")
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
        _progress(task, 50)
        # v6 keeps the common-extent DEM whole; morph_mask.nc defines the domain.
        dem = copy_rasters(destination / "dem.nc", {"dem": layers["dem"]},
                           request.headers["L0"], reference_path=folder / "filled_dem.nc", task=task)
        _progress(task, 80)
        _record(request.project_folder, dem)
        latlon_path = Path(data_folder(request.project_folder)) / "latlon.nc"
        morph_mask = destination / "morph_mask.nc"
        if not morph_mask.is_file():
            raise FileNotFoundError("Prepare morph_mask.nc before publication.")
        outputs = [Path(output), Path(dem), morph_mask]
        gauge = layers.get("idgauges")
        if gauge:
            idgauges = write_domain_dem_ascii(
                gauge,
                destination / "idgauges.asc",
                request.headers["L0"],
                None,
                reference_path=folder / "filled_dem.nc",
                task=TaskShare(task, 80, 100),
                project_folder=request.project_folder,
            )
            outputs.append(Path(idgauges))
            _record(request.project_folder, idgauges)
        from ...handlers.state.nml_outputs import register_morphology
        register_morphology(
            request.project_folder, bundle=output, dem=dem,
            mask=morph_mask, latlon=latlon_path)
        return tuple(outputs)

    outputs = []
    sources = _model_sources(folder)
    for index, (name, uri) in enumerate(sources.items()):
        output = write_domain_dem_ascii(
            uri, destination / f"{name}.asc", request.headers["L0"], None,
            reference_path=folder / "filled_dem.nc",
            task=TaskShare(task).part(index, len(sources)),
            project_folder=request.project_folder,
        )
        outputs.append(Path(output))
    if not outputs:
        raise ValueError("No morphology rasters were found to export.")
    for output in outputs:
        _record(request.project_folder, output, "mhm_tools.common.file_handler")
    return tuple(outputs)


def publish(request: SetupRequest, *, log=None, task=None) -> tuple[Path, ...]:
    """Publish class grids, LAI and morphology grids, one progress bar across all."""
    staged_lai = Path(lai_dem_staging_path(request.project_folder))
    classes = sum(path.suffix.lower() in RASTER_SUFFIXES for path in staged_files(request.project_folder))
    grids = 1 if is_v6(request.project_folder) else len(
        _model_sources(Path(temp_morphology_folder(request.project_folder))))
    units = max(1, classes + staged_lai.is_file() + grids)
    before_grids = 100 * (classes + staged_lai.is_file()) / units
    outputs = [
        Path(path)
        for path in publish_model_inputs(
            request.project_folder, request.headers["L0"], log=log,
            task=TaskShare(task, 0, 100 * classes / units),
        )
    ]
    if staged_lai.is_file():
        from ...handlers.state.nml_settings import load_settings

        config = load_settings(request.project_folder).get("lai", {})
        output = lai.publish_to_l0(
            request.project_folder,
            dict(request.headers["L0"]),
            request.crs,
            source=config.get("source_path", ""),
            target_timestep=config.get("target_timestep") or lai.DEFAULT_TIMESTEP,
            log=log,
        )
        outputs.append(Path(output))
    _progress(task, before_grids)
    outputs.extend(write(request, log=log, task=TaskShare(task, before_grids, 100)))
    for output in outputs:
        _record(request.project_folder, output)
    missing = missing_model_inputs(request.project_folder)
    if missing:
        raise FileNotFoundError(
            "Inputs recorded in nml-settings.json are missing: "
            + ", ".join(sorted(missing))
        )
    return tuple(outputs)


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
            _COMMANDS[stage.command](request, log=log, task=TaskShare(task).part(index - 1, len(active)))
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
