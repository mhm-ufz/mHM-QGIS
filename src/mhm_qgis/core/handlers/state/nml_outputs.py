"""Register prepared files as schema-ready namelist values."""
from __future__ import annotations

from pathlib import Path

from .nml_settings import load_settings, relative_workspace_path, save_settings


METEO_FIELDS = {
    "pre": "pre", "pet": "pet", "tavg": "temp", "tann": "tann",
    "tmin": "tmin", "tmax": "tmax", "ssrd": "ssrd", "strd": "strd",
    "eabs": "eabs", "windspeed": "wind",
}
OBSERVATION_FIELDS = {
    "sm": "sm_path", "neutrons": "neutrons_path",
    "et": "et_path", "twsa": "tws_path",
}
OBSERVATION_TIME_FIELDS = {
    "sm": "sm_time_step", "et": "et_time_step", "twsa": "tws_time_step",
}
TIME_STEPS = {"Daily": -1, "Monthly": -2, "Yearly": -3}


def register_values(project_folder, profile, namelist, values, *, managed=()):
    state = load_settings(project_folder)
    registry = state.setdefault("namelist_values", {})
    target = registry.setdefault(profile, {}).setdefault(namelist, {})
    for field in managed:
        target.pop(field, None)
    target.update(values)
    save_settings(project_folder, state)


def register_meteo(project_folder, outputs, mask=None):
    values = {}
    for variable, path in outputs.items():
        stem = METEO_FIELDS.get(variable)
        if stem:
            values[f"{stem}_path"] = [_relative(project_folder, path)]
            values[f"{stem}_var"] = [variable]
    if mask:
        values.update(meteo_mask_path=[_relative(project_folder, mask)],
                      meteo_mask_var=["mask"])
    managed = {
        f"{stem}_{suffix}"
        for stem in METEO_FIELDS.values() for suffix in ("path", "var")
    } | {"meteo_mask_path", "meteo_mask_var"}
    register_values(
        project_folder, "main", "config_input", values, managed=managed)


def register_morphology(project_folder, *, bundle, dem, mask, latlon):
    bundle = _relative(project_folder, bundle)
    values = {
        "latlon_path": [_relative(project_folder, latlon)],
        "dem_path": [_relative(project_folder, dem)], "dem_var": ["dem"],
        "morph_mask_path": [_relative(project_folder, mask)],
        "morph_mask_var": ["mask"], "morph_lat_var": ["lat"],
        "morph_lon_var": ["lon"],
    }
    for field, variable in (
            ("slope", "slope"), ("aspect", "aspect"), ("fdir", "fdir"),
            ("facc", "facc"), ("geo_class", "geology_class"),
            ("soil_class", "soil_class")):
        values[f"{field}_path"] = [bundle]
        values[f"{field}_var"] = [variable]
    register_values(project_folder, "main", "config_input", values)


def register_observations(project_folder, outputs, specs=()):
    values = {
        OBSERVATION_FIELDS[variable]: [_relative(project_folder, path)]
        for variable, path in outputs.items() if variable in OBSERVATION_FIELDS
    }
    for spec in specs:
        field = OBSERVATION_TIME_FIELDS.get(spec.kind)
        cadence = spec.output_temporal_resolution or spec.temporal_resolution
        if field and cadence in TIME_STEPS:
            values[field] = TIME_STEPS[cadence]
    managed = set(OBSERVATION_FIELDS.values()) | set(
        OBSERVATION_TIME_FIELDS.values())
    register_values(
        project_folder, "main", "config_observations", values,
        managed=managed)
    state = load_settings(project_folder)
    state["observations"] = {
        variable: {"path": _relative(project_folder, path), "variable": variable}
        for variable, path in outputs.items()
    }
    save_settings(project_folder, state)


def _relative(project_folder, path):
    return relative_workspace_path(project_folder, Path(path))


__all__ = [
    "register_meteo", "register_morphology", "register_observations",
    "register_values",
]
