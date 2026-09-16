"""Independent forcing variables, streamed at a selected output cadence."""
from __future__ import annotations
from dataclasses import dataclass, replace
from pathlib import Path
import os
import tempfile
from ..ERA5Land.mhm.types import MeteoForcingResult
from ..ERA5Land.mhm.io import write_header, write_netcdf
from ...grid import (
    TargetGrid,
    assert_header_file_matches,
    assert_matches_header,
    axis_resolution,
    axes_match_header,
    crs_equal as _same_crs,
    header_center_arrays,
    mesh_resolution,
    nearest_curvilinear,
    nearest_rectilinear,
    slice_and_pad,
)
from .types import (
    AUTO,
    MeteoFolderSpec,
    SpatialMetadata,
)


from .temporal import (
    VARIABLES,
    convert_units,
    deaccumulate_values,
    interval_bounds,
    normalize_unit,
    output_period,
    time_index,
    validate_spec,
)


@dataclass(frozen=True)
class _SpatialSignature:
    metadata: SpatialMetadata
    x_values: object
    y_values: object


def inspect_meteo_folder(folder, kind=None, source=AUTO, crs_fallback=None, **options):
    """Inspect metadata only; no forcing arrays are read or prepared."""
    import numpy as np
    spec = (folder if isinstance(folder, MeteoFolderSpec) else
            MeteoFolderSpec(kind, folder, source, crs_fallback, **options)).normalized()
    validate_spec(spec)
    signatures, records = [], []
    for path in _files_for_spec(spec):
        signatures.append(_inspect_file(path, spec))
        with _open_dataset(path) as ds:
            variable = _find_variable(ds, spec.kind)
            units = ds[variable].attrs.get("units")
            if units and normalize_unit(units) != normalize_unit(spec.unit):
                raise ValueError(f"{path.name}: metadata unit {units!r} differs from selected {spec.unit!r}.")
            time_name = "time" if "time" in ds[variable].dims else "valid_time"
            bounds = interval_bounds(ds, time_name, spec, variable)
            records.append((path, ds[time_name].values.copy(), bounds))
    _validate_spatial_signatures(signatures)
    records.sort(key=lambda item: item[1][0])
    times = np.concatenate([item[1] for item in records])
    bounds = np.concatenate([item[2] for item in records])
    time_index(times, spec.temporal_resolution)
    if (bounds[:-1, 1] != bounds[1:, 0]).any():
        raise ValueError("Meteorology files contain overlapping or missing intervals.")
    return replace(signatures[0].metadata, files=tuple(item[0] for item in records),
                   start_time=str(times[0]), end_time=str(times[-1]))


def inspect_meteo_inputs(inputs):
    """Inspect one input per output variable."""
    specs = [spec.normalized() for spec in inputs]
    if len({spec.kind for spec in specs}) != len(specs):
        raise ValueError("Configure each meteorology variable only once.")
    return {spec.kind: inspect_meteo_folder(spec) for spec in specs}


def process_meteo_inputs(inputs, output_root, target_grid, *, flat_layout=False,
                         log=None, inspections=None):
    """Stream inputs onto L2, optionally aggregating to a coarser cadence."""
    target_grid.validate()
    specs = [spec.normalized() for spec in inputs]
    if not specs or len({spec.kind for spec in specs}) != len(specs):
        raise ValueError("Configure one input per meteorology variable.")
    inspected = inspections if inspections is not None else inspect_meteo_inputs(specs)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    outputs, headers, files_used = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix=".mhm_qgis-meteo-", dir=root) as temporary:
        stage = Path(temporary)
        for spec in specs:
            validate_spec(spec)
            metadata = inspected[spec.kind]
            _validate_target_coverage(metadata, target_grid)
            output = stage / (f"{spec.kind}.nc" if flat_layout else f"{spec.kind}/{spec.kind}.nc")
            count = 0
            for block in _prepared_blocks(spec, metadata, target_grid, log=log):
                assert_matches_header(block, spec.kind, target_grid.header)
                _write_time_block(block, spec.kind, output, count)
                count += block.sizes["time"]
                block.close()
            if not count:
                raise ValueError(f"{spec.kind}: no time steps to write.")
            outputs[spec.kind] = output
            files_used[spec.kind] = len(metadata.files)
            if not flat_layout:
                header = output.with_name("header.txt")
                write_header(None, spec.kind, header, header=dict(target_grid.header))
                assert_header_file_matches(header, target_grid.header)
                headers[spec.kind] = header
            if log:
                log(f"{spec.kind}: prepared {count} {spec.output_temporal_resolution.lower()} intervals.")
        return _publish_meteo_result(MeteoForcingResult(outputs, headers, files_used),
                                     root, flat_layout=flat_layout)


