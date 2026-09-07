"""Morphology Setup commands with explicit, path-only inputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from ....applications.mhm_tools_handler import create_latlon_file
from ...handlers.file.ascii.morphology import (
    MorphologyAsciiLayer,
    prepare_morphology_ascii_files,
)
from ...handlers.file.netcdf.raster import copy_rasters, source, variables
from ...handlers.raster.tasks import crop_aligned_l0_raster, _target_mask, write_domain_dem_ascii
from ...handlers.state import processing
from ...handlers.store import registry
from ...handlers.store.layout import MERGED_MASK_NAME, is_v6, morph_folder
from ...handlers.store.paths import geometry_folder, master_data_folder
from ...morphology.layers import CATEGORICAL_PAD_VALUE, lai
from ...morphology.layers.advanced_l0 import missing_model_inputs, publish_model_inputs
from ...morphology.layers.domain_dem import domain_dem_plan, link_master_inputs, write_domain_dems
from ...morphology.layers.morph_input_nc import available_layers, write_morph_input_nc
from . import MORPH_SETUP_STAGES


@dataclass(frozen=True)
class SetupRequest:
    """Everything needed by Morphology Setup, detached from the dialog."""

    project_folder: str
    headers: Mapping[str, Mapping]
    crs: str = ""
    lai_source: str = ""
    lai_timestep: str = lai.DEFAULT_TIMESTEP
    workflow: str = "morph_setup"


_RASTERS = (
    ("3_land_use.tif", "3_Land_Use", CATEGORICAL_PAD_VALUE, False),
    ("3_soil.tif", "3_Soil", CATEGORICAL_PAD_VALUE, False),
    ("3_geology_processed.tif", "3_Geology", CATEGORICAL_PAD_VALUE, False),
)

_ASCII_NAMES = {
    "3_land_use_masked.tif": ("lc.asc", -9999, True),
    "3_soil_masked.tif": ("soil_class.asc", -9999, True),
    "3_geology_processed_masked.tif": ("geology_class.asc", -9999, True),
}


def variant(path, suffix: str) -> Path:
    """Return the sibling crop/mask name for a raster."""
    path = Path(path)
    stem = path.stem
    for existing in ("_crop", "_masked"):
        if stem.endswith(existing):
            stem = stem[: -len(existing)]
    return path.with_name(f"{stem}{suffix}{path.suffix or '.tif'}")


def raster_plan(project_folder) -> tuple[dict, ...]:
    """Return present geometry rasters and their crop/mask policy."""
    folder = Path(geometry_folder(project_folder))
    return tuple(
        {
            "input": folder / filename,
            "crop": variant(folder / filename, "_crop"),
            "masked": variant(folder / filename, "_masked"),
            "name": name,
            "pad": pad,
            "watershed_masked": watershed_masked,
        }
        for filename, name, pad, watershed_masked in _RASTERS
        if (folder / filename).is_file()
    )


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


def crop(request: SetupRequest, *, log=None) -> tuple[Path, ...]:
    """Place every prepared morphology raster on the common L0 grid."""
    l0 = dict(request.headers["L0"])
    folder = Path(geometry_folder(request.project_folder))
    filled = folder / "filled_dem.nc"
    outputs = []
    derivatives = _derivative_sources(folder)
    if "dem" not in derivatives:
        raise FileNotFoundError("Run Execute All to prepare dem_derivatives.nc before Morphology Setup.")
    output = copy_rasters(folder / "dem_derivatives_crop.nc", derivatives, l0, reference_path=filled)
    (folder / "dem_derivatives_masked.nc").unlink(missing_ok=True)
    outputs.append(output)
    _record(request.project_folder, output, "aligned L0 NetCDF window copy")
    for item in raster_plan(request.project_folder):
        item["crop"].unlink(missing_ok=True)
        item["masked"].unlink(missing_ok=True)
        output = Path(
            crop_aligned_l0_raster(
                item["input"],
                item["crop"],
                l0,
                reference_path=filled,
                pad_value=item["pad"],
            )
        )
        outputs.append(output)
        _record(request.project_folder, output, "aligned L0 window copy")
        _say(log, f"{item['name']} cropped successfully.")
    if request.lai_source:
        output = lai.crop_to_l0(
            request.project_folder, l0, request.crs, log=log
        )
        outputs.append(output)
        _record(request.project_folder, output)
    if not outputs:
        raise ValueError("No prepared morphology rasters were found to crop.")
    return tuple(outputs)


def mask(request: SetupRequest, *, log=None) -> tuple[Path, ...]:
    """Mask DEM derivatives while retaining categorical coverage everywhere."""
    l0 = dict(request.headers["L0"])
    folder = Path(geometry_folder(request.project_folder))
    filled = folder / "filled_dem.nc"
    watershed = folder / "Watersheds" / MERGED_MASK_NAME
    if not watershed.is_file():
        raise FileNotFoundError(
            "The merged domain mask is missing. Save a domain before Morphology Setup."
        )
    outputs = []
    derivatives = _derivative_sources(folder, "_crop")
    if "dem" not in derivatives:
        raise FileNotFoundError("Crop the NetCDF DEM derivatives before masking.")
    keep = _target_mask(watershed, l0, request.crs, filled)
    output = copy_rasters(folder / "dem_derivatives_masked.nc", derivatives, l0,
                          reference_path=filled, mask=keep)
    outputs.append(output)
    _record(request.project_folder, output, "aligned L0 NetCDF mask")
    for item in raster_plan(request.project_folder):
        if not item["crop"].is_file():
            continue
        item["masked"].unlink(missing_ok=True)
        output = Path(
            crop_aligned_l0_raster(
                item["crop"], item["masked"], l0,
                reference_path=filled,
                pad_value=item["pad"],
            )
        )
        outputs.append(output)
        _record(
            request.project_folder,
            output,
            "aligned L0 window copy",
        )
        _say(log, f"{item['name']} written successfully.")
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
        raise ValueError("No cropped morphology rasters were found to mask.")
    return tuple(outputs)


def latlon(request: SetupRequest, *, log=None) -> tuple[Path, ...]:
    """Write the v5.13 multilevel coordinate file."""
    if is_v6(request.project_folder):
        return ()
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


def write(request: SetupRequest, *, log=None) -> tuple[Path, ...]:
    """Write v5 ASCII inputs or the single v6 morphology NetCDF."""
    folder = Path(geometry_folder(request.project_folder))
    destination = Path(morph_folder(request.project_folder))
    destination.mkdir(parents=True, exist_ok=True)
    if is_v6(request.project_folder):
        layers = available_layers(folder)
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
        return (Path(output),)

    layers = []
    for filename, (name, nodata, integer) in _ASCII_NAMES.items():
        path = folder / filename
        if not path.is_file():
            continue
        layers.append(
            MorphologyAsciiLayer(
                input_path=str(path),
                output_path=str(destination / name),
                name=path.stem.replace("_masked", "").replace("_", " ").title(),
                nodata_value=nodata,
                integer=integer,
            )
        )
    result = prepare_morphology_ascii_files(
        layers=layers,
        headers=request.headers,
        overwrite=True,
        log=log,
    )
    outputs = [Path(path) for path in result.outputs.values()]
    for name, uri in _derivative_sources(folder, "_masked").items():
        output = write_domain_dem_ascii(uri, destination / f"{name}.asc",
                                        request.headers["L0"], None, reference_path=folder / "filled_dem.nc")
        outputs.append(Path(output))
    if not outputs:
        raise ValueError("No masked morphology rasters were found to export.")
    for output in outputs:
        _record(request.project_folder, output, "mhm_tools.common.file_handler")
    return tuple(outputs)


def publish(request: SetupRequest, *, log=None) -> tuple[Path, ...]:
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


def link_domains(request: SetupRequest, *, log=None) -> tuple[Path, ...]:
    """Link only published shared inputs, before replacing each domain DEM."""
    if is_v6(request.project_folder):
        return ()
    outputs = tuple(
        path for entry in domain_dem_plan(request.project_folder)
        for path in link_master_inputs(request.project_folder, entry["directory"], log=log, strict=True)
    )
    for output in outputs:
        _record(request.project_folder, output)
    return outputs


def domain_dems(request: SetupRequest, *, log=None) -> tuple[Path, ...]:
    folder = Path(geometry_folder(request.project_folder))
    plan = domain_dem_plan(request.project_folder)
    processing.save_domains(request.project_folder, plan)
    written = write_domain_dems(
        request.project_folder,
        source(folder / "dem_derivatives_crop.nc", "dem"),
        request.headers["L0"],
        reference_path=folder / "filled_dem.nc",
        log=log,
    )
    outputs = []
    for entry in written:
        outputs.append(Path(entry["dem_path"]))
    for output in outputs:
        _record(request.project_folder, output)
    return tuple(outputs)


_COMMANDS: dict[str, Callable] = {
    "crop": crop,
    "mask": mask,
    "latlon": latlon,
    "write": write,
    "publish": publish,
    "link_domains": link_domains,
    "domain_dems": domain_dems,
}


def run(request: SetupRequest, *, log=None) -> bool:
    """Run the canonical Morphology Setup plan synchronously."""
    processing.mark_workflow(request.project_folder, request.workflow, "running")
    try:
        active = [
            stage
            for stage in MORPH_SETUP_STAGES
            if not stage.condition
            or stage.condition != "v5"
            or not is_v6(request.project_folder)
        ]
        for index, stage in enumerate(active, start=1):
            key = f"{request.workflow}_{stage.command}"
            _say(log, f"Morphology Setup {index}/{len(active)}: {stage.label}")
            processing.mark_workflow(request.project_folder, key, "running")
            _COMMANDS[stage.command](request, log=log)
            processing.mark_workflow(request.project_folder, key, "completed")
    except Exception as error:
        processing.mark_workflow(
            request.project_folder, request.workflow, "failed", str(error)
        )
        raise
    processing.mark_workflow(
        request.project_folder,
        request.workflow,
        "completed",
        "Morphology Setup completed successfully.",
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
    "crop",
    "domain_dems",
    "latlon",
    "link_domains",
    "mask",
    "publish",
    "raster_plan",
    "run",
    "variant",
    "write",
]
