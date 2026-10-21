"""Shared categorical morphology definitions and path helpers."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from ...handlers.state.nml_settings import (
    load_settings,
    relative_workspace_path,
    update_section,
)
from ...handlers.store.layout import morph_folder
from ...handlers.store.paths import (
    morph_staging_folder,
    temp_morphology_folder,
    workspace_folder,
)

SPECS = {
    "lc": {
        "kind": "land_cover",
        "label": "Land Cover",
        "geometry": "3_land_use.tif",
        "layer_name": "3_Land_Use",
        "ready_name": "lc",
    },
    "soil": {
        "kind": "soil",
        "label": "Soil",
        "geometry": "3_soil.tif",
        "layer_name": "3_Soil",
        "ready_name": "soil_class",
        "definition": "soil_classdefinition.txt",
    },
    "geology": {
        "kind": "geology",
        "label": "Geology",
        "geometry": "3_geology_processed.tif",
        "layer_name": "3_Geology",
        "ready_name": "geology_class",
        "definition": "geology_classdefinition.txt",
    },
    "lai": {
        "kind": "lai",
        "label": "LAI Class",
        "geometry": "3_lai.tif",
        "layer_name": "3_LAI_Class",
        "ready_name": "LAI_class",
        "definition": "LAI_classdefinition.txt",
    },
}


# Ready NetCDFs with periods (land cover) or horizons (soil): staged name and variable,
# the names publication splits for v5.13 and v6 reads as they are.
STACKS = {"lc": ("lc_periods", "land_cover"), "soil": ("soil_horizon_class", "soil_class")}


def ready_layers(source, variable="") -> int:
    """Layers of a ready NetCDF variable along its first dimension; 1 for a 2-D raster."""
    if Path(str(source)).suffix.lower() != ".nc" or not variable:
        return 1
    from netCDF4 import Dataset

    with Dataset(source) as handle:
        values = handle[variable]
        return values.shape[0] if values.ndim == 3 else 1


def normalized_mode(value: object) -> str:
    """Return the stable internal name of a categorical input mode."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return "lookup_table" if "lookup_table" in text else text


def intermediate_paths(project_folder, kind: str) -> tuple[Path, ...]:
    """Return raw, cropped and masked geometry outputs for ``kind``."""
    path = Path(temp_morphology_folder(project_folder)) / SPECS[kind]["geometry"]
    return (
        path,
        path.with_name(f"{path.stem}_crop{path.suffix}"),
        path.with_name(f"{path.stem}_masked{path.suffix}"),
    )


def ready_paths(project_folder, kind: str) -> tuple[Path, ...]:
    """Return every supported final-file variant for ``kind``."""
    spec = SPECS[kind]
    folder = Path(morph_folder(project_folder))
    return tuple(
        folder / f"{spec['ready_name']}{suffix}"
        for suffix in (".asc", ".nc", ".tif", ".tiff", ".prj", ".PRJ")
    )


def saved_outputs(project_folder, kind: str) -> tuple[Path, ...]:
    """Return the complete configured output set when it still exists."""
    section = "land_cover" if kind == "lc" else kind
    configured = load_settings(project_folder).get(section, {})
    if not isinstance(configured, dict):
        return ()
    values = []
    if kind == "lc":
        values.extend(
            scene.get("output_path")
            for scene in configured.get("scenes", ())
            if isinstance(scene, dict)
        )
    values.extend(
        configured.get(name) for name in ("output_path", "classdefinition_path")
    )
    root = Path(workspace_folder(project_folder))
    outputs: list[Path] = []
    for value in values:
        if not value:
            continue
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if path not in outputs:
            outputs.append(path)
    return tuple(outputs) if outputs and all(path.is_file() for path in outputs) else ()


def record_namelist(
    project_folder,
    kind: str,
    output,
    *,
    mode: str,
    lookup: dict | None = None,
    definition=None,
) -> None:
    """Record one categorical product for the namelist handoff."""
    value = {
        "mode": normalized_mode(mode),
        "output_path": relative_workspace_path(project_folder, output),
        "variable": {
            "lc": "land_cover",
            "soil": "soil_class",
            "geology": "geology_class",
            "lai": "LAI_class",
        }[kind],
    }
    if kind == "lai":
        value["time_step"] = 0
    if definition is not None and Path(definition).is_file():
        value["classdefinition_path"] = relative_workspace_path(
            project_folder, definition
        )
    for key in ("lookup_table", "mapping_field", "class_field"):
        if lookup and lookup.get(key):
            value[key] = lookup[key]
    update_section(project_folder, "land_cover" if kind == "lc" else kind, value)


