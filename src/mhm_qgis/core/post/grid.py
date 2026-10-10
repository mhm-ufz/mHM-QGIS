"""The L1 grid of a run: axes, cell areas, basin weights and the sealed fraction.

Basin weights are the fraction of each L1 cell inside a basin, from its L0 mask by
exact block mean (L0 cell centres binned into L1 cells, divided by factor²). mHM
does the same for its L1 cell areas (`common/mo_common_grid.f90:171`), so
`cell_area_m2 * domain_weights` is mHM's effective cell area. No pyproj here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .catalog import value

EARTH_RADIUS_M = 6_371_008.8
FRAC_SEALED_DEFAULT = 0.6  # fracSealed_cityArea when the namelist omits it (v5.13 schema default)
# A missing or unreadable L0 file or namelist entry; GDAL raises RuntimeError with exceptions on.
_UNREADABLE = (OSError, RuntimeError, ValueError, ImportError, KeyError, TypeError, IndexError, StopIteration)


@dataclass(frozen=True)
class Axes:
    x: np.ndarray       # cell centres
    y: np.ndarray       # descending: north first
    x_bnds: np.ndarray  # (nx, 2)
    y_bnds: np.ndarray  # (ny, 2)
    geographic: bool

    @property
    def cell(self) -> float:
        return float(abs(self.x_bnds[0, 1] - self.x_bnds[0, 0]))

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.y), len(self.x)


def _edges(centres):
    half = np.diff(centres).mean() / 2 if len(centres) > 1 else 0.5
    return np.stack([centres - half, centres + half], axis=1)


def axes(path) -> Axes:
    """L1 axes from easting/northing, lon/lat (v5.13) or x/y (v6) and their bounds (F7)."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        names = next((pair for pair in (("easting", "northing"), ("lon", "lat"), ("x", "y"))
                      if all(name in handle.dimensions and name in handle.variables for name in pair)), None)
        if names is None:
            raise ValueError(f"{Path(path).name} has no easting/northing, lon/lat or x/y axes.")
        x, y = (np.asarray(handle[name][:], dtype="f8") for name in names)
        x_bnds, y_bnds = (np.asarray(handle[f"{name}_bnds"][:], dtype="f8") if f"{name}_bnds" in handle.variables
                          else _edges(centres) for name, centres in zip(names, (x, y), strict=True))
        geographic = "degree" in getattr(handle[names[0]], "units", "")
    if len(y) > 1 and not (np.diff(y) < 0).all():
        raise ValueError(f"{Path(path).name}: y must be descending (north first).")
    return Axes(x, y, x_bnds, y_bnds, geographic)


def axes_from_cache(root) -> Axes:
    """The scanned L1 axes, from the cache only (no netCDF read: GUI-safe)."""
    from .cache import load_index

    x, y = (np.load(Path(root) / f"{name}.npy") for name in ("x", "y"))
    return Axes(x, y, _edges(x), _edges(y), bool(load_index(root).get("geographic")))


def cell_index(grid: Axes, x, y):
    """(iy, ix) of the L1 cell holding a map point, or None outside the grid."""
    ix = int(np.floor((x - grid.x_bnds.min()) / grid.cell))
    iy = int(np.floor((grid.y_bnds.max() - y) / abs(grid.y_bnds[0, 1] - grid.y_bnds[0, 0])))
    ny, nx = grid.shape
    return (iy, ix) if 0 <= ix < nx and 0 <= iy < ny else None


def cell_area_m2(grid: Axes) -> np.ndarray:
    """Full cell areas: dx·dy, or R²·Δλ·(sin φ₂ - sin φ₁) on the sphere."""
    if grid.geographic:
        width = np.radians(np.abs(np.diff(grid.x_bnds, axis=1)[:, 0]))
        height = np.abs(np.diff(np.sin(np.radians(grid.y_bnds)), axis=1)[:, 0])
        return EARTH_RADIUS_M ** 2 * height[:, None] * width[None, :]
    return np.abs(np.diff(grid.y_bnds, axis=1)) * np.abs(np.diff(grid.x_bnds, axis=1)).T


def read_l0(path, variable=None, scene=0):
    """An L0 raster as (masked array, x centres, y centres): netCDF with 1-D axes, or GDAL."""
    path = Path(path)
    if path.suffix == ".nc":
        import netCDF4

        with netCDF4.Dataset(path) as handle:
            names = next(pair for pair in (("x", "y"), ("lon", "lat"), ("easting", "northing"))
                         if all(name in handle.variables and handle[name].ndim == 1 for name in pair))
            variable = variable or next(name for name, var in handle.variables.items()
                                        if var.dimensions[-2:] == names[::-1])
            data = handle[variable]
            data = data[scene] if data.ndim == 3 else data[:]
            return np.ma.masked_invalid(data), *(np.asarray(handle[name][:], "f8") for name in names)
    from osgeo import gdal

    handle = gdal.Open(str(path))
    if handle is None:
        raise OSError(f"Cannot open {path}.")
    band = handle.GetRasterBand(1)
    data, nodata = band.ReadAsArray(), band.GetNoDataValue()
    x0, dx, _, y0, _, dy = handle.GetGeoTransform()
    x = x0 + (np.arange(handle.RasterXSize) + 0.5) * dx
    y = y0 + (np.arange(handle.RasterYSize) + 0.5) * dy
    return np.ma.masked_equal(data, nodata) if nodata is not None else np.ma.asarray(data), x, y


