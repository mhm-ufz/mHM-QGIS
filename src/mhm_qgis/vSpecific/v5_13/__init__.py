"""mHM 5.13 legacy namelist initial values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common import (
    coordinate_flag,
    domain_count,
    geology_count,
    geoparameter,
    model_data_path,
    namelist_settings,
    repeat,
    resolution,
)


def build_dimensions(dialog: Any) -> dict[str, int]:
    domains = domain_count(dialog)
    settings = namelist_settings(dialog)
    return {
        "nDomains": domains,
        "nGeoUnits": geology_count(dialog),
        "nSoilHorizons": _soil_horizons(settings),
        "nLcoverScene": _land_cover_scenes(settings),
        "nGaugesTotal": _gauges_per_domain(settings, domains),
    }


def build_initial_values(dialog: Any) -> dict[str, Any]:
    count = domain_count(dialog)
    settings = namelist_settings(dialog)
    data = model_data_path(dialog)
    domain_directories = _domain_directories(settings, count, data)
    l1 = resolution(dialog, "current_l1_resolution")
    l11 = resolution(dialog, "current_l11_resolution")
    mainconfig: dict[str, Any] = {
        "iFlag_cordinate_sys": coordinate_flag(dialog),
        "nDomains": count,
        "L0Domain": list(range(1, count + 1)),
        "read_opt_domain_data": repeat(0, count),
    }
    if l1 is not None:
        mainconfig["resolution_Hydrology"] = repeat(l1, count)

    shared: dict[str, Any] = {
        "mhm_file_RestartIn": repeat("restart/mhm_restart_in.nc", count),
        "mrm_file_RestartIn": repeat("restart/mrm_restart_in.nc", count),
        "timestep": 1,
    }
    if l11 is not None:
        shared["resolution_Routing"] = repeat(l11, count)

    values: dict[str, Any] = {
        "main": {
            "mainconfig": mainconfig,
            "mainconfig_mhm_mrm": shared,
            "directories_general": {
                "dirConfigOut": "output/",
                "dirCommonFiles": f"{data}/static/morph/",
                "dir_Morpho": domain_directories,
                "dir_LCover": repeat(f"{data}/static/morph/", count),
                "mhm_file_RestartOut": repeat("restart/mhm_restart_out.nc", count),
                "mrm_file_RestartOut": repeat("restart/mrm_restart_out.nc", count),
                "dir_Out": repeat("output/", count),
                "file_LatLon": repeat(f"{data}/latlon.nc", count),
            },
            "directories_mHM": {
                "inputFormat_meteo_forcings": "nc",
                "dir_Precipitation": repeat(f"{data}/meteo/pre/", count),
                "dir_Temperature": repeat(f"{data}/meteo/tavg/", count),
                "dir_ReferenceET": repeat(f"{data}/meteo/pet/", count),
                "dir_MinTemperature": repeat(f"{data}/meteo/tmin/", count),
                "dir_MaxTemperature": repeat(f"{data}/meteo/tmax/", count),
                "dir_NetRadiation": repeat(f"{data}/meteo/netrad/", count),
                "dir_absVapPressure": repeat(f"{data}/meteo/eabs/", count),
                "dir_windspeed": repeat(f"{data}/meteo/windspeed/", count),
                "dir_Radiation": repeat(f"{data}/meteo/ssrd/", count),
                "time_step_model_inputs": repeat(0, count),
            },
            "directories_mRM": {
                "dir_Gauges": repeat(f"{data}/observation/streamflow/", count),
                "dir_Total_Runoff": repeat("output/", count),
                "dir_Bankfull_Runoff": repeat(f"{data}/static/morph/", count),
            },
            "LCover": _land_cover_values(settings),
            "directories_MPR": {
                "dir_gridded_LAI": repeat(f"{data}/lai/", count),
            },
        }
    }
    geo_count = geology_count(dialog)
    geo_values = geoparameter(dialog, geo_count, component_major=False)
    if geo_values is not None:
        values["parameter"] = {"geoparameter": {"GeoParam": geo_values}}
    soil = _soil_values(settings)
    if soil:
        values["main"]["soildata"] = soil
    gauges = _gauge_values(settings, count)
    if gauges:
        values["main"]["evaluation_gauges"] = gauges
    lai = _lai_values(settings)
    if lai:
        values["main"]["LAI_data_information"] = lai
    if settings.get("time"):
        from datetime import datetime

        from ...core.handlers.state.nml_settings import validate_times
        times = validate_times(settings["time"], "v5.13")
        start, evaluation, end = (datetime.fromisoformat(times[key])
                                  for key in ("sim_start", "eval_start", "sim_end"))
        values["main"]["time_periods"] = {
            "warming_Days": repeat((evaluation - start).days, count),
            "eval_Per": repeat(dict(yStart=evaluation.year, mStart=evaluation.month,
                                    dStart=evaluation.day, yEnd=end.year,
                                    mEnd=end.month, dEnd=end.day), count),
        }
    return values


def _domain_directories(settings: dict[str, Any], count: int, data="data") -> list[str]:
    domains = settings.get("domains", [])
    if not isinstance(domains, list):
        domains = []
    ordered = sorted(
        (item for item in domains if isinstance(item, dict)),
        key=lambda item: int(item.get("domain_id", 0) or 0),
    )
    values = []
    for item in ordered[:count]:
        directory = str(item.get("directory", "") or "").rstrip("/")
        values.append(f"{directory}/" if directory else f"{data}/static/morph/")
    return values + [
        f"{data}/static/morph/" for _ in range(max(0, count - len(values)))
    ]


def _land_cover_values(settings: dict[str, Any]) -> dict[str, Any]:
    configured = settings.get("land_cover", {})
    scenes = configured.get("scenes", []) if isinstance(configured, dict) else []
    valid = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        try:
            start = int(scene["start_year"])
            end = int(scene["end_year"])
        except (KeyError, TypeError, ValueError):
            continue
        path = scene.get("output_path") or scene.get("path") or scene.get("filename")
        if path:
            valid.append((start, end, Path(str(path)).name))
    if not valid:
        valid = [(1900, 2100, "lc.asc")]
    return {
        "nLCoverScene": len(valid),
        "LCoverYearStart": [item[0] for item in valid],
        "LCoverYearEnd": [item[1] for item in valid],
        "LCoverfName": [item[2] for item in valid],
    }


def _land_cover_scenes(settings: dict[str, Any]) -> int:
    return len(_land_cover_values(settings)["LCoverfName"])


def _soil_values(settings: dict[str, Any]) -> dict[str, Any]:
    soil = settings.get("soil", {})
    horizons = soil.get("horizons", []) if isinstance(soil, dict) else []
    depths = []
    for horizon in horizons[:10]:
        if not isinstance(horizon, dict):
            continue
        try:
            depths.append(float(horizon["lower_depth"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not depths:
        return {}
    tillage = int(soil.get("tillage_depth") or depths[0])
    return {
        "iFlag_soilDB": 1,
        "tillageDepth": tillage,
        "nSoilHorizons_mHM": len(depths),
        "soil_Depth": depths,
    }


def _soil_horizons(settings: dict[str, Any]) -> int:
    return len(_soil_values(settings).get("soil_Depth", ())) or 10


def _gauge_values(settings: dict[str, Any], count: int) -> dict[str, Any]:
    gauges = settings.get("gauges", [])
    if not isinstance(gauges, list) or not gauges:
        return {}
    rows = [[] for _ in range(count)]
    for gauge in gauges:
        if not isinstance(gauge, dict):
            continue
        try:
            gauge_id = int(gauge["gauge_id"])
        except (KeyError, TypeError, ValueError):
            continue
        filename = str(gauge.get("gauge_filename") or f"{gauge_id}.txt")
        for domain_id in gauge.get("domain_ids", []):
            try:
                index = int(domain_id) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < count:
                rows[index].append((gauge_id, Path(filename).name))
    # Rectangular rows: pad to the widest, not to a fixed maximum.
    widest = max((len(row) for row in rows), default=0)
    identifiers = [
        [item[0] for item in row] + [0] * (widest - len(row)) for row in rows
    ]
    filenames = [
        [item[1] for item in row] + [""] * (widest - len(row)) for row in rows
    ]
    return {
        # mHM reads nGaugesTotal as the "sum of all gauges in all subbains", so a
        # gauge shared by several domains counts once per domain. Counting distinct
        # gauge ids instead undersizes the gauge arrays mHM allocates from it.
        "nGaugesTotal": sum(len(row) for row in rows),
        "NoGauges_domain": [len(row) for row in rows],
        "Gauge_id": identifiers,
        "gauge_filename": filenames,
    }


def _gauges_per_domain(settings: dict[str, Any], count: int) -> int:
    identifiers = _gauge_values(settings, count).get("Gauge_id", ())
    return max((len(row) for row in identifiers), default=0) or 1


def _lai_values(settings: dict[str, Any]) -> dict[str, Any]:
    lai = settings.get("lai", {})
    if not isinstance(lai, dict):
        return {}
    if lai.get("mode") == "mhm_ready":
        return {
            "timeStep_LAI_input": 0,
            "inputFormat_gridded_LAI": "nc",
        }
    try:
        time_step = int(lai.get("time_step"))
    except (TypeError, ValueError):
        return {}
    return {
        "timeStep_LAI_input": time_step,
        "inputFormat_gridded_LAI": "nc",
    }


__all__ = ["build_dimensions", "build_initial_values"]
