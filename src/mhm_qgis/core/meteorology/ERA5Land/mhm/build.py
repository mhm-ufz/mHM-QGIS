"""Build daily mHM forcing datasets from monthly ERA5-Land files."""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path

from ....grid import (
    ensure_latitude_descending,
    force_target_grid,
    normalize_spatial_axes,
    resample_to_target_grid,
    resample_to_target_points,
    snap_bounds_to_grid,
    subset_dataset,
)
from .aggregation import aggregate_daily, drop_duplicate_times
from .dataset import find_source_variable, open_dataset, standardize_dataset
from .dependencies import import_dependencies
from .logging import log_message
from .metadata import add_time_bounds, finalize_geospatial_metadata
from .specs import VARIABLE_NAME_MAPPINGS
from .types import ForcingSpec

METEO_MAX_BYTES_ENV = "MHM_QGIS_METEO_MAX_BYTES"
METEO_DEFAULT_MAX_BYTES = 2 * 1024 ** 3


def single_pass_byte_limit() -> int:
    """Return the byte budget one multi-variable pass may hold in memory."""
    try:
        limit = int(os.environ.get(METEO_MAX_BYTES_ENV, "") or METEO_DEFAULT_MAX_BYTES)
    except ValueError:
        limit = METEO_DEFAULT_MAX_BYTES
    return limit if limit > 0 else METEO_DEFAULT_MAX_BYTES


def estimated_single_pass_bytes(files, spec_count, target_lat, target_lon) -> int:
    """Estimate the bytes a single multi-variable pass holds at its peak.

    Uses the first file's day count as the per-file estimate. The concatenation
    briefly holds a second copy alongside the per-file slices, hence the factor
    of two.
    """
    files = list(files)
    if not files or target_lat is None or target_lon is None:
        return 0
    np, _pd, xr = import_dependencies()
    cells = int(np.size(target_lat)) * int(np.size(target_lon))
    try:
        with open_dataset(files[0], xr) as ds_raw:
            ds = standardize_dataset(ds_raw)
            name = "time" if "time" in ds.coords else (
                "valid_time" if "valid_time" in ds.coords else None
            )
            if name is None:
                return 0
            values = np.asarray(ds[name].values)
            if np.issubdtype(values.dtype, np.datetime64):
                days = int(np.unique(values.astype("datetime64[D]")).size)
            else:
                days = int(values.size)
    except Exception:
        return 0
    return days * len(files) * int(spec_count) * cells * 8 * 2


def build_daily_dataset(
    files: Iterable[Path],
    spec: ForcingSpec,
    bounds: tuple[float, float, float, float] | None,
    target_lat=None,
    target_lon=None,
    target_sample_lat=None,
    target_sample_lon=None,
    log: Callable[[str], None] | None = None,
):
    """Build one complete daily forcing dataset from monthly ERA5-Land files."""
    return build_daily_datasets(
        files,
        (spec,),
        bounds,
        target_lat=target_lat,
        target_lon=target_lon,
        target_sample_lat=target_sample_lat,
        target_sample_lon=target_sample_lon,
        log=log,
    )[spec.output_variable]


