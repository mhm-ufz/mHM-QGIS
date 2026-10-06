"""Lazy NumPy/xarray operations for matching data to an exact grid."""
from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .model import (
    Extent,
    TargetGrid,
    header_bounds,
    header_center_coordinates,
    read_header_file,
)


def header_center_arrays(header: Mapping):
    """Return exact descending-y cell-centre axes as float64 arrays."""
    import numpy as np

    x_values, y_values = header_center_coordinates(header)
    return (
        np.asarray(x_values, dtype="float64"),
        np.asarray(y_values, dtype="float64"),
    )


def axis_resolution(values, label: str = "coordinate") -> float:
    """Return the spacing of a regular one-dimensional coordinate axis."""
    import numpy as np

    values = np.asarray(values)
    unique = np.unique(values[np.isfinite(values)])
    if len(unique) < 2:
        raise ValueError(f"{label} needs at least two values.")
    if len(unique) != len(values):
        raise ValueError(f"{label} contains duplicates.")
    diffs = np.abs(np.diff(np.sort(unique)))
    resolution = float(np.nanmedian(diffs))
    if not np.allclose(diffs, resolution, rtol=1e-6, atol=1e-9):
        raise ValueError(f"{label} is not regularly spaced.")
    return resolution


def mesh_resolution(values, axis: int, label: str = "coordinate") -> float:
    """Return the representative spacing along one axis of a 2-D mesh."""
    import numpy as np

    diffs = np.abs(np.diff(values, axis=axis))
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if not diffs.size:
        raise ValueError(f"{label} needs at least two values.")
    return float(np.nanmedian(diffs))


def header_from_coordinate_axes(x_values, y_values, *, unit="", nodata_value=-9999.0):
    """Build a square-cell header from one-dimensional centre coordinates."""
    import numpy as np

    x_values = np.asarray(x_values, dtype="float64")
    y_values = np.asarray(y_values, dtype="float64")
    x_resolution = axis_resolution(x_values, "x coordinate")
    y_resolution = axis_resolution(y_values, "y coordinate")
    cellsize = (x_resolution + y_resolution) / 2
    if not np.isclose(x_resolution, y_resolution, rtol=1e-6, atol=1e-12):
        raise ValueError("A grid header requires equal x and y resolution.")
    return {
        "ncols": int(x_values.size),
        "nrows": int(y_values.size),
        "xllcorner": float(np.min(x_values)) - cellsize / 2,
        "yllcorner": float(np.min(y_values)) - cellsize / 2,
        "cellsize": cellsize,
        "nodata_value": float(nodata_value),
        "unit": str(unit or ""),
    }


def axes_match_header(x_values, y_values, header: Mapping) -> bool:
    import numpy as np

    cellsize = float(header["cellsize"])
    tolerance = abs(cellsize) * 1e-6
    target_x, target_y = header_center_coordinates(header)
    for axis_values, target in ((x_values, target_x), (y_values, sorted(target_y))):
        values = np.sort(np.asarray(axis_values, dtype="float64").ravel())
        if values.size < 1:
            return False
        if values.size > 1 and abs(float(np.abs(np.diff(values)).max()) - cellsize) > tolerance:
            return False
        offset = (float(values[0]) - float(target[0])) / cellsize
        if abs(offset - round(offset)) > 1e-6:
            return False
    return True


def reindex_to_header(da, x_dim: str, y_dim: str, header: Mapping, fill_value=None):
    x_values, y_values = header_center_coordinates(header)
    return da.reindex(
        {x_dim: x_values, y_dim: y_values},
        method="nearest",
        tolerance=abs(float(header["cellsize"])) * 1e-6,
        fill_value=float("nan") if fill_value is None else fill_value,
    )


