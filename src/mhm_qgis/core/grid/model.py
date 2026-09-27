# -*- coding: utf-8 -*-
"""Exact, QGIS-free grid, resolution and extent calculations."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence


NODATA_VALUE = -9999.0
DEFAULT_ALIGNMENT_GAP_LIMIT = 1.0e-7
SETTINGS_PATH = Path(__file__).parents[2] / "settings.yaml"
PROJECTED_CELLSIZE_PRECISION = 8
GEOGRAPHIC_CELLSIZE_PRECISION = 8


@dataclass
class Resolution:
    """The exact mHM resolutions, without file loading or rounding."""

    l0: float | None = None
    l1: float | None = None
    l11: float | None = None
    l2: float | None = None

    def __post_init__(self) -> None:
        for name in ("l0", "l1", "l11", "l2"):
            value = getattr(self, name)
            if value is not None:
                value = float(value)
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{name.upper()} resolution must be positive.")
                setattr(self, name, value)
        if self.l11 is None:
            self.l11 = self.l1
        if self.l2 is None:
            self.l2 = self.l1

    @property
    def l0_resolution(self):
        return self.l0

    @property
    def l1_resolution(self):
        return self.l1

    @property
    def l11_resolution(self):
        return self.l11

    @property
    def l2_resolution(self):
        return self.l2

    def get_max_resolution(self):
        values = [value for value in (self.l1, self.l11, self.l2) if value]
        return max(values) if values else None

    def only_one_resolution(self):
        values = {value for value in (self.l0, self.l1, self.l11, self.l2) if value}
        return values.pop() if len(values) == 1 else None


@dataclass(frozen=True)
class Extent:
    """Cell-edge bounds in one CRS, ordered xmin, xmax, ymin, ymax."""

    xmin: float
    xmax: float
    ymin: float
    ymax: float

    def __post_init__(self) -> None:
        values = tuple(map(float, self.bounds))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Grid extent values must be finite.")
        if self.xmax < self.xmin or self.ymax < self.ymin:
            raise ValueError("Grid extent maximums must not be below minimums.")
        for name, value in zip(("xmin", "xmax", "ymin", "ymax"), values):
            object.__setattr__(self, name, value)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.xmin, self.xmax, self.ymin, self.ymax

    @classmethod
    def from_bounds(cls, bounds: Sequence[float]) -> "Extent":
        xmin, xmax, ymin, ymax = map(float, bounds)
        return cls(min(xmin, xmax), max(xmin, xmax), min(ymin, ymax), max(ymin, ymax))

    @classmethod
    def from_header(cls, header: Mapping) -> "Extent":
        return cls.from_bounds(header_bounds(header))

    @classmethod
    def from_centres(
        cls,
        bounds: Sequence[float],
        x_resolution: float,
        y_resolution: float,
    ) -> "Extent":
        """Convert min/max cell-centre coordinates to cell-edge bounds."""
        xmin, xmax, ymin, ymax = cls.from_bounds(bounds).bounds
        half_x, half_y = abs(float(x_resolution)) / 2, abs(float(y_resolution)) / 2
        return cls(xmin - half_x, xmax + half_x, ymin - half_y, ymax + half_y)

    @classmethod
    def union(cls, extents: Iterable["Extent | Sequence[float]"]) -> "Extent":
        values = [item if isinstance(item, cls) else cls.from_bounds(item) for item in extents]
        if not values:
            raise ValueError("At least one domain extent is required.")
        return cls(
            min(item.xmin for item in values),
            max(item.xmax for item in values),
            min(item.ymin for item in values),
            max(item.ymax for item in values),
        )


@dataclass(frozen=True)
class TargetGrid:
    """Target cell-centre coordinates and their exact mHM header."""

    lon: Sequence[float]
    lat: Sequence[float]
    header: Mapping[str, float]
    crs: str | None = None
    sample_lon: object | None = None
    sample_lat: object | None = None

    def validate(self) -> None:
        if len(self.lon) == 0 or len(self.lat) == 0:
            raise ValueError("The target meteorology grid is empty.")
        if int(self.header["ncols"]) != len(self.lon):
            raise ValueError("Target longitude count does not match header ncols.")
        if int(self.header["nrows"]) != len(self.lat):
            raise ValueError("Target latitude count does not match header nrows.")
        if (self.sample_lon is None) != (self.sample_lat is None):
            raise ValueError("Both target sample longitude and latitude grids are required.")
        if self.sample_lon is not None:
            import numpy as np

            shape = (int(self.header["nrows"]), int(self.header["ncols"]))
            if np.shape(self.sample_lon) != shape or np.shape(self.sample_lat) != shape:
                raise ValueError("Target sample coordinates do not match the header shape.")


@dataclass(frozen=True)
class SpatialResolution:
    """A measured x/y grid resolution expressed in one CRS."""

    resolution: float
    x_resolution: float
    y_resolution: float
    unit: str
    crs: str | None


def raster_resolution_info(
    bounds: Sequence[float],
    ncols: int,
    nrows: int,
    *,
    unit: str = "",
    crs: str = "",
) -> dict:
    """Return exact resolution and header metadata from raster primitives."""
    extent = Extent.from_bounds(bounds)
    columns, rows = int(ncols), int(nrows)
    if columns <= 0 or rows <= 0:
        raise ValueError("Raster dimensions must be positive.")
    x_resolution = (extent.xmax - extent.xmin) / columns
    y_resolution = (extent.ymax - extent.ymin) / rows
    resolution = (x_resolution + y_resolution) / 2
    return {
        "resolution": resolution,
        "x_resolution": x_resolution,
        "y_resolution": y_resolution,
        "exact_resolution": resolution,
        "exact_x_resolution": x_resolution,
        "exact_y_resolution": y_resolution,
        "unit": unit,
        "crs_authid": crs,
        "header": {
            "ncols": columns,
            "nrows": rows,
            "xllcorner": extent.xmin,
            "yllcorner": extent.ymin,
            "cellsize": resolution,
            "nodata_value": NODATA_VALUE,
            "unit": unit,
        },
    }


def resolution_in_crs(metadata, target_crs: str | None) -> SpatialResolution:
    """Express an inspected rectilinear grid resolution in ``target_crs``."""
    source_crs = metadata.crs
    if not target_crs or not source_crs or crs_equal(source_crs, target_crs):
        return SpatialResolution(
            metadata.resolution,
            metadata.x_resolution,
            metadata.y_resolution,
            metadata.unit,
            target_crs or source_crs,
        )
    try:
        from pyproj import CRS, Transformer
    except Exception as error:
        raise RuntimeError("pyproj is required to transform a grid resolution.") from error
    west, east, south, north = metadata.bounds
    x0, y0 = (west + east) / 2, (south + north) / 2
    transform = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    p0 = transform.transform(x0, y0)
    px = transform.transform(x0 + metadata.x_resolution, y0)
    py = transform.transform(x0, y0 + metadata.y_resolution)
    dx = math.hypot(px[0] - p0[0], px[1] - p0[1])
    dy = math.hypot(py[0] - p0[0], py[1] - p0[1])
    crs = CRS.from_user_input(target_crs)
    unit_name = crs.axis_info[0].unit_name.lower() if crs.axis_info else ""
    unit = "deg" if crs.is_geographic else "m" if unit_name in {"metre", "meter"} else unit_name
    return SpatialResolution((dx + dy) / 2, dx, dy, unit, target_crs)


@dataclass
class Grid(Resolution):
    """mHM resolutions plus source-level extents and their common target extent."""

    crs: str = ""
    unit: str = ""
    l0_extent: Extent | None = None
    l1_extent: Extent | None = None
    l11_extent: Extent | None = None
    l2_extent: Extent | None = None
    raw_l2_extent: Extent | None = None
    common_extent: Extent | None = None
    l0_header: dict = field(default_factory=dict)
    l2_header: dict = field(default_factory=dict)
    raw_l2_factor: int | None = None
    alignment_gap: tuple[float, float] | None = None
    alignment_gap_limit: float = 0.0
    requires_l2_resampling: bool = True

    @classmethod
    def for_meteorology(
        cls,
        l0_header: Mapping,
        domain_extents: Iterable[Extent | Sequence[float]],
        multiplier: int,
        *,
        crs: str = "",
        unit: str = "",
        raw_l2_resolution: Sequence[float] | None = None,
        raw_l2_extent: Extent | None = None,
        raw_l2_crs: str | None = None,
        gap_limit_fraction: float | None = None,
    ) -> "Grid":
        """Build the exact common L0/L2 target selected by the user."""
        ratio = _positive_integer(multiplier)
        normalized_l0 = standardize_header(l0_header, unit=unit)
        l0_resolution = float(normalized_l0["cellsize"])
        source_l0_extent = Extent.from_header(normalized_l0)
        requested_extent = Extent.union(domain_extents)
        limit_fraction = (
            alignment_gap_limit_fraction()
            if gap_limit_fraction is None
            else float(gap_limit_fraction)
        )
        if not math.isfinite(limit_fraction) or limit_fraction < 0:
            raise ValueError("The grid alignment-gap limit must be non-negative.")

        raw_factor = None
        if raw_l2_resolution is not None:
            x_resolution, y_resolution = map(abs, map(float, raw_l2_resolution))
            x_factor = integer_resolution_factor(x_resolution, l0_resolution)
            y_factor = integer_resolution_factor(y_resolution, l0_resolution)
            if x_factor == y_factor:
                raw_factor = x_factor

        gap = None
        anchor = (float(normalized_l0["xllcorner"]), float(normalized_l0["yllcorner"]))
        comparable = bool(
            raw_l2_extent is not None
            and raw_factor == ratio
            and crs_equal(raw_l2_crs, crs)
        )
        gap_limit = l0_resolution * limit_fraction
        if comparable:
            gap = alignment_gap(source_l0_extent, raw_l2_extent, l0_resolution)
            anchor = (
                raw_l2_extent.xmin + gap[0],
                raw_l2_extent.ymin + gap[1],
            )
        resampling = not comparable or any(abs(value) > gap_limit for value in gap or ())
        exact_l0, l2 = aligned_l0_l2_headers(
            requested_extent.bounds,
            normalized_l0,
            ratio,
            unit,
            anchor=anchor,
        )
        common = Extent.from_header(l2)
        return cls(
            l0=l0_resolution,
            l2=float(l2["cellsize"]),
            crs=str(crs or ""),
            unit=str(unit or ""),
            l0_extent=source_l0_extent,
            l2_extent=common,
            raw_l2_extent=raw_l2_extent,
            common_extent=common,
            l0_header=exact_l0,
            l2_header=l2,
            raw_l2_factor=raw_factor,
            alignment_gap=gap,
            alignment_gap_limit=gap_limit,
            requires_l2_resampling=resampling,
        )

    @property
    def multiplier(self) -> int:
        factor = integer_resolution_factor(self.l2, self.l0)
        if factor is None:
            raise ValueError("L2 resolution is not an integer multiple of L0.")
        return factor

    def metadata(self, *, extent_source: str = "", source_file: str = "") -> dict:
        """Return the JSON-safe processing metadata for this grid."""
        result = {
            "version": 3,
            "l0_resolution": self.l0,
            "l0_unit": self.unit,
            "l2_resolution": self.l2,
            "l2_unit": self.unit,
            "l2_ratio_to_l0": self.multiplier,
            "extent_source": str(extent_source),
            "l2_bounds": self.common_extent.bounds if self.common_extent else None,
            "crs_authid": self.crs,
            "l2_header": dict(self.l2_header),
            "l0_header": dict(self.l0_header),
            "raw_l2_ratio_to_l0": self.raw_l2_factor,
            "alignment_gap": list(self.alignment_gap) if self.alignment_gap else None,
            "alignment_gap_limit": self.alignment_gap_limit,
            "requires_l2_resampling": self.requires_l2_resampling,
        }
        if source_file:
            result["source_file"] = source_file
        return result


def alignment_gap_limit_fraction(path: Path | str | None = None) -> float:
    """Read the dimensionless limit from an explicit or packaged settings file."""
    from ..handlers.state.settings import read

    return read(path=path if path is not None else SETTINGS_PATH)["grid_alignment_gap_limit"]


def crs_equal(left: str | None, right: str | None) -> bool:
    """Compare two CRS strings without importing QGIS."""
    if not left or not right:
        return not left and not right
    try:
        from pyproj import CRS

        return bool(CRS.from_user_input(left) == CRS.from_user_input(right))
    except Exception:
        return str(left).strip().casefold() == str(right).strip().casefold()


def alignment_gap(l0_extent: Extent, l2_extent: Extent, l0_resolution: float):
    """Return the smallest signed shift putting the L2 edge on the L0 lattice."""
    cellsize = float(l0_resolution)
    if not math.isfinite(cellsize) or cellsize <= 0:
        raise ValueError("The L0 resolution must be positive.")

    def gap(value, anchor):
        return anchor + round((value - anchor) / cellsize) * cellsize - value

    return gap(l2_extent.xmin, l0_extent.xmin), gap(l2_extent.ymin, l0_extent.ymin)


def is_geographic_unit(unit: str | None) -> bool:
    return (unit or "").strip().lower() in {"deg", "degree", "degrees"}


def cellsize_precision_for_unit(unit: str | None) -> int:
    return GEOGRAPHIC_CELLSIZE_PRECISION if is_geographic_unit(unit) else PROJECTED_CELLSIZE_PRECISION


def floor_to_precision(value, precision: int) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        return numeric
    quantum = Decimal("1").scaleb(-int(precision))
    return float(Decimal(str(numeric)).quantize(quantum, rounding=ROUND_FLOOR))


def floor_cellsize(value, unit: str | None = None, precision: int | None = None) -> float:
    return floor_to_precision(
        value,
        cellsize_precision_for_unit(unit) if precision is None else precision,
    )


def ceil_cellsize(value, unit: str | None = None, precision: int | None = None) -> float:
    return floor_cellsize(value, unit, precision)


def display_precision_for_unit(unit: str | None) -> int:
    return 6 if is_geographic_unit(unit) else 3


def format_resolution(value, unit: str | None = None, precision: int | None = None):
    if precision is None:
        precision = display_precision_for_unit(unit)
    try:
        text = f"{float(value):.{precision}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""
    return text or "0"


def nearest_integer_multiple(raw_resolution: float, base_resolution: float, unit=None):
    if raw_resolution <= 0 or base_resolution <= 0:
        raise ValueError("Grid resolutions must be positive.")
    ratio = max(1, int(round(float(raw_resolution) / float(base_resolution))))
    return ratio * float(base_resolution), ratio


def integer_resolution_factor(coarse_resolution, fine_resolution, unit=None, tolerance=None):
    if coarse_resolution is None or fine_resolution is None:
        return None
    coarse, fine = float(coarse_resolution), float(fine_resolution)
    if coarse <= 0 or fine <= 0:
        return None
    ratio = coarse / fine
    nearest = int(round(ratio))
    tolerance = 1e-7 if tolerance is None else float(tolerance)
    return nearest if nearest >= 1 and abs(ratio - nearest) <= tolerance else None


def resolution_is_multiple(coarse_resolution, fine_resolution, tolerance=None, unit=None):
    return integer_resolution_factor(coarse_resolution, fine_resolution, unit, tolerance) is not None


def possible_resolutions(fine_resolution: float, coarse_resolution: float, unit=None):
    factor = integer_resolution_factor(coarse_resolution, fine_resolution, unit)
    if factor is None:
        return []
    return [
        float(coarse_resolution) / (factor // divisor)
        for divisor in range(1, factor + 1)
        if factor % divisor == 0
    ]


def standardize_header(header: Mapping, *, unit: str | None = None) -> dict:
    result = {str(key).lower(): value for key, value in dict(header).items()}
    cellsize = float(result.get("exact_cellsize", result.get("cellsize", 0)))
    if "xllcenter" in result and "xllcorner" not in result:
        result["xllcorner"] = float(result["xllcenter"]) - cellsize / 2
    if "yllcenter" in result and "yllcorner" not in result:
        result["yllcorner"] = float(result["yllcenter"]) - cellsize / 2
    required = {"ncols", "nrows", "xllcorner", "yllcorner"}
    missing = required - result.keys()
    if missing or cellsize <= 0:
        names = ", ".join(sorted(missing or {"cellsize"}))
        raise ValueError(f"Grid header is missing required key(s): {names}")
    resolved_unit = str(unit if unit is not None else result.get("unit", "") or "")
    return {
        "ncols": int(float(result["ncols"])),
        "nrows": int(float(result["nrows"])),
        "xllcorner": float(result["xllcorner"]),
        "yllcorner": float(result["yllcorner"]),
        "cellsize": cellsize,
        "nodata_value": float(result.get("nodata_value", result.get("nodata", NODATA_VALUE))),
        "unit": resolved_unit,
        "cellsize_precision": cellsize_precision_for_unit(resolved_unit),
    }


def header_bounds(header: Mapping) -> tuple[float, float, float, float]:
    xmin = float(header["xllcorner"])
    ymin = float(header["yllcorner"])
    cellsize = float(header["cellsize"])
    return (
        xmin,
        xmin + int(header["ncols"]) * cellsize,
        ymin,
        ymin + int(header["nrows"]) * cellsize,
    )


def north_up_cellsize(geotransform: Sequence[float]) -> float:
    """Return a square north-up pixel size or raise for a rotated grid."""
    values = tuple(map(float, geotransform))
    if len(values) != 6:
        raise ValueError("A raster geotransform must contain six values.")
    cellsize = abs(values[1])
    tolerance = max(cellsize * 1e-8, 1e-12)
    if (
        cellsize <= 0
        or not math.isclose(values[1], cellsize, rel_tol=0, abs_tol=tolerance)
        or not math.isclose(values[5], -cellsize, rel_tol=0, abs_tol=tolerance)
        or abs(values[2]) > tolerance
        or abs(values[4]) > tolerance
    ):
        raise ValueError("The raster grid must be square, north-up and unrotated.")
    return cellsize


def header_from_geotransform(
    geotransform: Sequence[float], ncols: int, nrows: int, unit: str = ""
) -> dict:
    """Build a normalized header from a north-up GDAL geotransform."""
    cellsize = north_up_cellsize(geotransform)
    return standardize_header(
        {
            "ncols": ncols,
            "nrows": nrows,
            "xllcorner": float(geotransform[0]),
            "yllcorner": float(geotransform[3]) - int(nrows) * cellsize,
            "cellsize": cellsize,
        },
        unit=unit,
    )


def geotransform_from_header(header: Mapping) -> tuple[float, ...]:
    """Return the north-up GDAL geotransform described by a grid header."""
    normalized = standardize_header(header)
    cellsize = normalized["cellsize"]
    ymax = normalized["yllcorner"] + normalized["nrows"] * cellsize
    return normalized["xllcorner"], cellsize, 0.0, ymax, 0.0, -cellsize


def geotransform_bounds(
    geotransform: Sequence[float], ncols: int, nrows: int
) -> tuple[float, float, float, float]:
    """Return GDAL output bounds: xmin, ymin, xmax, ymax."""
    transform = tuple(map(float, geotransform))
    corners = ((0, 0), (int(ncols), 0), (0, int(nrows)), (int(ncols), int(nrows)))
    xs = [transform[0] + col * transform[1] + row * transform[2] for col, row in corners]
    ys = [transform[3] + col * transform[4] + row * transform[5] for col, row in corners]
    return min(xs), min(ys), max(xs), max(ys)


def headers_match(first: Mapping, second: Mapping, tolerance_fraction=1e-6) -> bool:
    """Return whether two headers describe the same grid."""
    left, right = standardize_header(first), standardize_header(second)
    if (left["nrows"], left["ncols"]) != (right["nrows"], right["ncols"]):
        return False
    tolerance = max(abs(right["cellsize"]) * float(tolerance_fraction), 1e-12)
    return all(
        math.isclose(left[key], right[key], rel_tol=0, abs_tol=tolerance)
        for key in ("xllcorner", "yllcorner", "cellsize")
    )


def raster_grids_match(
    first: Mapping,
    second: Mapping,
    *,
    tolerance=1e-9,
    allow_missing_projection=False,
) -> bool:
    """Compare two raster metadata mappings without opening their files."""
    if (first["rows"], first["cols"]) != (second["rows"], second["cols"]):
        return False
    if not all(
        math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
        for left, right in zip(first["geotransform"], second["geotransform"])
    ):
        return False
    left_projection = first.get("projection") or ""
    right_projection = second.get("projection") or ""
    if allow_missing_projection and not (left_projection and right_projection):
        return True
    return left_projection == right_projection


def _header(bounds, cellsize, anchor_x, anchor_y, unit):
    extent = Extent.from_bounds(bounds)

    def index(value, anchor, upper):
        quotient = (value - anchor) / cellsize
        nearest = round(quotient)
        tolerance = 1e-9 * max(1.0, abs(quotient))
        if math.isclose(quotient, nearest, rel_tol=0.0, abs_tol=tolerance):
            return int(nearest)
        return math.ceil(quotient) if upper else math.floor(quotient)

    x0, x1 = index(extent.xmin, anchor_x, False), index(extent.xmax, anchor_x, True)
    y0, y1 = index(extent.ymin, anchor_y, False), index(extent.ymax, anchor_y, True)
    return {
        "ncols": max(1, x1 - x0),
        "nrows": max(1, y1 - y0),
        "xllcorner": anchor_x + x0 * cellsize,
        "yllcorner": anchor_y + y0 * cellsize,
        "cellsize": float(cellsize),
        "nodata_value": NODATA_VALUE,
        "unit": unit or "",
        "cellsize_precision": cellsize_precision_for_unit(unit),
    }


def header_for_bounds(bounds, cellsize: float, unit: str | None = None) -> dict:
    return _header(bounds, float(cellsize), 0.0, 0.0, unit)


def header_for_aligned_bounds(bounds, cellsize, anchor_x, anchor_y, unit=None) -> dict:
    return _header(bounds, float(cellsize), float(anchor_x), float(anchor_y), unit)


def header_for_existing_bounds(reference_header: Mapping, cellsize: float, unit=None) -> dict:
    source = standardize_header(reference_header)
    extent = Extent.from_header(source)
    resolution = float(cellsize)
    result = {
        **source,
        "ncols": max(1, int(round((extent.xmax - extent.xmin) / resolution))),
        "nrows": max(1, int(round((extent.ymax - extent.ymin) / resolution))),
        "cellsize": resolution,
        "unit": unit or source.get("unit", ""),
    }
    result["cellsize_precision"] = cellsize_precision_for_unit(result["unit"])
    return result


def header_for_model_level(
    l2_header: Mapping,
    cellsize: float,
    l0_cellsize: float,
    level: str,
    *,
    tolerance: float = 1e-6,
) -> dict:
    """Build one exact common-extent model grid from integer cell factors."""
    l2 = standardize_header(l2_header)
    l0_factor = integer_resolution_factor(
        cellsize, l0_cellsize, tolerance=tolerance
    )
    if l0_factor is None:
        raise ValueError(
            f"{level}/L0 resolution ratio must be an integer within {tolerance:g}."
        )

    resolution = l0_factor * float(l0_cellsize)
    finer_factor = integer_resolution_factor(
        l2["cellsize"], resolution, tolerance=tolerance
    )
    coarser_factor = integer_resolution_factor(
        resolution, l2["cellsize"], tolerance=tolerance
    )
    if finer_factor is not None:
        ncols = l2["ncols"] * finer_factor
        nrows = l2["nrows"] * finer_factor
    elif coarser_factor is not None:
        if l2["ncols"] % coarser_factor or l2["nrows"] % coarser_factor:
            raise ValueError(
                f"{level} is {coarser_factor} x L2, but the L2 dimensions "
                f"{l2['nrows']} x {l2['ncols']} are not divisible by "
                f"{coarser_factor} on the common extent."
            )
        ncols = l2["ncols"] // coarser_factor
        nrows = l2["nrows"] // coarser_factor
    else:
        raise ValueError(
            f"{level}/L2 or L2/{level} resolution ratio must be an integer "
            f"within {tolerance:g}."
        )

    result = {
        **l2,
        "ncols": ncols,
        "nrows": nrows,
        "cellsize": resolution,
    }
    result["cellsize_precision"] = cellsize_precision_for_unit(result.get("unit"))
    return result


def aligned_l0_l2_headers(bounds, l0_header, multiplier, unit=None, *, anchor=None):
    ratio = _positive_integer(multiplier)
    source = dict(l0_header)
    l0_cellsize = float(source.get("exact_cellsize", source.get("cellsize", 0)))
    if not math.isfinite(l0_cellsize) or l0_cellsize <= 0:
        raise ValueError("The L0 cell size must be positive.")
    try:
        source_anchor = (float(source["xllcorner"]), float(source["yllcorner"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("The L0 grid requires a lower-left anchor.") from error
    anchor_x, anchor_y = anchor or source_anchor
    resolved_unit = unit or str(source.get("unit", "") or "")
    l2 = header_for_aligned_bounds(
        bounds,
        ratio * l0_cellsize,
        anchor_x,
        anchor_y,
        resolved_unit,
    )
    exact_l0 = {
        **l2,
        "ncols": int(l2["ncols"]) * ratio,
        "nrows": int(l2["nrows"]) * ratio,
        "cellsize": l0_cellsize,
    }
    validate_l0_l2_alignment(exact_l0, l2, ratio)
    return exact_l0, l2


def l0_header_from_l2(l2_header, l0_cellsize, multiplier):
    ratio = _positive_integer(multiplier)
    l2 = standardize_header(l2_header)
    tolerance = max(abs(l2["cellsize"]), 1.0) * 1e-9
    if not math.isclose(l2["cellsize"], ratio * float(l0_cellsize), rel_tol=0, abs_tol=tolerance):
        raise ValueError("The saved L2 resolution is not an exact multiple of L0.")
    l0 = {
        **l2,
        "ncols": l2["ncols"] * ratio,
        "nrows": l2["nrows"] * ratio,
        "cellsize": float(l0_cellsize),
    }
    validate_l0_l2_alignment(l0, l2, ratio)
    return l0


def validate_l0_l2_alignment(l0_header, l2_header, multiplier) -> None:
    ratio = _positive_integer(multiplier)
    l0, l2 = standardize_header(l0_header), standardize_header(l2_header)
    tolerance = max(abs(l2["cellsize"]), abs(l0["cellsize"]), 1.0) * 1e-9
    if not math.isclose(l2["cellsize"], ratio * l0["cellsize"], rel_tol=0, abs_tol=tolerance):
        raise ValueError("L2 cell size must equal multiplier x L0 cell size.")
    if l0["ncols"] != l2["ncols"] * ratio or l0["nrows"] != l2["nrows"] * ratio:
        raise ValueError("L0 dimensions must equal L2 dimensions x multiplier.")
    if any(not math.isclose(l0[key], l2[key], rel_tol=0, abs_tol=tolerance) for key in ("xllcorner", "yllcorner")):
        raise ValueError("L0 and L2 must share the same lower-left boundary.")
    if any(not math.isclose(left, right, rel_tol=0, abs_tol=tolerance) for left, right in zip(header_bounds(l0), header_bounds(l2))):
        raise ValueError("L0 and L2 must share the same outer extent.")


def validate_grid_headers(headers: Mapping[str, Mapping]):
    """Validate the complete L0/L1/L11/L2 mHM grid contract."""
    required = ("L0", "L1", "L11", "L2")
    missing = [level for level in required if level not in headers]
    if missing:
        raise ValueError(f"Missing grid header(s): {', '.join(missing)}")
    standardized = {level: standardize_header(headers[level]) for level in required}
    reference = header_bounds(standardized["L0"])
    tolerance = max(item["cellsize"] for item in standardized.values()) * 1e-6
    for level, header in standardized.items():
        bounds = header_bounds(header)
        if any(abs(left - right) > tolerance for left, right in zip(reference, bounds)):
            raise ValueError(f"{level} extent {bounds} does not match L0 extent {reference}.")

    l0_cellsize = standardized["L0"]["cellsize"]
    ratios = {}
    for level, key in (("L1", "L1_to_L2"), ("L11", "L11_to_L2")):
        header = standardized[level]
        expected = header_for_model_level(
            standardized["L2"], header["cellsize"], l0_cellsize, level
        )
        if (header["nrows"], header["ncols"]) != (
            expected["nrows"], expected["ncols"]
        ):
            raise ValueError(
                f"{level} dimensions must be {expected['nrows']} x "
                f"{expected['ncols']} on the common extent."
            )
        ratios[key] = (
            integer_resolution_factor(
                header["cellsize"],
                standardized["L2"]["cellsize"],
                tolerance=1e-6,
            )
            or integer_resolution_factor(
                standardized["L2"]["cellsize"],
                header["cellsize"],
                tolerance=1e-6,
            )
        )
    ratios["L0_to_L1"] = integer_resolution_factor(
        standardized["L1"]["cellsize"], l0_cellsize, tolerance=1e-6
    )
    return standardized, ratios


def aligned_window(source_header: Mapping, target_header: Mapping, *, label="raster") -> dict:
    """Return integer copy offsets for two aligned grids without opening files."""
    source, target = standardize_header(source_header), standardize_header(target_header)
    cellsize = target["cellsize"]
    tolerance = max(abs(cellsize) * 1e-8, 1e-12)
    if not math.isclose(source["cellsize"], cellsize, rel_tol=0, abs_tol=tolerance):
        raise ValueError(f"{label} is not aligned to the target grid resolution.")

    def offset(value, name):
        nearest = round(value)
        if not math.isclose(value, nearest, rel_tol=0, abs_tol=1e-7):
            raise ValueError(f"{label} {name} is not aligned to the target cell grid.")
        return int(nearest)

    source_ymax = source["yllcorner"] + source["nrows"] * cellsize
    target_ymax = target["yllcorner"] + target["nrows"] * cellsize
    destination_x = offset(
        (source["xllcorner"] - target["xllcorner"]) / cellsize, "x origin"
    )
    destination_y = offset((target_ymax - source_ymax) / cellsize, "y origin")
    source_x, source_y = max(0, -destination_x), max(0, -destination_y)
    target_x, target_y = max(0, destination_x), max(0, destination_y)
    return {
        "cellsize": cellsize,
        "target_xmin": target["xllcorner"],
        "target_ymax": target_ymax,
        "target_cols": target["ncols"],
        "target_rows": target["nrows"],
        "source_x": source_x,
        "source_y": source_y,
        "target_x": target_x,
        "target_y": target_y,
        "copy_cols": min(source["ncols"] - source_x, target["ncols"] - target_x),
        "copy_rows": min(source["nrows"] - source_y, target["nrows"] - target_y),
    }


def header_center_coordinates(header: Mapping) -> tuple[list[float], list[float]]:
    normalized = standardize_header(header)
    cellsize = normalized["cellsize"]
    x_values = [
        normalized["xllcorner"] + (index + 0.5) * cellsize
        for index in range(normalized["ncols"])
    ]
    y_values = [
        normalized["yllcorner"] + (index + 0.5) * cellsize
        for index in range(normalized["nrows"])
    ]
    return x_values, y_values[::-1]


def read_header_file(path, unit: str | None = None) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    header = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key, value = parts[0].lower(), parts[1]
        if key in {"ncols", "nrows"}:
            header[key] = int(float(value))
        elif key in {"xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value"}:
            header[key] = float(value)
    try:
        return standardize_header(header, unit=unit)
    except ValueError:
        return None


def write_header_file(path, header: Mapping) -> None:
    normalized = standardize_header(header)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "ncols         {ncols}\n"
        "nrows         {nrows}\n"
        "xllcorner     {xllcorner}\n"
        "yllcorner     {yllcorner}\n"
        "cellsize      {cellsize}\n"
        "NODATA_value  {nodata_value}\n".format(**normalized),
        encoding="utf-8",
    )


def _positive_integer(value) -> int:
    try:
        integer = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("The L2 resolution multiplier must be an integer.") from error
    if integer < 1 or float(value) != integer:
        raise ValueError("The L2 resolution multiplier must be a positive integer.")
    return integer
