"""Flood events of a gauge and how well the run reproduces each (Tier B, §7.7).

Events are found on observed quickflow (Q minus the Lyne-Hollick baseflow), or on
the simulation when there is no observation. The parameters live in `checks.yml`.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .signatures import baseflow

RANKINGS = ("rainfall", "observed peak", "abs peak error", "abs volume error")


@cache
def defaults() -> dict:
    return yaml.safe_load((Path(__file__).with_name("checks.yml")).read_text(encoding="utf-8"))["events"]


def detect(q, f_on=None, f_off=None, merge_gap=None, min_peak=None):
    """(start, end) index pairs, end inclusive, of the events in a discharge series."""
    options = defaults()
    f_on, f_off = f_on or options["f_on"], f_off or options["f_off"]
    merge_gap = options["merge_gap"] if merge_gap is None else merge_gap
    min_peak = options["min_peak"] if min_peak is None else min_peak
    flow = np.nan_to_num(np.asarray(q, float))
    quick = flow - baseflow(flow)
    on = f_on * np.percentile(quick, 99)
    off = f_off * on
    events, t = [], 0
    while t < len(quick):
        if quick[t] > on:
            start = t
            while start > 0 and quick[start - 1] > off:
                start -= 1
            end = t
            while end + 1 < len(quick) and quick[end + 1] > off:
                end += 1
            events.append([start, end])
            t = end + 1
        else:
            t += 1
    merged = []
    for start, end in events:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged if flow[a:b + 1].max() >= min_peak]


def table(frame: pd.DataFrame, rain=None, area_km2=None, max_lag=None) -> pd.DataFrame:
    """One row per event of a gauge frame (obs, sim; m³ s⁻¹), with rainfall in mm
    from `rain` (aligned) and quickflow volumes in mm from the catchment area."""
    max_lag = defaults()["max_lag"] if max_lag is None else max_lag
    observed = frame["obs"].notna().any()
    reference = frame["obs"] if observed else frame["sim"]
    step_days = float(np.median(np.diff(frame.index.values) / np.timedelta64(1, "D")))
    rows = []
    names = (("obs", "observed"), ("sim", "simulated")) if observed else (("sim", "simulated"),)
    quick = {name: frame[name].to_numpy(float) - baseflow(frame[name].to_numpy(float)) for name, _ in names}
    for start, end in detect(reference.to_numpy()):
        window = slice(start, end + 1)
        times = frame.index[window]
        row = {"start": frame.index[start], "end": frame.index[end]}
        if rain is not None:
            row["rainfall"] = float(np.nansum(rain.to_numpy(float)[max(0, start - max_lag):end + 1]))
        for name, label in names:
            values = frame[name].to_numpy(float)[window]
            row[f"{label} peak"] = float(np.nanmax(values)) if np.isfinite(values).any() else np.nan
            if area_km2:
                volume = np.nansum(np.clip(quick[name][window], 0, None)) * step_days * 86400 / (area_km2 * 1e6) * 1e3
                row[f"{label} quickflow (mm)"] = float(volume)
        if observed:
            obs, sim = frame["obs"].to_numpy(float)[window], frame["sim"].to_numpy(float)[window]
            row["peak error (%)"] = 100 * (row["simulated peak"] - row["observed peak"]) / row["observed peak"]
            row["timing error (days)"] = float((times[np.nanargmax(sim)] - times[np.nanargmax(obs)])
                                               / np.timedelta64(1, "D"))
            if area_km2:
                row["volume error (%)"] = 100 * (row["simulated quickflow (mm)"] / row["observed quickflow (mm)"] - 1)
        if rain is not None and area_km2 and row.get("rainfall"):
            key = "observed" if observed else "simulated"
            row["runoff coefficient"] = row[f"{key} quickflow (mm)"] / row["rainfall"]
            rainfall = rain.to_numpy(float)[max(0, start - max_lag):end + 1]
            rain_times = frame.index[max(0, start - max_lag):end + 1]
            flow = np.clip(quick["obs" if observed else "sim"][window], 0, None)
            if np.nansum(rainfall) > 0 and flow.sum() > 0:
                origin = rain_times[0]
                rain_centre = np.average((rain_times - origin) / np.timedelta64(1, "D"), weights=np.nan_to_num(rainfall))
                flow_centre = np.average((times - origin) / np.timedelta64(1, "D"), weights=flow)
                row["centroid lag (days)"] = float(flow_centre - rain_centre)
        rows.append(row)
    result = pd.DataFrame(rows)
    if observed and len(result):
        result["abs peak error"] = result["peak error (%)"].abs()
        if "volume error (%)" in result:
            result["abs volume error"] = result["volume error (%)"].abs()
    return result


def ranked(events: pd.DataFrame, key="observed peak") -> pd.DataFrame:
    """Events sorted by a ranking key (largest first) when the table has it."""
    return events.sort_values(key, ascending=False) if key in events else events
