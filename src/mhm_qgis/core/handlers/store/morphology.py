"""Resolve prepared morphology outputs for the main display selector."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from ..lookup.manifests import period_datetime

from ..state.nml_settings import load_settings
from ..file.netcdf.raster import variables
from .paths import geometry_folder, workspace_folder
from .layout import MERGED_MASK_NAME, morph_folder, lai_folder
from .registry import first_existing, variants


@dataclass(frozen=True)
class DisplayOutput:
    path: Path
    name: str
    is_raster: bool = True
    band: int | None = None
    variable: str | None = None


def land_cover_periods(project_folder: str | Path) -> list[dict]:
    land_cover = load_settings(project_folder).get("land_cover", {})
    scenes = land_cover.get("scenes", []) if isinstance(land_cover, dict) else []
    return [scene for scene in scenes if isinstance(scene, dict)]


def resolve_display_output(
    project_folder: str | Path,
    key: str,
    *,
    year: int | None = None,
    when: datetime | None = None,
) -> DisplayOutput | None:
    project_folder = str(project_folder)
    geometry = Path(geometry_folder(project_folder))
    morph = Path(morph_folder(project_folder))
    workspace = Path(workspace_folder(project_folder))
    settings = load_settings(project_folder)

    if key == "land_cover":
        return _land_cover_output(settings, workspace, geometry, morph, when or (datetime(year, 1, 1) if year else None))
    if key == "soil":
        configured = _configured_path(settings.get("soil"), workspace)
        candidates = [morph / configured.name] if configured else []
        candidates += [
            morph / "soil_horizon_class.nc",
            morph / "soil_class.asc",
            *variants(geometry / "3_soil.tif"),
        ]
        if configured:
            candidates.append(configured)
        return _first(candidates, "Soil", variable="soil_class")
    if key == "domain_mask":
        merged_vector = geometry / "Watersheds" / "4_watershed_merged_vector.shp"
        if merged_vector.is_file():
            return DisplayOutput(merged_vector, "Domain Mask", is_raster=False)
        return _first(
            [
                geometry / "Watersheds" / MERGED_MASK_NAME,
                geometry / "Watersheds" / "4_watershed_DEM.tif",
            ],
            "Domain Mask",
        )

    derivative = {
        "dem": "dem", "slope": "slope", "aspect": "aspect",
        "flow_accumulation": "facc", "flow_direction": "fdir", "idgauges": "idgauges",
    }.get(key)
    if derivative:
        name = key.replace("_", " ").title()
        for suffix in ("_masked", "_crop", ""):
            path = geometry / f"dem_derivatives{suffix}.nc"
            variable = "dem_filled" if key == "dem" and not suffix else derivative
            if variable in variables(path):
                return DisplayOutput(path, name, variable=variable)
        if derivative in variables(morph / "input.nc"):
            return DisplayOutput(morph / "input.nc", name, variable=derivative)
        return _first([geometry / f"{derivative}.nc", morph / f"{derivative}.asc"], name, variable=derivative)

    candidates = {
        "geology": [
            morph / "geology_class.nc",
            morph / "geology_class.asc",
            *variants(geometry / "3_geology_processed.tif"),
        ],
        "lai": [
            Path(lai_folder(project_folder)) / "lai.nc",
        ],
    }.get(key, [])
    return _first(
        candidates,
        key.replace("_", " ").title(),
        variable={"lai": "lai", "geology": "geology_class"}.get(key),
    )


def _land_cover_output(settings, workspace, geometry, morph, when):
    land_cover = settings.get("land_cover", {})
    if not isinstance(land_cover, dict):
        land_cover = {}
    scenes = [scene for scene in land_cover.get("scenes", []) if isinstance(scene, dict)]
    selected_index = 0
    if scenes and when is not None:
        for index, scene in enumerate(scenes):
            try:
                start = period_datetime(scene.get("start_time") or str(scene["start_year"]))
                end = period_datetime(scene["end_time"]) if scene.get("end_time") else datetime(int(scene["end_year"]) + 1, 1, 1)
                if start <= when < end or (index == len(scenes) - 1 and when == end):
                    selected_index = index
                    break
            except (KeyError, TypeError, ValueError):
                continue
    if scenes:
        configured = _configured_path(scenes[selected_index], workspace)
        if configured and (morph / configured.name).is_file():
            configured = morph / configured.name
        if configured and configured.is_file():
            return DisplayOutput(
                configured, "Land Cover", band=1, variable="land_cover"
            )
    configured = _configured_path(land_cover, workspace)
    return _first(
        ([morph / configured.name] if configured else []) + [
            morph / "lc_periods.nc",
            morph / "lc.asc",
            *variants(geometry / "3_land_use.tif"),
        ] + ([configured] if configured else []),
        "Land Cover",
        band=selected_index + 1,
        variable="land_cover",
    )


def _configured_path(value, workspace):
    if not isinstance(value, dict):
        return None
    raw = value.get("output_path") or value.get("path") or value.get("filename")
    if not raw:
        return None
    path = Path(str(raw))
    if path.is_absolute():
        return path
    resolved = workspace / path
    if resolved.exists() or not path.parts or path.parts[0] != "data":
        return resolved
    if len(path.parts) > 1 and path.parts[1] == "master":
        return resolved
    return workspace / "data" / "master" / Path(*path.parts[1:])


def _first(candidates, name, band=None, variable=None):
    path = first_existing(candidates)
    return DisplayOutput(path, name, band=band, variable=variable) if path else None


__all__ = [
    "DisplayOutput",
    "land_cover_periods",
    "resolve_display_output",
]