def grid_matches_header(dataset, x_dim: str, y_dim: str, header: Mapping) -> bool:
    import numpy as np

    target_x, target_y = header_center_coordinates(header)
    if dataset.sizes.get(x_dim) != len(target_x) or dataset.sizes.get(y_dim) != len(target_y):
        return False
    tolerance = float(header["cellsize"]) * 1e-6
    return bool(
        np.allclose(np.sort(dataset[x_dim].values), target_x, rtol=0, atol=tolerance)
        and np.allclose(np.sort(dataset[y_dim].values), np.sort(target_y), rtol=0, atol=tolerance)
    )


def assert_source_covers_header(dataset, x_dim: str, y_dim: str, header: Mapping) -> None:
    import numpy as np

    x_values = np.asarray(dataset[x_dim].values, dtype="float64")
    y_values = np.asarray(dataset[y_dim].values, dtype="float64")

    def resolution(values):
        unique = np.unique(values[np.isfinite(values)])
        return (
            float(np.nanmedian(np.abs(np.diff(np.sort(unique)))))
            if unique.size > 1
            else float(header["cellsize"])
        )

    x_resolution, y_resolution = resolution(x_values), resolution(y_values)
    source = Extent.from_centres(
        (
            float(np.nanmin(x_values)),
            float(np.nanmax(x_values)),
            float(np.nanmin(y_values)),
            float(np.nanmax(y_values)),
        ),
        x_resolution,
        y_resolution,
    )
    target = Extent.from_bounds(header_bounds(header))
    tolerance = max(float(header["cellsize"]), x_resolution, y_resolution) * 1e-4
    if (
        target.xmin < source.xmin - tolerance
        or target.xmax > source.xmax + tolerance
        or target.ymin < source.ymin - tolerance
        or target.ymax > source.ymax + tolerance
    ):
        raise ValueError(
            "Input raster extent does not cover the target L0 extent. "
            f"Source={source.bounds}, target={target.bounds}."
        )


def slice_and_pad(da, x_dim: str, y_dim: str, header: Mapping):
    return reindex_to_header(da, x_dim, y_dim, header)


def assert_matches_header(dataset, variable: str, header: Mapping[str, Any]) -> None:
    rows, cols = int(header["nrows"]), int(header["ncols"])
    sizes = dataset[variable].sizes
    if sizes.get("lat") != rows or sizes.get("lon") != cols:
        raise ValueError(
            f"{variable}: prepared L2 grid is {sizes.get('lat')}x{sizes.get('lon')}, "
            f"expected {rows}x{cols}."
        )


def assert_header_file_matches(path: Path | str, header: Mapping[str, Any]) -> None:
    written = read_header_file(path)
    if written is None:
        raise ValueError(f"Could not read the written L2 header: {path}")
    if int(written["ncols"]) != int(header["ncols"]) or int(written["nrows"]) != int(header["nrows"]):
        raise ValueError(f"Written L2 header has the wrong dimensions: {path}")
    tolerance = max(abs(float(header["cellsize"])), 1.0) * 1e-9
    for key in ("xllcorner", "yllcorner", "cellsize"):
        if not math.isclose(float(written[key]), float(header[key]), rel_tol=0, abs_tol=tolerance):
            raise ValueError(f"Written L2 header {key} does not match: {path}")


def assert_netcdf_matches_header(path, variable, header) -> None:
    import xarray as xr

    with xr.open_dataset(path) as dataset:
        if variable not in dataset:
            raise ValueError(f"{path} has no {variable} variable.")
        assert_matches_header(dataset, variable, header)


def target_grid_from_header(header: Mapping, crs: str | None = None) -> TargetGrid:
    """Build WGS84 sampling coordinates without importing QGIS."""
    import numpy as np

    x_values, y_values = header_center_coordinates(header)
    x_mesh, y_mesh = np.meshgrid(
        np.asarray(x_values, dtype="float64"),
        np.asarray(y_values, dtype="float64"),
    )
    lon_mesh, lat_mesh = x_mesh, y_mesh
    if crs:
        from pyproj import CRS, Transformer

        source = CRS.from_user_input(crs)
        if not source.is_geographic:
            lon_mesh, lat_mesh = Transformer.from_crs(
                source, "EPSG:4326", always_xy=True
            ).transform(x_mesh, y_mesh)
    middle_row, middle_column = lon_mesh.shape[0] // 2, lon_mesh.shape[1] // 2
    return TargetGrid(
        lon=lon_mesh[middle_row, :].tolist(),
        lat=lat_mesh[:, middle_column].tolist(),
        header=dict(header),
        crs=crs,
        sample_lon=lon_mesh,
        sample_lat=lat_mesh,
    )


