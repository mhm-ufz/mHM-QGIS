"""mHM v6 namelist initial values."""

from __future__ import annotations

from typing import Any

from ..common import (
    domain_count,
    geology_count,
    geology_rows,
    namelist_settings,
    model_data_path,
    repeat,
    resolution,
)


def build_dimensions(dialog: Any) -> dict[str, int]:
    return {
        "max_layers": 10,
        "n_domains": domain_count(dialog),
        "n_geo_units": geology_count(dialog),
    }


def build_initial_values(dialog: Any) -> dict[str, Any]:
    count = domain_count(dialog)
    settings = namelist_settings(dialog)
    data = model_data_path(dialog)
    domain_dems = _domain_dem_paths(settings, count, data)
    geo_count = geology_count(dialog)
    l1 = resolution(dialog, "current_l1_resolution")
    l11 = resolution(dialog, "current_l11_resolution")
    resolution_values: dict[str, Any] = {}
    if l1 is not None:
        resolution_values["hydro"] = repeat(l1, count)
    if l11 is not None:
        resolution_values["route"] = repeat(l11, count)

    values: dict[str, Any] = {
        "main": {
            "config_project": {
                "n_domains": count,
                "n_geo_units": geo_count,
                "max_layers": 10,
            },
            "config_resolution": resolution_values,
            "config_input": {
                "latlon_path": repeat(f"{data}/latlon.nc", count),
                "meteo_mask_path": repeat(
                    f"{data}/meteo/mask.nc", count),
                "pre_path": repeat(f"{data}/meteo/pre.nc", count),
                "pet_path": repeat(f"{data}/meteo/pet.nc", count),
                "temp_path": repeat(f"{data}/meteo/tavg.nc", count),
                "tann_path": repeat(f"{data}/meteo/tann.nc", count),
                "tmin_path": repeat(f"{data}/meteo/tmin.nc", count),
                "tmax_path": repeat(f"{data}/meteo/tmax.nc", count),
                "ssrd_path": repeat(f"{data}/meteo/ssrd.nc", count),
                "strd_path": repeat(f"{data}/meteo/strd.nc", count),
                "netrad_path": repeat(f"{data}/meteo/net_rad.nc", count),
                "eabs_path": repeat(f"{data}/meteo/eabs.nc", count),
                "wind_path": repeat(f"{data}/meteo/windspeed.nc", count),
                "hydro_mask_path": domain_dems,
                "dem_path": domain_dems,
                "slope_path": repeat(f"{data}/morph/input.nc", count),
                "aspect_path": repeat(f"{data}/morph/input.nc", count),
                "fdir_path": repeat(f"{data}/morph/input.nc", count),
                "facc_path": repeat(f"{data}/morph/input.nc", count),
                "geo_class_path": repeat(f"{data}/morph/input.nc", count),
                "soil_class_path": repeat(f"{data}/morph/input.nc", count),
                "lai_class_path": repeat("", count),
                "morph_mask_path": domain_dems,
            },
            "config_mpr": {
                "land_cover_path": repeat(f"{data}/morph/lc_periods.nc", count),
                "lai_path": repeat(f"{data}/morph/lai.nc", count),
                "soil_lut_path": repeat(
                    f"{data}/morph/soil_classdefinition.txt", count
                ),
                "geo_lut_path": repeat(
                    f"{data}/morph/geology_classdefinition.txt", count
                ),
                "lai_lut_path": repeat(
                    f"{data}/morph/LAI_classdefinition.txt", count
                ),
                "restart_input_path": repeat("restart/mpr_restart_in.nc", count),
                "restart_output_path": repeat("restart/mpr_restart_out.nc", count),
            },
            "config_mhm": {
                "output_path": repeat("output/mhm_output.nc", count),
                "restart_input_path": repeat("restart/mhm_restart_in.nc", count),
                "restart_output_path": repeat("restart/mhm_restart_out.nc", count),
            },
            "config_mrm": {
                "scc_gauges_path": repeat(f"{data}/morph/idgauges.asc", count),
                "output_path": repeat("output/mrm_output.nc", count),
                "output_node_path": repeat("output/mrm_node_output.nc", count),
                "restart_input_path": repeat("restart/mrm_restart_in.nc", count),
                "restart_output_path": repeat("restart/mrm_restart_out.nc", count),
                "diagnostics_path": repeat("output/mrm_diagnostics.nc", count),
            },
            "config_time": {"time_step": repeat(1, count)},
        }
    }
    rows = geology_rows(dialog)
    if rows:
        parameters = [dict(value=100.0, min=1.0, max=1000.0, optimize=True)
                      for _ in range(geo_count)]
        for row in rows:
            if row["value"] is not None:
                parameters[row["index"] - 1]["value"] = float(row["value"])
        values["parameter"] = {"baseflow_1": {"baseflow_recession": parameters}}
    if settings.get("time"):
        from ...core.handlers.state.nml_settings import validate_times
        values["main"]["config_time"].update({
            name: repeat(value, count)
            for name, value in validate_times(settings["time"]).items()
        })
    _apply_land_cover(values["main"], settings, count)
    _apply_soil(values["main"], settings, count)
    _apply_lai(values["main"], settings, count)
    _apply_registered(values, settings)
    return values