def build_daily_datasets(
    files: Iterable[Path],
    specs: Iterable[ForcingSpec],
    bounds: tuple[float, float, float, float] | None,
    target_lat=None,
    target_lon=None,
    target_sample_lat=None,
    target_sample_lon=None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Return every requested daily forcing dataset in one mapping.

    This holds all of them at once. Prefer :func:`iter_daily_datasets` when the
    caller can write and release each dataset as it arrives.
    """
    return dict(iter_daily_datasets(
        files,
        specs,
        bounds,
        target_lat=target_lat,
        target_lon=target_lon,
        target_sample_lat=target_sample_lat,
        target_sample_lon=target_sample_lon,
        log=log,
    ))


def iter_daily_datasets(
    files: Iterable[Path],
    specs: Iterable[ForcingSpec],
    bounds: tuple[float, float, float, float] | None,
    target_lat=None,
    target_lon=None,
    target_sample_lat=None,
    target_sample_lon=None,
    log: Callable[[str], None] | None = None,
):
    """Yield ``(variable, dataset)`` pairs, releasing each before the next.

    Reading every file once for `tavg`, `tmin`, and `tmax` needs all three
    series in memory at the same time. On a long record that costs several
    times the peak of building them one at a time, so the single pass is only
    taken when the estimated hold fits `MHM_QGIS_METEO_MAX_BYTES`; otherwise each
    variable is built on its own and the files are read again.
    """
    specs = list(specs)
    files = list(files)
    if not specs:
        raise ValueError("At least one forcing spec is required.")

    if len(specs) > 1:
        estimate = estimated_single_pass_bytes(
            files, len(specs), target_lat, target_lon
        )
        limit = single_pass_byte_limit()
        if estimate > limit:
            log_message(
                log,
                f"Building {len(specs)} variables one at a time: a single pass "
                f"would hold about {estimate / 1024 ** 3:.1f} GiB "
                f"(limit {limit / 1024 ** 3:.1f} GiB).",
            )
            for spec in specs:
                yield from iter_daily_datasets(
                    files,
                    (spec,),
                    bounds,
                    target_lat=target_lat,
                    target_lon=target_lon,
                    target_sample_lat=target_sample_lat,
                    target_sample_lon=target_sample_lon,
                    log=log,
                )
            return

    np, pd, xr = import_dependencies()
    explicit_target_lat = None if target_lat is None else np.asarray(target_lat, dtype="float64")
    explicit_target_lon = None if target_lon is None else np.asarray(target_lon, dtype="float64")
    explicit_sample_lat = (
        None if target_sample_lat is None
        else np.asarray(target_sample_lat, dtype="float64")
    )
    explicit_sample_lon = (
        None if target_sample_lon is None
        else np.asarray(target_sample_lon, dtype="float64")
    )
    has_explicit_target = explicit_target_lat is not None and explicit_target_lon is not None
    has_sample_grid = (
        explicit_sample_lat is not None and explicit_sample_lon is not None
    )
    daily_arrays = {spec.output_variable: [] for spec in specs}
    grids = {spec.output_variable: (None, None) for spec in specs}
    snapped_bounds = None

    for path in files:
        with open_dataset(path, xr) as ds_raw:
            base = standardize_dataset(ds_raw)
            base = normalize_spatial_axes(base, np)
            for spec in specs:
                aliases = VARIABLE_NAME_MAPPINGS.get(spec.source_key, [])
                source_variable = find_source_variable(base, aliases)
                if source_variable is None:
                    raise KeyError(
                        f"No {spec.source_key} variable found in {path.name}. "
                        f"Available variables: {list(base.data_vars)}")

                ds = base[[source_variable]]
                if snapped_bounds is None and bounds is not None:
                    snapped_bounds = snap_bounds_to_grid(ds, bounds, np)
                    log_message(
                        log,
                        "ERA5-Land crop bounds snapped to grid centers: "
                        f"west={snapped_bounds[0]:.6f}, east={snapped_bounds[1]:.6f}, "
                        f"south={snapped_bounds[2]:.6f}, north={snapped_bounds[3]:.6f}",
                    )

                if snapped_bounds is not None:
                    ds = subset_dataset(ds, snapped_bounds)

                ds = normalize_spatial_axes(ds, np)
                ds = ensure_latitude_descending(ds)
                daily = aggregate_daily(ds[source_variable], spec, np)
                daily = daily.astype("float64").load()
                daily = normalize_spatial_axes(daily, np)
                daily = ensure_latitude_descending(daily)
                daily = daily.transpose("time", "lat", "lon")

                target_lat, target_lon = grids[spec.output_variable]
                if has_explicit_target and has_sample_grid:
                    daily = resample_to_target_points(
                        daily,
                        explicit_sample_lat,
                        explicit_sample_lon,
                        explicit_target_lat,
                        explicit_target_lon,
                        np,
                    )
                    target_lat = daily["lat"].values
                    target_lon = daily["lon"].values
                elif has_explicit_target:
                    daily = resample_to_target_grid(
                        daily,
                        explicit_target_lat,
                        explicit_target_lon,
                        np,
                    )
                    if explicit_target_lat[0] < explicit_target_lat[-1]:
                        daily = ensure_latitude_descending(daily)
                    target_lat = daily["lat"].values
                    target_lon = daily["lon"].values
                elif target_lat is None or target_lon is None:
                    target_lat = daily["lat"].values
                    target_lon = daily["lon"].values
                else:
                    daily = force_target_grid(daily, target_lat, target_lon, np)
                grids[spec.output_variable] = (target_lat, target_lon)

                daily.name = spec.output_variable
                daily.attrs.clear()
                daily.attrs.update({
                    "units": spec.units,
                    "long_name": spec.long_name,
                })
                daily_arrays[spec.output_variable].append(daily)

    for spec in specs:
        arrays = daily_arrays[spec.output_variable]
        if not arrays:
            raise RuntimeError(
                f"No daily data produced for {spec.output_variable}."
            )
        target_lat, target_lon = grids[spec.output_variable]
        combined = xr.concat(
            arrays,
            dim="time",
            coords="minimal",
            compat="override",
            join="override",
        )
        # Release the per-file slices as soon as they are merged so the peak
        # does not carry every variable's slices plus every concatenation.
        arrays.clear()
        daily_arrays[spec.output_variable] = []
        combined = combined.assign_coords(lat=target_lat, lon=target_lon)
        combined = combined.sortby("time")
        combined = normalize_spatial_axes(combined, np)
        combined = ensure_latitude_descending(combined)
        combined = drop_duplicate_times(combined, np)
        ds_out = combined.to_dataset(name=spec.output_variable)
        ds_out = add_time_bounds(ds_out, np, pd, xr)
        ds_out = finalize_geospatial_metadata(ds_out, spec.output_variable)
        ds_out.attrs.update({
            "Conventions": "CF-1.6",
            "source": "ERA5-Land",
            "history": "Prepared for mHM by mhm_qgis",
        })
        yield spec.output_variable, ds_out
        del combined, ds_out
