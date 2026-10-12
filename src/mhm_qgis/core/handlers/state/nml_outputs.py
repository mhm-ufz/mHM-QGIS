"""Register prepared files as schema-ready namelist values.

The fields are v6 namelist names (`config_input`). A v5.13 setup reads its inputs
from the fixed `dir_*` folders `vSpecific/v5_13` builds, so writers pass `v6`
(the caller knows the version; this state module must not import the store
layout) and a v5.13 setup gets no `namelist_values`, only the removal of stale ones.
"""
from __future__ import annotations

from pathlib import Path

from .nml_settings import load_settings, relative_workspace_path, save_settings

METEO_FIELDS = {
    "pre": "pre", "pet": "pet", "tavg": "temp", "tann": "tann",
    "tmin": "tmin", "tmax": "tmax", "ssrd": "ssrd", "strd": "strd",
    "netrad": "netrad", "eabs": "eabs", "windspeed": "wind",
}
OBSERVATION_FIELDS = {
    "sm": "sm_path", "neutrons": "neutrons_path",
    "et": "et_path", "twsa": "tws_path",
}
OBSERVATION_TIME_FIELDS = {
    "sm": "sm_time_step", "et": "et_time_step", "twsa": "tws_time_step",
}
TIME_STEPS = {"Daily": -1, "Monthly": -2, "Yearly": -3}


def register_values(project_folder, profile, namelist, values, *, managed=(), v6=True):
    state = load_settings(project_folder)
    if not v6:
        if state.pop("namelist_values", None) is not None:
            save_settings(project_folder, state)
        return
    registry = state.setdefault("namelist_values", {})
    target = registry.setdefault(profile, {}).setdefault(namelist, {})
    for field in managed:
        target.pop(field, None)
    target.update(values)
    save_settings(project_folder, state)


def register_meteo(project_folder, outputs, mask=None, *, v6):
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
        project_folder, "main", "config_input", values, managed=managed, v6=v6)


BUNDLE_FIELDS = (("slope", "slope"), ("aspect", "aspect"), ("fdir", "fdir"), ("facc", "facc"),
                 ("geo_class", "geology_class"), ("soil_class", "soil_class"))
MORPHOLOGY_FIELDS = {"latlon_path", "dem_path", "dem_var", "morph_mask_path", "morph_mask_var", "morph_lat_var",
                     "morph_lon_var", "soil_horizon_class_path",
                     *(f"{field}_{suffix}" for field, _ in BUNDLE_FIELDS for suffix in ("path", "var"))}


def register_morphology(project_folder, *, bundle, dem, mask, latlon):
    """v6 only: its caller is the v6 branch of the publication stage.

    Registers what the published files hold: a bundle field only when `input.nc` has
    that variable, and for multi-horizon soil (`soil_db_mode` 1) the published
    `soil_horizon_class.nc` with its class variable, which mHM v6 reads through
    `soil_class_var` (it has no own variable name)."""
    held = _variables(bundle)  # None when unreadable: register as declared
    horizons = Path(bundle).with_name("soil_horizon_class.nc")
    soil = load_settings(project_folder).get("soil") or {}
    layered = isinstance(soil, dict) and int(soil.get("soil_db_mode") or 0) == 1 and horizons.is_file()
    values = {
        "latlon_path": [_relative(project_folder, latlon)],
        "dem_path": [_relative(project_folder, dem)], "dem_var": ["dem"],
        "morph_mask_path": [_relative(project_folder, mask)],
        "morph_mask_var": ["mask"], "morph_lat_var": ["lat"],
        "morph_lon_var": ["lon"],
    }
    for field, variable in BUNDLE_FIELDS:
        if (held is None or variable in held) and not (layered and field == "soil_class"):
            values[f"{field}_path"] = [_relative(project_folder, bundle)]
            values[f"{field}_var"] = [variable]
    if layered:
        variable = next((name for name, ndim in (_variables(horizons) or {}).items() if ndim == 3), "soil_class")
        values.update(soil_horizon_class_path=[_relative(project_folder, horizons)], soil_class_var=[variable])
    register_values(project_folder, "main", "config_input", values, managed=MORPHOLOGY_FIELDS)


def _variables(path) -> dict[str, int] | None:
    """A NetCDF file's variables and their ranks; None when it can't be read."""
    try:
        import netCDF4

        with netCDF4.Dataset(path) as handle:
            return {name: variable.ndim for name, variable in handle.variables.items()}
    except (OSError, ImportError):
        return None


def register_observations(project_folder, outputs, specs=(), *, v6):
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
        managed=managed, v6=v6)
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
