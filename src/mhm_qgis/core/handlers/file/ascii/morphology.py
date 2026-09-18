# -*- coding: utf-8 -*-
"""mHM morphology ASCII preparation through mhm_tools file handlers."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....grid import (
    assert_source_covers_header,
    axes_match_header,
    header_center_coordinates,
    grid_matches_header,
    headers_match,
    reindex_to_header,
    standardize_header,
    validate_grid_headers as validate_mhm_grid_headers,
)


Header = dict[str, Any]
LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class MorphologyAsciiLayer:
    """One raster-to-ASCII export request."""

    input_path: Path | str
    output_path: Path | str
    name: str = ""
    nodata_value: float | int = -9999
    integer: bool = False
    data_var: str | None = None


@dataclass(frozen=True)
class MorphologyAsciiResult:
    """Prepared morphology ASCII outputs and grid compatibility details."""

    outputs: dict[str, Path]
    headers: dict[str, Header]
    ratios: dict[str, int]


def prepare_morphology_ascii_files(
        layers: Iterable[MorphologyAsciiLayer | Mapping[str, Any]],
        headers: Mapping[str, Mapping[str, Any]],
        overwrite: bool = True,
        log: LogCallback | None = None) -> MorphologyAsciiResult:
    """Crop/align prepared morphology rasters to L0 and write mHM ASCII files."""
    standardized_headers, ratios = validate_grid_headers(headers)
    l0_header = standardized_headers["L0"]
    outputs: dict[str, Path] = {}

    _log(log, "Grid compatibility verified for ASCII export.")
    _log(
        log,
        "Grid ratios: "
        f"L0/L1={ratios['L0_to_L1']}, "
        f"L1/L2={ratios['L1_to_L2']}, "
        f"L11/L2={ratios['L11_to_L2']}.",
    )

    for layer in layers:
        spec = _layer_spec(layer)
        input_path = Path(spec.input_path)
        output_path = Path(spec.output_path)
        display_name = spec.name or output_path.name
        if not input_path.exists():
            raise FileNotFoundError(f"Input raster does not exist: {input_path}")
        if output_path.exists() and not overwrite:
            _log(log, f"{display_name} already exists. Skipping: {output_path}")
            outputs[display_name] = output_path
            continue

        _log(log, f"Preparing {display_name}: {input_path.name} -> {output_path.name}")
        dataset = _read_raster(input_path, spec.data_var)
        aligned = align_dataset_to_header(
            dataset,
            l0_header,
            data_var=spec.data_var,
            nodata_value=spec.nodata_value,
            integer=spec.integer,
        )
        _write_ascii(
            aligned,
            output_path,
            l0_header,
            nodata_value=spec.nodata_value,
        )
        outputs[display_name] = output_path
        _log(log, f"Written {display_name}: {output_path}")

    return MorphologyAsciiResult(
        outputs=outputs,
        headers=standardized_headers,
        ratios=ratios,
    )


def validate_grid_headers(
        headers: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Header], dict[str, int]]:
    """Validate L0, L1, L11, and L2 headers for mHM-compatible dimensions."""
    return validate_mhm_grid_headers(headers)


def align_dataset_to_header(
        dataset,
        header: Mapping[str, Any],
        data_var: str | None = None,
        nodata_value: float | int = -9999,
        integer: bool = False):
    """Return a dataset sampled on the exact grid described by ``header``."""
    import xarray as xr
    from .....externals.mhm_tools_handler import (xarray_coord_key,
                                                   xarray_single_data_var)

    header = standardize_header(header)
    var_name = data_var
    if var_name is None:
        var_name = xarray_single_data_var(dataset)
    if var_name is None or var_name not in dataset:
        raise ValueError("Cannot determine a single raster data variable for ASCII export.")

    x_key = xarray_coord_key(dataset, lon=True)
    y_key = xarray_coord_key(dataset, lat=True)
    source = dataset.sortby(x_key).sortby(y_key)
    assert_source_covers_header(source, x_key, y_key, header)

    target_x, target_y = header_center_coordinates(header)
    da = source[var_name]
    da = _drop_singleton_non_spatial_dims(da, x_key, y_key)
    # Sample by nearest label, not by interpolation. The source y-centres come
    # from the GDAL geotransform (top-down) while header_center_coordinates builds
    # them bottom-up: algebraically equal, ~3.6e-15 apart in float64. interp()
    # has no extrapolation domain, so that one ulp put the bottom row outside it
    # and _apply_nodata turned the resulting NaN into nodata -- an all-nodata
    # last row in every written layer. interp() also framed a genuinely coarser
    # source in nodata. sel() snaps to the nearest label and has no domain.
    # float64 is the input contract _apply_nodata already gets from
    # pad_dataset_to_header; interp() used to provide it by upcasting.
    aligned = da.sel(
        {x_key: target_x, y_key: target_y},
        method="nearest",
    ).astype("float64")
    # sel() returns the source labels, so this restores the exact header
    # coordinates that the written ASCII header is derived from. Load-bearing.
    aligned = aligned.assign_coords({
        x_key: xr.DataArray(
            target_x,
            dims=(x_key,),
            attrs={"axis": "X"},
        ),
        y_key: xr.DataArray(
            target_y,
            dims=(y_key,),
            attrs={"axis": "Y"},
        ),
    })
    aligned = _apply_nodata(aligned, nodata_value, integer)
    if aligned.sizes.get(y_key) != int(header["nrows"]) or aligned.sizes.get(x_key) != int(header["ncols"]):
        raise ValueError(
            "Aligned raster size does not match L0 header: "
            f"got {aligned.sizes.get(y_key)}x{aligned.sizes.get(x_key)}, "
            f"expected {int(header['nrows'])}x{int(header['ncols'])}."
        )
    return aligned.to_dataset(name=var_name)


def pad_dataset_to_header(
        dataset,
        header: Mapping[str, Any],
        data_var: str | None = None,
        nodata_value: float | int = -9999,
        integer: bool = False,
        pad_value: float | int | None = None):
    """Return a dataset placed on ``header`` by exact cell lookup, padded out.

    Unlike :func:`align_dataset_to_header` this never resamples. The source must
    already sit on the header cell grid; cells outside it receive ``pad_value``,
    or ``nodata_value`` when no pad value is given. Non-spatial dimensions such
    as time or soil horizon are preserved.
    """
    from .....externals.mhm_tools_handler import (xarray_coord_key,
                                                   xarray_single_data_var)

    header = standardize_header(header)
    var_name = data_var
    if var_name is None:
        var_name = xarray_single_data_var(dataset)
    if var_name is None or var_name not in dataset:
        raise ValueError("Cannot determine a single raster data variable for L0 padding.")

    x_key = xarray_coord_key(dataset, lon=True)
    y_key = xarray_coord_key(dataset, lat=True)
    source = dataset.sortby(x_key).sortby(y_key)
    _assert_cells_align(source, x_key, y_key, header)

    padded = reindex_to_header(
        source[var_name].astype("float64"),
        x_key,
        y_key,
        header,
        fill_value=float(nodata_value if pad_value is None else pad_value),
    )
    padded = _apply_nodata(padded, nodata_value, integer)
    if (
            padded.sizes.get(y_key) != int(header["nrows"])
            or padded.sizes.get(x_key) != int(header["ncols"])):
        raise ValueError(
            "Padded raster size does not match L0 header: "
            f"got {padded.sizes.get(y_key)}x{padded.sizes.get(x_key)}, "
            f"expected {int(header['nrows'])}x{int(header['ncols'])}."
        )
    return padded.to_dataset(name=var_name)


def pad_l0_file_to_header(
        path: Path | str,
        header: Mapping[str, Any],
        *,
        data_var: str | None = None,
        nodata_value: float | int = -9999,
        integer: bool = False,
        pad_value: float | int | None = None,
        fill_nodata: bool = False) -> Path:
    """Rewrite a model-ready raster onto the exact L0 header without resampling.

    Cells the source does not cover take ``pad_value``; the declared nodata
    stays ``nodata_value`` regardless.
    """
    path = Path(path)
    header = standardize_header(header)
    if path.suffix.lower() == ".asc":
        # ASCII grids are padded by streaming text, which keeps memory flat.
        # Loading one costs gigabytes: 3.2 GiB for a 13201 x 6001 soil raster.
        from .pad import pad_ascii_grid

        return pad_ascii_grid(path, header, nodata=nodata_value, pad=pad_value, fill_nodata=fill_nodata)
    dataset = _read_raster(path, data_var)
    if _grid_matches_header(dataset, header) and not fill_nodata:
        dataset.close()
        return path

    padded = pad_dataset_to_header(
        dataset,
        header,
        data_var=data_var,
        nodata_value=nodata_value,
        integer=integer,
        pad_value=pad_value,
    )
    if fill_nodata:
        for name in padded.data_vars:
            padded[name] = padded[name].where(padded[name] != nodata_value, pad_value).fillna(pad_value)
    temporary = path.with_name(f".{path.name}.tmp{path.suffix or '.nc'}")
    temporary.unlink(missing_ok=True)
    padded.to_netcdf(temporary)
    dataset.close()
    temporary.replace(path)
    return path


def _grid_matches_header(dataset, header: Mapping[str, Any]) -> bool:
    """Return True when a dataset already sits exactly on the header grid."""
    from .....externals.mhm_tools_handler import xarray_coord_key

    x_key = xarray_coord_key(dataset, lon=True)
    y_key = xarray_coord_key(dataset, lat=True)
    return grid_matches_header(dataset, x_key, y_key, header)


def file_matches_header(path, header) -> bool:
    """Check a prepared grid without loading its raster values."""
    path = Path(path)
    if path.suffix.lower() == ".asc":
        from .pad import read_ascii_header

        return headers_match(read_ascii_header(path), header)
    import xarray as xr

    with xr.open_dataset(path) as dataset:
        return _grid_matches_header(dataset, header)


def _assert_cells_align(dataset, x_key, y_key, header) -> None:
    """Reject a source whose cells are not on the header cell grid."""
    if not axes_match_header(
            dataset[x_key].values, dataset[y_key].values, dict(header)):
        raise ValueError(
            "Source raster cells are not aligned to the L0 grid "
            f"(cell size {float(header['cellsize'])}); it cannot be placed on "
            "the common extent without resampling."
        )


def _read_raster(path: Path, data_var: str | None):
    from .....externals.mhm_tools_handler import read_xarray_dataset

    return read_xarray_dataset(
        path,
        var_name=data_var,
        force_decending_y=True,
    )


def _write_ascii(dataset, output_path: Path, header: Mapping[str, Any],
                 nodata_value: float | int) -> None:
    from .....externals.mhm_tools_handler import (write_xarray_ascii,
                                                   xarray_single_data_var)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_var = xarray_single_data_var(dataset)
    write_xarray_ascii(
        dataset,
        output_path,
        data_var=data_var,
        nodata_value=nodata_value,
        resolution=float(header["cellsize"]),
    )
    expected = standardize_header(header)
    written = _read_ascii_header(output_path, unit=expected.get("unit", ""))
    _assert_header_matches(written, expected, output_path.name)


def _layer_spec(layer: MorphologyAsciiLayer | Mapping[str, Any]) -> MorphologyAsciiLayer:
    if isinstance(layer, MorphologyAsciiLayer):
        return layer
    return MorphologyAsciiLayer(
        input_path=layer["input_path"],
        output_path=layer["output_path"],
        name=layer.get("name", ""),
        nodata_value=layer.get("nodata_value", -9999),
        integer=bool(layer.get("integer", False)),
        data_var=layer.get("data_var"),
    )


def _assert_header_matches(written: Mapping[str, Any], expected: Header, name: str) -> None:
    if not headers_match(written, expected):
        raise ValueError(f"Written ASCII {name} does not match the target grid header.")


def _read_ascii_header(path: Path, unit: str | None = None) -> Header:
    header: dict[str, float | int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            key = parts[0].lower()
            if key == "nodata_value":
                header[key] = float(parts[1])
            elif key in {"ncols", "nrows"}:
                header[key] = int(float(parts[1]))
            elif key in {"xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize"}:
                header[key] = float(parts[1])
            if len(header) >= 6:
                break
    if unit:
        header["unit"] = unit
    return standardize_header(header)


def _drop_singleton_non_spatial_dims(da, x_key: str, y_key: str):
    extra_dims = [dim for dim in da.dims if dim not in {x_key, y_key}]
    for dim in extra_dims:
        if da.sizes.get(dim) != 1:
            raise ValueError(
                f"ASCII export requires a 2D raster; dimension {dim!r} has "
                f"size {da.sizes.get(dim)}."
            )
        da = da.isel({dim: 0}, drop=True)
    return da


def _apply_nodata(da, nodata_value: float | int, integer: bool):
    source_nodata = da.attrs.get("_FillValue", da.attrs.get("nodata_value"))
    if source_nodata is not None:
        try:
            da = da.where(da != source_nodata, nodata_value)
        except Exception:
            pass
    da = da.fillna(nodata_value)
    if integer:
        da = da.round().astype("int32")
    da.attrs["nodata_value"] = nodata_value
    da.attrs["_FillValue"] = nodata_value
    return da


def _log(log: LogCallback | None, message: str) -> None:
    if log:
        log(message)


__all__ = [
    "MorphologyAsciiLayer",
    "MorphologyAsciiResult",
    "align_dataset_to_header",
    "file_matches_header",
    "pad_dataset_to_header",
    "pad_l0_file_to_header",
    "prepare_morphology_ascii_files",
    "validate_grid_headers",
]