def basin_weights(grid: Axes, inside, x, y) -> np.ndarray:
    """Fraction of each L1 cell covered by the L0 cells where `inside` is True."""
    size = abs(float(x[1] - x[0]))
    factor = grid.cell / size
    if not np.isclose(factor, round(factor), rtol=1e-6):
        raise ValueError(f"The L0 cell size {size:g} does not divide the L1 cell size {grid.cell:g}.")
    rows, cols = np.nonzero(np.ma.filled(inside, False))
    ny, nx = grid.shape
    ix = np.floor((x[cols] - grid.x_bnds.min()) / grid.cell).astype(int)
    iy = np.floor((grid.y_bnds.max() - y[rows]) / grid.cell).astype(int)
    keep = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    counts = np.bincount(iy[keep] * nx + ix[keep], minlength=ny * nx).reshape(ny, nx)
    return (counts / round(factor) ** 2).astype("f8")


def restart_field(run, name, scene=None):
    """A field of the run's restart file (F14), as stored; None when not written."""
    if not run.exists("restart"):
        return None
    import netCDF4

    with netCDF4.Dataset(run.files["restart"]) as handle:
        if name not in handle.variables:
            return None
        data = handle[name][:] if scene is None else handle[name][scene]
    return np.ma.filled(np.ma.asarray(data, dtype="f8"), np.nan)


def _l0_source(run, what, scene=0):
    """(path, variable, scene) of the domain's DEM or a land-cover scene."""
    values, index, setup = run.values, run.ref.index, run.ref.setup
    if run.version == "v6":
        key = {"dem": "config_input.dem", "land_cover": "config_mpr.land_cover"}[what]
        return setup / value(values, f"{key}_path", index), value(values, f"{key}_var", index), scene
    folder = setup / (value(values, "directories_general.dir_" + ("Morpho" if what == "dem" else "LCover"), index) or "")
    name = "dem.asc" if what == "dem" else values["lcover"]["lcoverfname"][scene]
    return folder / name, None, 0


def valid_cells(path) -> np.ndarray:
    """The cells holding data in the first step of the file's first gridded variable."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        name = next(name for name, var in handle.variables.items() if var.ndim == 3 and var.dimensions[0] == "time")
        return ~np.ma.getmaskarray(handle[name][0])


def scene_index(run, bounds) -> np.ndarray:
    """The land-cover scene of each output interval (by its start), 0 when unknown.

    v5.13 lists the scenes' years in `&LCover`; a v6 land-cover file with a time axis
    gives one scene per time step."""
    starts = bounds[:, 0]
    try:
        if run.version == "v5.13":
            first = np.array(run.values["lcover"]["lcoveryearstart"], dtype=int)
            years = starts.astype("datetime64[Y]").astype(int) + 1970
            return np.clip(np.searchsorted(first, years, "right") - 1, 0, len(first) - 1)
        import netCDF4

        from .catalog import read_bounds

        path, variable, _ = _l0_source(run, "land_cover")
        with netCDF4.Dataset(path) as handle:
            if handle[variable].ndim < 3:
                return np.zeros(len(starts), int)
            axis = handle[variable].dimensions[0]
        scenes = read_bounds(path, axis)[:, 0]
        return np.clip(np.searchsorted(scenes, starts, "right") - 1, 0, len(scenes) - 1)
    except _UNREADABLE:
        return np.zeros(len(starts), int)


def domain_weights(run, grid: Axes):
    """(weights, source): mHM's own cell areas from the restart, the L0 DEM, or the
    valid output cells (whole cells, approximate at the edges)."""
    area = restart_field(run, "L1_domain_cellarea")
    if area is not None and area.shape == grid.shape:
        return np.where(area > 0, area * 1e6 / cell_area_m2(grid), 0.0), "restart"
    try:
        dem, x, y = read_l0(*_l0_source(run, "dem"))
        return basin_weights(grid, ~np.ma.getmaskarray(dem), x, y), "dem"
    except _UNREADABLE:
        return valid_cells(run.files["mhm"]).astype("f8"), "output"


def gauge_weights(run, grid: Axes):
    """({gauge id: weights}, problems) from the delineated catchment masks."""
    from ..handlers.state import domain_state

    state = domain_state.load_state(run.ref.domain)
    weights, problems = {}, []
    for record in domain_state.gauge_records(state):
        outlet = state["outlets"][record["outlet_id"]]
        try:
            mask, x, y = read_l0(domain_state.resolve_output_path(run.ref.domain, outlet["mask_path"]))
            weights[record["gauge_id"]] = basin_weights(grid, mask == int(outlet.get("mask_value", 1)), x, y)
        except _UNREADABLE as error:
            problems.append(f"Gauge {record['gauge_id']}: no catchment mask ({error}).")
    return weights, problems


def sealed_fraction(run, grid: Axes, scene=0):
    """(fSealed, source) for one land-cover scene (F8, F14): mHM's L1_fSealed from the
    restart, else frac(L0 land cover == 2) * fracSealed_cityArea, else (None, "")."""
    sealed = restart_field(run, "L1_fSealed", scene)
    if sealed is not None and sealed.shape == grid.shape:
        return sealed, "restart"
    try:
        cover, x, y = read_l0(*_l0_source(run, "land_cover", scene))
    except _UNREADABLE:
        return None, ""
    valid = basin_weights(grid, ~np.ma.getmaskarray(cover), x, y)
    urban = basin_weights(grid, cover == 2, x, y)
    key = "lcover_mpr.fracSealed_cityArea" if run.version == "v5.13" else "config_mpr.fracSealed_cityArea"
    city = float(value(run.values, key, run.ref.index) or FRAC_SEALED_DEFAULT)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(valid > 0, urban / valid, np.nan) * city, "land cover"