def copy_ready(
    project_folder,
    kind: str,
    source,
    *,
    definition_source="",
    mode="mhm_ready",
    variable="",
    source_crs="",
    target_crs="",
    target_header=None,
    task=None,
) -> tuple[Path, ...]:
    """Stage a ready raster for common-grid validation and publication."""
    spec = SPECS[kind]
    source = Path(str(source).split("|", 1)[0])
    if source.suffix.lower() not in {".asc", ".nc", ".tif", ".tiff"} or not source.is_file():
        raise ValueError(
            f"mHM-ready {spec['label']} input must be a local ASC, NetCDF, or TIFF raster."
        )
    target = Path(morph_staging_folder(project_folder)) / (
        spec["ready_name"] + ".nc"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    definition_source = Path(definition_source) if definition_source else None
    definition_name = spec.get("definition") or (
        definition_source.name if definition_source else None
    )
    existing_definition = target.parent / definition_name if definition_name else None
    if spec.get("definition") and not definition_source and existing_definition.is_file():
        definition_source = Path(morph_folder(project_folder)) / definition_name

    if target_header and ready_layers(source, variable) > 1:
        if kind not in STACKS:
            raise ValueError(f"mHM-ready {spec['label']} input must have one layer.")
        return _stage_ready_stack(
            project_folder, kind, source, variable, definition_source, definition_name,
            source_crs=source_crs, target_crs=target_crs, target_header=target_header, task=task,
        )
    with TemporaryDirectory(prefix=f"mhm_qgis_{kind}_ready_", dir=target.parent) as name:
        temporary = Path(name)
        prepared = temporary / target.name
        if target_header:
            from ...handlers.file.netcdf.raster import copy_rasters
            from ...handlers.file.netcdf.raster import source as raster_source
            from ...handlers.raster.tasks import warp_raster_to_header

            uri = raster_source(source, variable) if source.suffix.lower() == ".nc" else source
            warped = temporary / f"{spec['ready_name']}.tif"
            warp_raster_to_header(
                uri,
                warped,
                target_header,
                target_crs,
                source_crs=source_crs,
                pad_value=-9999,
                task=task,
            )
            output_variable = {
                "lc": "land_cover",
                "soil": "soil_class",
                "geology": "geology_class",
                "lai": "LAI_class",
            }[kind]
            copy_rasters(
                prepared,
                {output_variable: warped},
                target_header,
                reference_path=warped,
                task=task,
            )
        else:
            shutil.copy2(source, prepared)
        replacements = [(prepared, target)]
        if prepared.with_suffix(".prj").is_file():
            replacements.append((prepared.with_suffix(".prj"), target.with_suffix(".prj")))
        if definition_name and definition_source and definition_source.is_file():
            prepared_definition = temporary / definition_name
            shutil.copy2(definition_source, prepared_definition)
            replacements.append((prepared_definition, target.parent / definition_name))
        if source.suffix.lower() == ".asc" and not target_header:
            for suffix in (".proj", ".prj", ".PRJ"):
                sidecar = source.with_suffix(suffix)
                if sidecar.is_file():
                    staged_sidecar = prepared.with_suffix(suffix)
                    shutil.copy2(sidecar, staged_sidecar)
                    replacements.append((staged_sidecar, target.with_suffix(suffix)))
                    break
        stack = [target.parent / f"{STACKS[kind][0]}.nc"] if kind in STACKS else []
        _publish(replacements, stack, temporary)  # a single layer replaces a staged stack

    definition = target.parent / definition_name if definition_name else None
    record_namelist(
        project_folder,
        kind,
        target,
        mode=mode,
        definition=definition,
    )
    return tuple(path for _source, path in replacements)


def _stage_ready_stack(project_folder, kind, source, variable, definition_source, definition_name, *,
                       source_crs, target_crs, target_header, task=None) -> tuple[Path, ...]:
    """Warp every period (land cover) or horizon (soil) of a ready NetCDF onto L0 like a
    single layer (nearest, -9999 outside), keeping that dimension and its bounds."""
    from netCDF4 import Dataset, num2date
    from osgeo import gdal

    from ...handlers.file.netcdf.raster import _writer
    from ...handlers.file.netcdf.raster import source as raster_source
    from ...handlers.raster.tasks import warp_raster_to_header

    stem, name = STACKS[kind]
    if kind == "soil":  # mHM v5.13 reads this name with iFlag_soilDB 1 (mo_mpr_file.f90)
        definition_name = "soil_classdefinition_iFlag_soilDB_1.txt"
        if not definition_source or not Path(definition_source).is_file():
            raise ValueError("mHM-ready soil horizons need their class definition file.")
    staging = Path(morph_staging_folder(project_folder))
    target = staging / f"{stem}.nc"
    with TemporaryDirectory(prefix=f"mhm_qgis_{kind}_ready_", dir=staging) as folder:
        temporary = Path(folder)
        warped = gdal.Open(warp_raster_to_header(
            raster_source(source, variable), temporary / f"{stem}.tif", target_header, target_crs,
            source_crs=source_crs, pad_value=-9999, task=task))
        prepared = temporary / target.name
        with Dataset(source) as src, _writer(prepared, target_header, warped.GetProjection()) as out:
            dim = src[variable].dimensions[0]
            axis, edges_name = src[dim], getattr(src[dim], "bounds", "")
            if edges_name not in src.variables:
                raise ValueError(f"mHM-ready {SPECS[kind]['label']}: '{dim}' needs bounds (periods or horizon depths).")
            edges, units = src[edges_name][:], getattr(axis, "units", "")
            out.createDimension(dim, len(axis))
            out.createDimension("bnds", 2)
            for copied, dims in ((axis, (dim,)), (src[edges_name], (dim, "bnds"))):
                var = out.createVariable(copied.name, copied.dtype, dims)
                var.setncatts({key: copied.getncattr(key) for key in copied.ncattrs() if key != "_FillValue"})
                var[:] = copied[:]
            values = out.createVariable(name, "i4", (dim, "y", "x"), fill_value=-9999, zlib=True)
            values.grid_mapping = "crs"
            for index in range(len(axis)):
                values[index] = warped.GetRasterBand(index + 1).ReadAsArray()
            calendar = getattr(axis, "calendar", "standard")
        warped = None
        replacements = [(prepared, target)]
        if definition_name and definition_source and Path(definition_source).is_file():
            shutil.copy2(definition_source, temporary / definition_name)
            replacements.append((temporary / definition_name, staging / definition_name))
        _publish(replacements, [staging / f"{SPECS[kind]['ready_name']}.nc"], temporary)

    definition = staging / definition_name if definition_name else None
    record_namelist(project_folder, kind, target, mode="mhm_ready", definition=definition)
    section = "land_cover" if kind == "lc" else "soil"
    record = load_settings(project_folder)[section]
    if kind == "lc":
        def year(value):  # the nearest 1 January
            when = num2date(value, units, calendar)
            return when.year + (when.month > 6)
        record["scenes"] = [{"start_year": year(lo), "end_year": year(hi) - 1} for lo, hi in edges]
    else:
        scale = {"m": 1000, "cm": 10}.get(str(units).strip(), 1)
        record.update(soil_db_mode=1, horizons=[
            {"horizon": index, "upper_depth": float(lo) * scale, "lower_depth": float(hi) * scale}
            for index, (lo, hi) in enumerate(edges, 1)])
    update_section(project_folder, section, record)
    from ...handlers.store.layout import is_v6
    from .advanced_inputs import record_products

    record_products(project_folder, kind, (target,), "6" if is_v6(project_folder) else "5.13")
    return (target, definition) if definition and definition.is_file() else (target,)


def _publish(replacements, removals, temporary: Path) -> None:
    """Replace one categorical output set without leaving half a set."""
    tracked = []
    for name in [destination for _source, destination in replacements] + list(removals):
        path = Path(name)
        if path not in tracked:
            tracked.append(path)
    backups = []
    published = []
    try:
        for index, path in enumerate(tracked):
            if path.is_file():
                backup = temporary / f".backup_{index}_{path.name}"
                os.replace(path, backup)
                backups.append((backup, path))
        for source, destination in replacements:
            os.replace(source, destination)
            published.append(Path(destination))
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        for backup, path in reversed(backups):
            if backup.is_file():
                os.replace(backup, path)
        raise


__all__ = [
    "SPECS",
    "copy_ready",
    "intermediate_paths",
    "normalized_mode",
    "ready_layers",
    "ready_paths",
    "record_namelist",
    "saved_outputs",
]