def nearest_rectilinear(
    da,
    source_x,
    source_y,
    target_x,
    target_y,
    output_x,
    output_y,
):
    """Sample a rectilinear source at exact two-dimensional target points."""
    import numpy as np

    source_x_mesh, source_y_mesh = np.meshgrid(source_x, source_y)
    return nearest_curvilinear(
        da,
        source_x_mesh,
        source_y_mesh,
        output_x,
        output_y,
        target_x,
        target_y,
    )


def nearest_curvilinear(
    da,
    source_x,
    source_y,
    output_x,
    output_y,
    sample_x=None,
    sample_y=None,
):
    """Nearest-neighbour sample a curvilinear source onto an output grid."""
    import numpy as np
    import xarray as xr

    try:
        from scipy.spatial import cKDTree
    except Exception as exc:
        raise RuntimeError(
            "scipy is required to resample curvilinear data."
        ) from exc

    spatial_dims = [
        dim for dim in da.dims
        if dim not in {"time", "valid_time", "bnds"} and da.sizes[dim] > 1
    ]
    if len(spatial_dims) != 2:
        raise ValueError(f"Variable {da.name!r} must have exactly two spatial dimensions.")
    y_dim, x_dim = spatial_dims
    source_points = np.column_stack(
        [np.asarray(source_x).reshape(-1), np.asarray(source_y).reshape(-1)]
    )
    if sample_x is None or sample_y is None:
        x_mesh, y_mesh = np.meshgrid(output_x, output_y)
    else:
        x_mesh = np.asarray(sample_x, dtype="float64")
        y_mesh = np.asarray(sample_y, dtype="float64")
    target_points = np.column_stack([x_mesh.reshape(-1), y_mesh.reshape(-1)])
    finite = np.isfinite(source_points).all(axis=1)
    if not finite.any():
        raise ValueError("Source grid has no finite coordinates.")
    _, indices = cKDTree(source_points[finite]).query(target_points)
    indices = np.flatnonzero(finite)[indices]
    values = np.asarray(da.transpose("time", y_dim, x_dim).values)
    sampled = values.reshape(values.shape[0], -1)[:, indices]
    sampled = sampled.reshape(values.shape[0], len(output_y), len(output_x)).astype("float64")
    dx = mesh_resolution(source_x, axis=1) / 2
    dy = mesh_resolution(source_y, axis=0) / 2
    inside = ((x_mesh >= np.nanmin(source_x) - dx) & (x_mesh <= np.nanmax(source_x) + dx)
              & (y_mesh >= np.nanmin(source_y) - dy) & (y_mesh <= np.nanmax(source_y) + dy))
    sampled[:, ~inside] = np.nan
    return xr.DataArray(
        sampled,
        dims=("time", "lat", "lon"),
        coords={"time": da["time"], "lat": output_y, "lon": output_x},
    )


def snap_bounds_to_grid(ds, bounds, np) -> tuple[float, float, float, float]:
    west, east, south, north = bounds
    lon_values = np.sort(np.asarray(ds["lon"].values, dtype=float))
    lat_values = np.sort(np.asarray(ds["lat"].values, dtype=float))
    return (
        _coord_floor(lon_values, min(west, east)),
        _coord_ceil(lon_values, max(west, east)),
        _coord_floor(lat_values, min(south, north)),
        _coord_ceil(lat_values, max(south, north)),
    )


