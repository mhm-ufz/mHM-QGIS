"""Prepare and stage advanced land-cover and soil inputs through mHM-tools."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from ....externals.mhm_tools_handler import (
    prepare_land_cover_periods,
    prepare_soil_horizons,
)
from ...handlers.lookup import (
    LandUseInput,
    SoilInput,
    validate_land_use_input,
    write_land_use_manifest,
    write_soil_manifest,
)
from ...handlers.state.nml_settings import (
    load_settings,
    relative_workspace_path,
    update_section,
)
from ...handlers.store.layout import morph_folder
from ...handlers.store.paths import morph_staging_folder, temp_morphology_folder


def input_fingerprint(value, dem_file, version):
    """Fingerprint all selected source files and their formatting options."""
    from ...handlers.state.cache import fingerprint
    paths = ([value.lookup_table, *(period.file_path for period in value.periods)]
             if isinstance(value, LandUseInput) else
             [getattr(horizon, name) for horizon in value.horizons
              for name in ("clay_layer", "sand_layer", "silt_layer", "bulk_density_layer")])
    return fingerprint(
        [dem_file, *paths],
        {"inputs": value.as_dict(), "version": version, "format": "common-l0-2"},
    )


def process_land_cover_input(
    project_folder,
    version,
    value: LandUseInput,
    dem_file,
    *,
    log=None,
) -> tuple[Path, ...]:
    """Write a period manifest and stage outputs for common-L0 publication."""
    times = load_settings(project_folder).get("time", {})
    validate_land_use_input(value, simulation_start=times.get("sim_start"),
                            simulation_end=times.get("sim_end"), version=version)
    manifest = _manifest_path(project_folder, "land_cover")
    write_land_use_manifest(value, manifest)
    output_type = "nc"
    with TemporaryDirectory(
        prefix="mhm_qgis_land_cover_", dir=temp_morphology_folder(project_folder)
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

    data_outputs = tuple(path for path in published if path.suffix.lower() == ".nc")
    if not data_outputs:
        raise RuntimeError("Land-cover formatting did not create model input files.")
    scenes = []
    for index, period in enumerate(value.periods):
        end_year = (
            period.end_time.year
            if times.get("sim_end") and index == len(value.periods) - 1
            else period.end_year
        )
        scene = {
            "start_time": period.start_time.isoformat(),
            "end_time": period.end_time.isoformat(),
            "start_year": period.start_year,
            "end_year": end_year,
            "source_path": str(period.file_path),
        }
        if str(version).startswith("5"):
            scene["output_path"] = relative_workspace_path(
                project_folder,
                Path(morph_folder(project_folder))
                / _v5_scene_name(period.start_year, end_year),
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
    _version,
    value: SoilInput,
    dem_file,
    *,
    log=None,
) -> tuple[Path, Path]:
    """Write a horizon manifest and stage outputs for common-L0 publication."""
    manifest = _manifest_path(project_folder, "soil")
    write_soil_manifest(value, manifest)
    with TemporaryDirectory(
        prefix="mhm_qgis_soil_", dir=temp_morphology_folder(project_folder)
    ) as temporary:
        data, definition = prepare_soil_horizons(
            manifest,
            dem_file,
            temporary,
            output_type="nc",
            composition_step=value.composition_step,
            bulk_density_step=value.bulk_density_step,
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
            "soil_db_mode": 1,
            "manifest_path": relative_workspace_path(project_folder, manifest),
            "output_path": relative_workspace_path(project_folder, data_path),
            "variable": "soil_class",
            "classdefinition_path": relative_workspace_path(
                project_folder, definition_path
            ),
            "source_bulk_density_unit": value.bulk_density_unit,
            "bulk_density_unit": "g/cm3",
            "composition_step": value.composition_step,
            "bulk_density_step": value.bulk_density_step,
            "composition_normalization": "component_sum_percent",
            "lower_depths": list(value.lower_depths),
            "horizons": horizons,
        },
    )
    return data_path, definition_path


def _v5_scene_name(start_year, end_year) -> str:
    """The ASCII grid v5.13 reads for one land-cover period."""
    return f"lc_{start_year}_{end_year}.asc"


def record_products(project_folder, kind, outputs, version) -> None:
    """Point the soil or land-cover handoff at a finished stage's products, fresh or reused.

    Accepting the input dialog again replaces the section with its inputs only, and an
    unchanged stage is then reused without running, so its products are recorded here
    (`output_path`, soil's `classdefinition_path`, `manifest_path`, v5.13 scene paths).
    Paths name the published copy when it exists; publication repoints staged ones."""
    section = "soil" if kind == "soil" else "land_cover"
    settings = load_settings(project_folder)
    record = dict(settings.get(section) or {})
    master = Path(morph_folder(project_folder))

    def located(path):
        published = master / path.name
        return relative_workspace_path(project_folder, published if published.is_file() else path)

    paths = [Path(path) for path in outputs]
    data = next((path for path in paths if path.suffix.lower() == ".nc"), None)
    definition = next((path for path in paths if path.suffix.lower() == ".txt"), None)
    if data is not None:
        record["output_path"] = located(data)
    if definition is not None and section == "soil":
        record["classdefinition_path"] = located(definition)
    manifest = Path(temp_morphology_folder(project_folder)) / "format-data" / section / "format-data.csv"
    if manifest.is_file():
        record["manifest_path"] = relative_workspace_path(project_folder, manifest)
    if section == "land_cover" and str(version).startswith("5"):
        scenes = record.get("scenes") or []
        if scenes and settings.get("time", {}).get("sim_end"):
            scenes[-1]["end_year"] = int(settings["time"]["sim_end"][:4])
        for scene in scenes:
            if isinstance(scene, dict) and scene.get("start_year") and scene.get("end_year"):
                scene["output_path"] = relative_workspace_path(
                    project_folder, master / _v5_scene_name(scene["start_year"], scene["end_year"]))
    elif section == "land_cover":
        scenes = record.get("scenes") or []
        if scenes and settings.get("time", {}).get("sim_end"):
            scenes[-1]["end_year"] = int(settings["time"]["sim_end"][:4])
    update_section(project_folder, section, record)


def _manifest_path(project_folder, name):
    folder = Path(temp_morphology_folder(project_folder)) / "format-data" / name
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
    "record_products",
]