def _apply_registered(values, settings):
    """Overlay only outputs explicitly registered by preprocessing."""
    for profile, namelists in settings.get("namelist_values", {}).items():
        if profile not in values or not isinstance(namelists, dict):
            continue
        for namelist, fields in namelists.items():
            if isinstance(fields, dict):
                values[profile].setdefault(namelist, {}).update(fields)


def _domain_dem_paths(settings: dict[str, Any], count: int, data="data/master") -> list[str]:
    domains = settings.get("domains", [])
    if not isinstance(domains, list):
        domains = []
    ordered = sorted(
        (item for item in domains if isinstance(item, dict)),
        key=lambda item: int(item.get("domain_id", 0) or 0),
    )
    values = [
        str(item.get("dem_path", "") or f"{data}/morph/dem.nc")
        for item in ordered[:count]
    ]
    return values + [f"{data}/morph/dem.nc" for _ in range(max(0, count - len(values)))]


def _apply_land_cover(main, settings, count):
    land_cover = settings.get("land_cover", {})
    if not isinstance(land_cover, dict):
        return
    path = land_cover.get("output_path") or land_cover.get("path")
    if path:
        main["config_mpr"]["land_cover_path"] = repeat(str(path), count)
    variable = land_cover.get("variable")
    if variable:
        main["config_mpr"]["land_cover_var"] = repeat(str(variable), count)


def _apply_soil(main, settings, count):
    soil = settings.get("soil", {})
    horizons = soil.get("horizons", []) if isinstance(soil, dict) else []
    if not isinstance(soil, dict):
        return
    mode = int(soil.get("soil_db_mode", 1 if horizons else 0))
    path = soil.get("output_path") or soil.get("path")
    if path:
        field = "soil_horizon_class_path" if mode == 1 else "soil_class_path"
        main["config_input"][field] = repeat(str(path), count)
    variable = soil.get("variable")
    if variable:
        main["config_input"]["soil_class_var"] = repeat(str(variable), count)
    lookup = soil.get("classdefinition_path") or soil.get("lut_path")
    if lookup:
        main["config_mpr"]["soil_lut_path"] = repeat(str(lookup), count)
    if not horizons:
        main["config_mpr"]["soil_db_mode"] = repeat(mode, count)
        return
    lower_depths = []
    for horizon in horizons[:10]:
        if not isinstance(horizon, dict):
            continue
        try:
            lower_depths.append(int(round(float(horizon["lower_depth"]))))
        except (KeyError, TypeError, ValueError):
            continue
    if not lower_depths:
        return
    main["config_mpr"].update(
        {
            "soil_db_mode": repeat(mode, count),
            "tillage_depth": repeat(
                int(soil.get("tillage_depth") or lower_depths[0]), count
            ),
            "n_layers": repeat(len(lower_depths), count),
            "soil_depth": [
                repeat(depth, count) for depth in lower_depths
            ]
            + [repeat(0, count) for _ in range(10 - len(lower_depths))],
        }
    )


def _apply_lai(main, settings, count):
    lai = settings.get("lai", {})
    if not isinstance(lai, dict):
        return
    try:
        time_step = int(lai.get("time_step"))
    except (TypeError, ValueError):
        return
    main["config_mpr"]["lai_time_step"] = repeat(time_step, count)
    path = lai.get("output_path")
    if path:
        main["config_mpr"]["lai_path"] = repeat(str(path), count)
    variable = lai.get("variable") or "lai"
    main["config_mpr"]["lai_var"] = repeat(str(variable), count)


__all__ = ["build_dimensions", "build_initial_values"]