def subset_dataset(ds, bounds):
    west, east, south, north = bounds
    lon_ascending = float(ds["lon"].values[0]) <= float(ds["lon"].values[-1])
    lat_ascending = float(ds["lat"].values[0]) <= float(ds["lat"].values[-1])
    subset = ds.sel(
        lon=slice(west, east) if lon_ascending else slice(east, west),
        lat=slice(south, north) if lat_ascending else slice(north, south),
    )
    if subset.sizes.get("lat", 0) == 0 or subset.sizes.get("lon", 0) == 0:
        raise ValueError(
            "ERA5-Land crop produced an empty grid. "
            f"Bounds were west={west}, east={east}, south={south}, north={north}."
        )
    return subset


def ensure_latitude_descending(ds):
    if ds.sizes.get("lat", 0) > 1 and float(ds["lat"][0]) < float(ds["lat"][-1]):
        return ds.sortby("lat", ascending=False)
    return ds


def normalize_spatial_axes(obj, np, decimals: int = 6):
    for axis in ("lat", "lon"):
        if axis not in obj.coords:
            continue
        values = np.asarray(obj[axis].values, dtype="float64")
        if values.ndim != 1:
            raise ValueError(f"Coordinate {axis} must be one-dimensional.")
        rounded = np.round(values, decimals)
        obj = obj.assign_coords({axis: rounded})
        _, first_indices = np.unique(rounded, return_index=True)
        if len(first_indices) != len(rounded):
            obj = obj.isel({axis: np.sort(first_indices)})
    return obj


def force_target_grid(da, target_lat, target_lon, np):
    lat_values = np.asarray(da["lat"].values, dtype="float64")
    lon_values = np.asarray(da["lon"].values, dtype="float64")
    if np.array_equal(lat_values, target_lat) and np.array_equal(lon_values, target_lon):
        return da
    return da.reindex(
        lat=target_lat,
        lon=target_lon,
        method="nearest",
        tolerance=max(_axis_tolerance(target_lat, np), _axis_tolerance(target_lon, np)),
    )


def resample_to_target_grid(da, target_lat, target_lon, np):
    if target_lat is None or target_lon is None:
        return da
    source = da.sortby("lat").sortby("lon")
    return source.reindex(
        lat=np.asarray(target_lat, dtype="float64"),
        lon=np.asarray(target_lon, dtype="float64"),
        method="nearest",
    ).transpose("time", "lat", "lon")


def resample_to_target_points(da, sample_lat, sample_lon, target_lat, target_lon, np):
    import xarray as xr
    from scipy.spatial import cKDTree

    source = da.sortby("lat").sortby("lon").transpose("time", "lat", "lon")
    source_lon, source_lat = np.meshgrid(source["lon"].values, source["lat"].values)
    source_points = np.column_stack([source_lon.reshape(-1), source_lat.reshape(-1)])
    target_points = np.column_stack([
        np.asarray(sample_lon, dtype="float64").reshape(-1),
        np.asarray(sample_lat, dtype="float64").reshape(-1),
    ])
    _, indices = cKDTree(source_points).query(target_points)
    values = np.asarray(source.values)
    sampled = values.reshape(values.shape[0], -1)[:, indices]
    sampled = sampled.reshape(values.shape[0], len(target_lat), len(target_lon))
    return xr.DataArray(
        sampled,
        dims=("time", "lat", "lon"),
        coords={"time": source["time"], "lat": target_lat, "lon": target_lon},
        name=da.name,
        attrs=dict(da.attrs),
    )


def _coord_floor(values, target: float) -> float:
    candidates = values[values <= target]
    return float(candidates[-1] if len(candidates) else values[0])


def _coord_ceil(values, target: float) -> float:
    candidates = values[values >= target]
    return float(candidates[0] if len(candidates) else values[-1])


def _axis_tolerance(values, np) -> float:
    values = np.asarray(values, dtype="float64")
    if len(values) < 2:
        return 1e-8
    diffs = np.diff(np.sort(values))
    diffs = diffs[diffs > 0]
    return float(np.min(diffs) / 4.0) if len(diffs) else 1e-8
