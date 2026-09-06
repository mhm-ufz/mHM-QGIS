"""Prepare and stage advanced land-cover and soil inputs through mHM-tools."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from ...handlers.lookup import (
    LandUseInput,
    SoilInput,
    write_land_use_manifest,
    write_soil_manifest,
)
from ....applications.mhm_tools_handler import prepare_land_cover_periods, prepare_soil_horizons
from ...handlers.state.nml_settings import relative_workspace_path, update_section
from ...handlers.store.paths import geometry_folder, morph_staging_folder


def process_land_cover_input(
    project_folder,
    version,
    value: LandUseInput,
    dem_file,
    *,
    log=None,
) -> tuple[Path, ...]:
    """Write a period manifest and stage outputs for common-L0 publication."""
    manifest = _manifest_path(project_folder, "land_cover")
    write_land_use_manifest(value, manifest)
    output_type = "asc" if str(version).startswith("5") else "nc"
    with TemporaryDirectory(
        prefix="mhm_qgis_land_cover_", dir=geometry_folder(project_folder)
    ) as temporary:
        prepare_land_cover_periods(
            manifest,
            dem_file,
            temporary,
            value.lookup_table,
            value.mapping_field,
            value.class_field,
            output_type=output_type,
            log=log,
        )
        published = _publish_directory(temporary, morph_staging_folder(project_folder))

    data_outputs = tuple(
        path for path in published if path.suffix.lower() in {".asc", ".nc"}
    )
    if not data_outputs:
        raise RuntimeError("Land-cover formatting did not create model input files.")
    scenes = []
    asc_outputs = [path for path in data_outputs if path.suffix.lower() == ".asc"]
    for index, period in enumerate(value.periods):
        scene = {
            "start_year": period.start_year,
            "end_year": period.end_year,
            "source_path": str(period.file_path),
        }
        if index < len(asc_outputs):
            scene["output_path"] = relative_workspace_path(
                project_folder, asc_outputs[index]
            )
        scenes.append(scene)
    shared = next(
        (path for path in data_outputs if path.suffix.lower() == ".nc"), None
    )
    update_section(
        project_folder,
        "land_cover",
        {
            "mode": "historical" if len(scenes) > 1 else "single",
            "manifest_path": relative_workspace_path(project_folder, manifest),
            "output_path": (
                relative_workspace_path(project_folder, shared) if shared else ""
            ),
            "variable": "land_cover",
            "lookup_table": str(value.lookup_table),
            "mapping_field": value.mapping_field,
            "class_field": value.class_field,
            "scenes": scenes,
        },
    )
    return data_outputs


def process_soil_input(
    project_folder,
    version,
    value: SoilInput,
    dem_file,
    *,
    log=None,
) -> tuple[Path, Path]:
    """Write a horizon manifest and stage outputs for common-L0 publication."""
    manifest = _manifest_path(project_folder, "soil")
    write_soil_manifest(value, manifest)
    output_type = "asc" if str(version).startswith("5") else "nc"
    with TemporaryDirectory(
        prefix="mhm_qgis_soil_", dir=geometry_folder(project_folder)
    ) as temporary:
        data, definition = prepare_soil_horizons(
            manifest,
            dem_file,
            temporary,
            output_type=output_type,
            log=log,
        )
        published = _publish_directory(temporary, morph_staging_folder(project_folder))

    data_path = _published_match(published, data.name)
    definition_path = _published_match(published, definition.name)
    horizons = [
        {
            "horizon": item.horizon,
            "upper_depth": item.upper_depth,
            "lower_depth": item.lower_depth,
            "clay_layer": str(item.clay_layer),
            "sand_layer": str(item.sand_layer),
            "silt_layer": str(item.silt_layer),
            "bulk_density_layer": str(item.bulk_density_layer),
        }
        for item in value.horizons
    ]
    update_section(
        project_folder,
        "soil",
        {
            "mode": "multi_horizon",
            "soil_db_mode": 0 if output_type == "asc" else 1,
            "manifest_path": relative_workspace_path(project_folder, manifest),
            "output_path": relative_workspace_path(project_folder, data_path),
            "variable": "soil_class",
            "classdefinition_path": relative_workspace_path(
                project_folder, definition_path
            ),
            "source_bulk_density_unit": value.bulk_density_unit,
            "bulk_density_unit": "g/cm3",
            "composition_normalization": "component_sum_percent",
            "lower_depths": list(value.lower_depths),
            "horizons": horizons,
        },
    )
    return data_path, definition_path


def _manifest_path(project_folder, name):
    folder = Path(geometry_folder(project_folder)) / "format-data" / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "format-data.csv"


def _publish_directory(source, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    published = []
    for path in sorted(Path(source).iterdir()):
        if not path.is_file():
            continue
        target = destination / path.name
        os.replace(path, target)
        published.append(target)
    return tuple(published)


def _published_match(outputs, name):
    for output in outputs:
        if output.name == name:
            return output
    raise FileNotFoundError(f"Formatted output was not published: {name}")


__all__ = [
    "process_land_cover_input",
    "process_soil_input",
]