def _prepared_blocks(spec, metadata, target, *, log=None):
    """Aggregate one interval at a time, then convert and align it."""
    import numpy as np
    import xarray as xr
    previous = None
    pending = None
    last_path = None
    for path in metadata.files:
        last_path = path
        with _open_dataset(path) as ds:
            variable = _find_variable(ds, spec.kind)
            raw = ds[variable]
            time_name = "time" if "time" in raw.dims else "valid_time"
            bounds = interval_bounds(ds, time_name, spec, variable)
            y_dim, x_dim = _spatial_dims(raw)
            for dim in tuple(raw.dims):
                if dim not in {time_name, y_dim, x_dim}:
                    raw = raw.isel({dim: 0}, drop=True)
            raw = raw.rename({time_name: "time"}) if time_name != "time" else raw
            raw = raw.transpose("time", y_dim, x_dim)
            cells = max(raw.sizes[y_dim] * raw.sizes[x_dim],
                        int(target.header["nrows"]) * int(target.header["ncols"]))
            block_size = min(32, max(1, 64 * 1024 * 1024 // (8 * cells)))
            for start in range(0, raw.sizes["time"], block_size):
                chunk = raw.isel(time=slice(start, start + block_size)).load()
                chunk_bounds = bounds[start:start + block_size]
                values, previous = deaccumulate_values(
                    chunk.values, chunk_bounds, spec, previous)
                if spec.output_temporal_resolution == spec.temporal_resolution:
                    converted = convert_units(values, chunk_bounds, spec, variable)
                    yield _prepared_dataset(
                        chunk.copy(data=converted), chunk_bounds, ds, spec,
                        target, np, xr)
                    continue
                for number, plane in enumerate(values):
                    expected = output_period(
                        chunk_bounds[number], spec.output_temporal_resolution)
                    if pending is not None and pending["expected"] != expected:
                        yield _finish_interval(pending, ds, spec, target, np, xr, log)
                        pending = None
                    if pending is None:
                        pending = {
                            "expected": expected,
                            "start": chunk_bounds[number, 0],
                            "end": chunk_bounds[number, 1],
                            "value": np.where(np.isfinite(plane), plane, 0.0),
                            "count": np.isfinite(plane).astype("int32"),
                            "template": chunk.isel(time=slice(number, number + 1)),
                            "source": variable,
                            "input_seconds": float(
                                (chunk_bounds[number, 1] - chunk_bounds[number, 0])
                                / np.timedelta64(1, "s")),
                        }
                    else:
                        _accumulate(pending, plane, spec.aggregation, np)
                        pending["end"] = chunk_bounds[number, 1]
    if pending is not None and last_path is not None:
        with _open_dataset(last_path) as ds:
            yield _finish_interval(pending, ds, spec, target, np, xr, log)


def _finish_interval(pending, ds, spec, target, np, xr, log):
    actual = np.asarray([[pending["start"], pending["end"]]], dtype="datetime64[ns]")
    if tuple(actual[0]) != pending["expected"] and log:
        log(
            f"WARNING: {spec.kind}: partial {spec.output_temporal_resolution.lower()} "
            f"interval {actual[0, 0]} to {actual[0, 1]}; aggregating available values."
        )
    values = _aggregate_result(pending, spec.aggregation, np)[None, ...]
    values = convert_units(
        values, actual, spec, pending["source"],
        input_seconds=pending["input_seconds"])
    template = pending["template"].assign_coords(time=[actual[0, 0]]).copy(data=values)
    return _prepared_dataset(template, actual, ds, spec, target, np, xr)


def _accumulate(pending, plane, method, np):
    valid = np.isfinite(plane)
    if method in {"Average", "Sum"}:
        pending["value"] += np.where(valid, plane, 0.0)
    elif method == "Minimum":
        pending["value"] = np.where(
            pending["count"] == 0, plane, np.fmin(pending["value"], plane))
    else:
        pending["value"] = np.where(
            pending["count"] == 0, plane, np.fmax(pending["value"], plane))
    pending["count"] += valid


def _aggregate_result(pending, method, np):
    if method == "Average":
        return np.divide(
            pending["value"], pending["count"],
            out=np.full_like(pending["value"], np.nan),
            where=pending["count"] > 0)
    return np.where(pending["count"] > 0, pending["value"], np.nan)


def _prepared_dataset(chunk, bounds, ds, spec, target, np, xr):
    chunk = _resample_ready(chunk, ds, spec.crs, target, np, xr).load()
    chunk.name = spec.kind
    chunk.attrs = {
        "units": VARIABLES[spec.kind][1],
        "long_name": spec.kind,
        "input_cadence": spec.temporal_resolution,
        "output_cadence": spec.output_temporal_resolution,
    }
    output = chunk.to_dataset()
    output["time_bnds"] = (("time", "bnds"), bounds)
    output["time"].attrs = {
        "standard_name": "time", "axis": "T", "bounds": "time_bnds"}
    output["lat"].attrs = {"standard_name": "latitude", "units": "degrees_north"}
    output["lon"].attrs = {"standard_name": "longitude", "units": "degrees_east"}
    output.attrs = {"Conventions": "CF-1.8", "history": "Prepared by mhm_qgis"}
    return _attach_target_coordinates(output, spec.kind, target)


def _write_time_block(ds, variable, path, offset):
    """Create once, then append data and exact CF time coordinates."""
    if not offset:
        write_netcdf(ds, variable, path, unlimited_dims=("time",))
        return
    import numpy as np
    import pandas as pd
    from netCDF4 import Dataset, date2num
    with Dataset(path, "a") as handle:
        stop = offset + ds.sizes["time"]
        handle[variable][offset:stop] = np.ma.masked_invalid(ds[variable].values)
        for name in ("time", "time_bnds"):
            target = handle[name]
            values = ds[name].values
            dates = pd.DatetimeIndex(values.reshape(-1)).to_pydatetime()
            target[offset:stop] = np.asarray(date2num(dates, getattr(target, "units", handle["time"].units), getattr(target, "calendar", handle["time"].calendar))).reshape(values.shape)


def _publish_meteo_result(
        staged: MeteoForcingResult,
        output_root: Path,
        *,
        flat_layout: bool = False) -> MeteoForcingResult:
    """Publish a complete staged result and roll back replacement failures."""
    first_output = next(iter(staged.outputs.values()))
    staging_root = first_output.parent if flat_layout else first_output.parents[1]
    backup_root = staging_root / "backups"
    managed_variables = tuple(staged.outputs)
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []

    try:
        for variable in managed_variables:
            finals = (
                (output_root / f"{variable}.nc",)
                if flat_layout else
                (
                    output_root / variable / f"{variable}.nc",
                    output_root / variable / "header.txt",
                )
            )
            for final in finals:
                if not final.exists():
                    continue
                backup = backup_root / final.relative_to(output_root)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(final, backup)
                backups.append((backup, final))

        final_outputs = {}
        final_headers = {}
        for variable, staged_output in staged.outputs.items():
            if flat_layout:
                final_output = output_root / f"{variable}.nc"
                final_header = None
            else:
                final_dir = output_root / variable
                final_dir.mkdir(parents=True, exist_ok=True)
                final_output = final_dir / f"{variable}.nc"
                final_header = final_dir / "header.txt"
            os.replace(staged_output, final_output)
            published.append(final_output)
            if final_header is not None:
                os.replace(staged.headers[variable], final_header)
                published.append(final_header)
                final_headers[variable] = final_header
            final_outputs[variable] = final_output

        return MeteoForcingResult(
            final_outputs,
            final_headers,
            dict(staged.files_used),
        )
    except Exception:
        for path in published:
            if path.exists():
                path.unlink()
        for backup, final in reversed(backups):
            if backup.exists():
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, final)
        raise


def _files_for_spec(spec: MeteoFolderSpec) -> list[Path]:
    folder = Path(spec.folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Meteorology folder does not exist: {folder}")
    pattern = spec.pattern.strip()
    if not pattern or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise ValueError("Enter a relative NetCDF glob within the selected folder.")
    files = sorted(path for path in folder.glob(pattern)
                   if path.is_file() and path.suffix.lower() == ".nc")
    if not files:
        raise ValueError(f"{spec.kind}: no NetCDF files match {pattern!r} in {folder}.")
    return files


def _open_dataset(path: Path):
    import xarray as xr

    last_error = None
    for engine in ("netcdf4", "h5netcdf", "scipy", None):
        try:
            kwargs = {} if engine is None else {"engine": engine}
            return xr.open_dataset(
                path, decode_times=True, mask_and_scale=True, **kwargs)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Could not open NetCDF file {path}: {last_error}")


def _inspect_file(
        path: Path,
        spec: MeteoFolderSpec) -> _SpatialSignature:
    import numpy as np

    with _open_dataset(path) as ds:
        variable = _find_variable(ds, spec.kind)
        da = ds[variable]
        y_dim, x_dim = _spatial_dims(da)
        x_name, x_values = _axis_values(ds, x_dim, "x")
        y_name, y_values = _axis_values(ds, y_dim, "y")

        if x_values is None or y_values is None:
            lon_name, lon = _geographic_coordinate(ds, "lon", (y_dim, x_dim))
            lat_name, lat = _geographic_coordinate(ds, "lat", (y_dim, x_dim))
            if lon is None or lat is None:
                raise ValueError(
                    f"{path.name}: could not identify spatial coordinates.")
            x_name, y_name = lon_name, lat_name
            x_values, y_values = lon, lat

        x_values = np.asarray(x_values, dtype="float64")
        y_values = np.asarray(y_values, dtype="float64")
        if x_values.ndim == 1 and y_values.ndim == 1:
            dx = axis_resolution(x_values, f"{path.name}: coordinate {x_name}")
            dy = axis_resolution(y_values, f"{path.name}: coordinate {y_name}")
            bounds = (
                float(np.nanmin(x_values)), float(np.nanmax(x_values)),
                float(np.nanmin(y_values)), float(np.nanmax(y_values)))
            shape = (len(y_values), len(x_values))
        elif x_values.shape == y_values.shape == (
                da.sizes[y_dim], da.sizes[x_dim]):
            dx = mesh_resolution(
                x_values, axis=1, label=f"{path.name}: coordinate {x_name}"
            )
            dy = mesh_resolution(
                y_values, axis=0, label=f"{path.name}: coordinate {y_name}"
            )
            bounds = (
                float(np.nanmin(x_values)), float(np.nanmax(x_values)),
                float(np.nanmin(y_values)), float(np.nanmax(y_values)))
            shape = x_values.shape
        else:
            raise ValueError(f"{path.name}: spatial coordinates are not a 2-D grid.")

        crs = _dataset_crs(ds, da, x_name, y_name) or spec.crs
        unit = _coordinate_unit(ds, x_name, crs)
        metadata = SpatialMetadata(
            kind=spec.kind,
            source=spec.source,
            files=(path,),
            shape=shape,
            x_coordinate=x_name,
            y_coordinate=y_name,
            x_resolution=dx,
            y_resolution=dy,
            resolution=(dx + dy) / 2.0,
            bounds=bounds,
            crs=crs,
            unit=unit,
        )
        return _SpatialSignature(metadata, x_values, y_values)


def _find_variable(ds, kind):
    """Resolve an exact output name or one unambiguous known source alias."""
    candidates = [name for name in VARIABLES[kind][0] if name in ds.data_vars
                  and ("time" in ds[name].dims or "valid_time" in ds[name].dims)]
    if kind in candidates:
        return kind
    if len(candidates) != 1:
        raise ValueError(f"{kind}: expected one source variable; found {candidates or list(ds.data_vars)}.")
    return candidates[0]


def _spatial_dims(da) -> tuple[str, str]:
    dims = [dim for dim in da.dims if dim not in {"time", "valid_time", "bnds"}]
    extra = [dim for dim in dims if da.sizes[dim] > 1]
    if len(extra) != 2:
        raise ValueError(
            f"Variable {da.name!r} must have exactly two spatial dimensions.")
    return extra[-2], extra[-1]


def _axis_values(ds, dim: str, axis: str):
    import numpy as np

    candidates = (
        ("lon", "longitude", "xc", "x", "easting")
        if axis == "x" else
        ("lat", "latitude", "yc", "y", "northing")
    )
    for name in (dim,) + candidates:
        if name not in ds.variables:
            continue
        values = np.asarray(ds[name].values)
        if values.ndim == 1 and ds[name].dims == (dim,):
            return name, values
    return dim, None


def _geographic_coordinate(ds, axis: str, dims: tuple[str, str]):
    aliases = ("lon", "longitude") if axis == "lon" else ("lat", "latitude")
    for name in aliases:
        if name in ds.variables and ds[name].dims == dims:
            return name, ds[name].values
    return axis, None


def _dataset_crs(ds, da, x_name: str, y_name: str) -> str | None:
    if x_name.lower() in {"lon", "longitude"} and y_name.lower() in {
            "lat", "latitude"}:
        return "EPSG:4326"

    mapping_name = da.attrs.get("grid_mapping")
    candidates = [ds.attrs]
    if mapping_name and mapping_name in ds.variables:
        candidates.append(ds[mapping_name].attrs)
    for name in ("spatial_ref", "crs"):
        if name in ds.variables:
            candidates.append(ds[name].attrs)
    for attrs in candidates:
        for key in ("spatial_ref", "crs_wkt", "crs"):
            value = attrs.get(key)
            if value:
                return str(value)
    return None


def _coordinate_unit(ds, x_name: str, crs: str | None) -> str:
    units = str(ds[x_name].attrs.get("units", "")) if x_name in ds else ""
    if "degree" in units.lower() or x_name.lower() in {"lon", "longitude"}:
        return "deg"
    if crs:
        try:
            from pyproj import CRS

            return _crs_unit(CRS.from_user_input(crs))
        except Exception:
            pass
    return units or ""


def _crs_unit(crs) -> str:
    if getattr(crs, "is_geographic", False):
        return "deg"
    name = crs.axis_info[0].unit_name.lower() if crs.axis_info else ""
    if name in {"metre", "meter"}:
        return "m"
    if "foot" in name or "feet" in name:
        return "ft"
    return name


def _validate_spatial_signatures(signatures: list[_SpatialSignature]) -> None:
    import numpy as np

    first = signatures[0]
    for current in signatures[1:]:
        if current.metadata.shape != first.metadata.shape:
            raise ValueError(
                "NetCDF files do not share the same spatial dimensions: "
                f"{first.metadata.files[0].name}={first.metadata.shape}, "
                f"{current.metadata.files[0].name}={current.metadata.shape}.")
        if (
                np.shape(current.x_values) != np.shape(first.x_values)
                or np.shape(current.y_values) != np.shape(first.y_values)
                or not np.allclose(current.x_values, first.x_values,
                                   rtol=1e-9, atol=1e-9, equal_nan=True)
                or not np.allclose(current.y_values, first.y_values,
                                   rtol=1e-9, atol=1e-9, equal_nan=True)):
            raise ValueError(
                "NetCDF files do not share identical spatial coordinates: "
                f"{first.metadata.files[0].name} and "
                f"{current.metadata.files[0].name}.")
        left, right = first.metadata.crs, current.metadata.crs
        if (left is None) != (right is None) or (
                left and right and not _same_crs(left, right)):
            raise ValueError(
                "NetCDF files do not share the same CRS: "
                f"{first.metadata.files[0].name} and "
                f"{current.metadata.files[0].name}.")


def _validate_target_coverage(
        metadata: SpatialMetadata,
        target: TargetGrid) -> None:
    """Reject disjoint grids; allow padding around overlapping input."""
    import numpy as np

    target_x = np.asarray(
        target.sample_lon
        if target.sample_lon is not None else target.lon,
        dtype="float64",
    )
    target_y = np.asarray(
        target.sample_lat
        if target.sample_lat is not None else target.lat,
        dtype="float64",
    )
    if (
            target.crs
            and metadata.crs
            and _same_crs(metadata.crs, target.crs)):
        target_x, target_y = header_center_arrays(target.header)
    elif metadata.crs and not _same_crs(metadata.crs, "EPSG:4326"):
        try:
            from pyproj import Transformer
        except Exception as exc:
            raise RuntimeError(
                "pyproj is required to validate projected meteorology data.") from exc
        transform = Transformer.from_crs(
            "EPSG:4326", metadata.crs, always_xy=True)
        target_x, target_y = transform.transform(target_x, target_y)
    elif metadata.crs is None and metadata.unit not in {"deg", "degree"}:
        return

    west, east, south, north = metadata.bounds
    x_tolerance = metadata.x_resolution / 2.0 + 1e-9
    y_tolerance = metadata.y_resolution / 2.0 + 1e-9
    if (
            float(target_x.max()) < west - x_tolerance
            or float(target_x.min()) > east + x_tolerance
            or float(target_y.max()) < south - y_tolerance
            or float(target_y.min()) > north + y_tolerance):
        raise ValueError(
            f"The target grid is outside the {metadata.kind} source extent.")


def _resample_ready(da, ds, source_crs, target, np, xr):
    y_dim, x_dim = _spatial_dims(da)
    x_name, x_values = _axis_values(ds, x_dim, "x")
    y_name, y_values = _axis_values(ds, y_dim, "y")
    target_lon = np.asarray(target.lon, dtype="float64")
    target_lat = np.asarray(target.lat, dtype="float64")
    sample_lon = (
        np.asarray(target.sample_lon, dtype="float64")
        if target.sample_lon is not None else None
    )
    sample_lat = (
        np.asarray(target.sample_lat, dtype="float64")
        if target.sample_lat is not None else None
    )

    if x_values is not None and y_values is not None:
        source_crs = _dataset_crs(ds, da, x_name, y_name) or source_crs
        lon_name, lon = _geographic_coordinate(ds, "lon", (y_dim, x_dim))
        lat_name, lat = _geographic_coordinate(ds, "lat", (y_dim, x_dim))
        if source_crs is None and lon is not None and lat is not None:
            return nearest_curvilinear(
                da, np.asarray(lon), np.asarray(lat),
                target_lon, target_lat, sample_lon, sample_lat)
        x_target, y_target = target_lon, target_lat
        target_crs_matches = bool(
            source_crs and target.crs and _same_crs(source_crs, target.crs)
        )
        if target_crs_matches:
            x_target, y_target = header_center_arrays(target.header)
        elif sample_lon is not None:
            x_target, y_target = sample_lon, sample_lat
            if source_crs and not _same_crs(source_crs, "EPSG:4326"):
                try:
                    from pyproj import Transformer
                except Exception as exc:
                    raise RuntimeError(
                        "pyproj is required to resample projected "
                        "mHM-ready data."
                    ) from exc
                transform = Transformer.from_crs(
                    "EPSG:4326",
                    source_crs,
                    always_xy=True,
                )
                x_target, y_target = transform.transform(
                    x_target,
                    y_target,
                )
            return nearest_rectilinear(
                da,
                np.asarray(x_values),
                np.asarray(y_values),
                x_target,
                y_target,
                target_lon,
                target_lat,
            )
        elif source_crs and not _same_crs(source_crs, "EPSG:4326"):
            try:
                from pyproj import Transformer
            except Exception as exc:
                raise RuntimeError(
                    "pyproj is required to resample projected mHM-ready data.") from exc
            transform = Transformer.from_crs(
                "EPSG:4326", source_crs, always_xy=True)
            center_lat = float((target_lat.min() + target_lat.max()) / 2.0)
            center_lon = float((target_lon.min() + target_lon.max()) / 2.0)
            x_target = np.asarray([
                transform.transform(lon, center_lat)[0]
                for lon in target_lon])
            y_target = np.asarray([
                transform.transform(center_lon, lat)[1]
                for lat in target_lat])

        source = da.assign_coords(
            {x_dim: np.asarray(x_values), y_dim: np.asarray(y_values)})
        source = source.sortby(x_dim).sortby(y_dim)
        if target_crs_matches and axes_match_header(
                x_values, y_values, target.header):
            result = slice_and_pad(source, x_dim, y_dim, target.header)
        else:
            result = source.interp(
                {x_dim: x_target, y_dim: y_target}, method="nearest")
        result = result.rename({x_dim: "lon", y_dim: "lat"})
        return result.assign_coords(lon=target_lon, lat=target_lat).transpose(
            "time", "lat", "lon")

    lon_name, lon = _geographic_coordinate(ds, "lon", (y_dim, x_dim))
    lat_name, lat = _geographic_coordinate(ds, "lat", (y_dim, x_dim))
    if lon is None or lat is None:
        raise ValueError("mHM-ready data has no usable spatial coordinates.")
    return nearest_curvilinear(
        da,
        np.asarray(lon),
        np.asarray(lat),
        target_lon,
        target_lat,
        sample_lon,
        sample_lat,
    )


def _attach_target_coordinates(ds, variable: str, target: TargetGrid):
    """Attach exact 2-D cell-centre coordinates for projected target grids."""
    if target.sample_lon is None or target.sample_lat is None:
        return ds

    ds = ds.assign_coords({
        "lon2d": (
            ("lat", "lon"),
            target.sample_lon,
            {
                "standard_name": "longitude",
                "units": "degrees_east",
            },
        ),
        "lat2d": (
            ("lat", "lon"),
            target.sample_lat,
            {
                "standard_name": "latitude",
                "units": "degrees_north",
            },
        ),
    })
    ds["lon"].attrs = {"standard_name": "projection_x_coordinate", "axis": "X"}
    ds["lat"].attrs = {"standard_name": "projection_y_coordinate", "axis": "Y"}
    ds[variable].attrs["coordinates"] = "lat2d lon2d"
    if target.crs:
        try:
            from pyproj import CRS

            crs = CRS.from_user_input(target.crs)
            unit = crs.axis_info[0].unit_name if crs.axis_info else "metre"
            ds["lon"].attrs["units"] = ds["lat"].attrs["units"] = unit
            crs_attrs = crs.to_cf()
            crs_attrs["spatial_ref"] = crs.to_wkt()
            ds["crs"] = 0
            ds["crs"].attrs.update(crs_attrs)
            ds[variable].attrs["grid_mapping"] = "crs"
        except Exception:
            pass
    return ds
