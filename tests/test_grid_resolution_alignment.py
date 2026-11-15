"""Focused checks for the shared L0/L2 extent contract."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.grid import (
    Extent,
    Grid,
    Resolution,
    aligned_l0_l2_headers,
    axes_match_header,
    header_bounds,
    header_for_aligned_bounds,
    header_for_existing_bounds,
    header_for_model_level,
    l0_header_from_l2,
    validate_grid_headers,
    validate_l0_l2_alignment,
)

L0_ANCHOR = {
    "xllcorner": 100.0,
    "yllcorner": 200.0,
    "cellsize": 10.0,
    "unit": "m",
}


def test_resolution_and_grid_keep_exact_values_and_level_extents():
    resolutions = Resolution(l0=10, l1=20, l2=30)
    assert resolutions.l11 == 20
    assert resolutions.l2_resolution == 30

    grid = Grid.for_meteorology(
        {**L0_ANCHOR, "ncols": 100, "nrows": 100},
        (115, 985, 218, 812),
        3,
        crs="EPSG:32632",
        unit="m",
        raw_l2_resolution=(30, 30),
        raw_l2_extent=Extent(100, 1000, 200, 830),
        raw_l2_crs="EPSG:32632",
    )

    assert grid.l0 == 10
    assert grid.l2 == 30
    assert grid.l0_extent == Extent(100, 1100, 200, 1200)
    assert grid.l2_extent == grid.common_extent == Extent(100, 1000, 200, 830)  # whole L2 cells covering it
    assert not grid.requires_l2_resampling


def test_user_multiplier_remains_authoritative_over_exact_raw_factor():
    grid = Grid.for_meteorology(
        {**L0_ANCHOR, "ncols": 100, "nrows": 100},
        (115, 985, 218, 812),
        4,
        crs="EPSG:32632",
        unit="m",
        raw_l2_resolution=(30, 30),
        raw_l2_extent=Extent(100, 1000, 200, 830),
        raw_l2_crs="EPSG:32632",
    )

    assert grid.raw_l2_factor == 3
    assert grid.multiplier == 4
    assert grid.l2 == 40
    assert grid.requires_l2_resampling


def test_a_gap_below_half_an_l0_cell_shifts_and_another_crs_resamples():
    base = {
        "l0_header": {**L0_ANCHOR, "ncols": 100, "nrows": 100},
        "bounds": (115, 985, 218, 812),
        "multiplier": 3,
        "crs": "EPSG:32632",
        "unit": "m",
        "raw_l2_resolution": (30, 30),
    }
    shifted = Grid.for_meteorology(**base, raw_l2_extent=Extent(100.3, 1000.3, 200, 830), raw_l2_crs="EPSG:32632")
    other = Grid.for_meteorology(**base, raw_l2_extent=Extent(100.3, 1000.3, 200, 830), raw_l2_crs="EPSG:4326")

    assert shifted.alignment_gap == pytest.approx((-0.3, 0))
    assert not shifted.requires_l2_resampling
    assert other.requires_l2_resampling


def test_the_common_extent_takes_the_precipitation_cells_around_it():
    grid = Grid.for_meteorology(
        {**L0_ANCHOR, "ncols": 100, "nrows": 100},
        (415, 585, 418, 512),
        3,
        crs="EPSG:32632",
        unit="m",
        raw_l2_resolution=(30, 30),
        raw_l2_extent=Extent(100, 1000, 200, 830),
        raw_l2_crs="EPSG:32632",
    )

    assert grid.common_extent == Extent(400, 610, 410, 530)  # whole 30 m precipitation cells around it
    assert grid.l0_header["ncols"] == 3 * grid.l2_header["ncols"]
    assert grid.l0_header["xllcorner"] == 400
    assert grid.l0_header["cellsize"] == 10


def test_blocks_of_the_lcm_snap_outward_so_every_level_nests():
    grid = Grid.for_meteorology({**L0_ANCHOR, "ncols": 100, "nrows": 100}, (115, 985, 218, 812), 3, block=6,
                                crs="EPSG:32632", unit="m")

    assert grid.common_extent == Extent(100, 1000, 200, 860)  # 60 m blocks from the L0 corner, around it
    assert (grid.l2_header["ncols"], grid.l0_header["ncols"]) == (30, 90)
    with pytest.raises(ValueError, match="not a multiple"):
        Grid.for_meteorology({**L0_ANCHOR, "ncols": 100, "nrows": 100}, (115, 985, 218, 812), 3, block=4)


def test_extents_intersect_or_share_nothing():
    assert Extent(0, 10, 0, 10).intersect(Extent(5, 15, 5, 15)) == Extent(5, 10, 5, 10)
    assert Extent(0, 10, 0, 10).intersect(Extent(10, 15, 0, 10)) is None


def test_mhm_rules_on_the_four_levels():
    from mhm_qgis.core.grid import validate_mhm_grid

    def headers(l1, l11, l2):
        return {level: {"cellsize": size} for level, size in (("L0", 1.0), ("L1", l1), ("L11", l11), ("L2", l2))}

    validate_mhm_grid(headers(2, 6, 4), "6.0")  # v6 takes any nested L11/L1 ratio
    with pytest.raises(ValueError, match="even L11/L1 ratio"):
        validate_mhm_grid(headers(2, 6, 4), "5.13")
    validate_mhm_grid(headers(2, 4, 4), "5.13")
    with pytest.raises(ValueError, match=r"L1 .* whole multiple of L0"):
        validate_mhm_grid(headers(0.5, 4, 4), "6.0")
    with pytest.raises(ValueError, match="must nest"):
        validate_mhm_grid(headers(2, 3, 4), "6.0")


def test_inner_bounds_drop_the_curved_corners_of_a_transformed_box():
    pytest.importorskip("pyproj")
    from pyproj import Transformer

    from mhm_qgis.core.grid import inner_bounds

    box = (10.0, 12.0, 47.0, 50.0)
    inner = inner_bounds(box, "EPSG:4326", "EPSG:32632")
    outer = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True).transform_bounds(10, 47, 12, 50)
    assert outer[0] < inner[0] < inner[1] < outer[2]
    assert outer[1] < inner[2] < inner[3] < outer[3]
    assert inner_bounds(box, "EPSG:4326", "EPSG:4326") == box


def test_aligned_extent_is_minimal_and_has_integer_l0_l2_dimensions():
    l2 = header_for_aligned_bounds(
        bounds=(115.0, 985.0, 218.0, 812.0),
        cellsize=30.0,
        anchor_x=100.0,
        anchor_y=200.0,
        unit="m",
    )
    l0 = header_for_existing_bounds(l2, cellsize=10.0, unit="m")

    assert l2 == {
        "ncols": 30,
        "nrows": 21,
        "xllcorner": 100.0,
        "yllcorner": 200.0,
        "cellsize": 30.0,
        "nodata_value": -9999.0,
        "unit": "m",
        "cellsize_precision": 8,
    }
    assert l0["ncols"] == 3 * l2["ncols"]
    assert l0["nrows"] == 3 * l2["nrows"]


def test_paired_headers_expand_outward_and_are_exact_by_construction():
    l0, l2 = aligned_l0_l2_headers(
        bounds=(115.0, 985.0, 218.0, 812.0),
        l0_header={
            "xllcorner": 100.0,
            "yllcorner": 200.0,
            "cellsize": 10.0,
            "unit": "m",
        },
        multiplier=3,
        unit="m",
    )

    assert l2["xllcorner"] == 100.0
    assert l2["yllcorner"] == 200.0
    assert l2["cellsize"] == 30.0
    assert l0["ncols"] == l2["ncols"] * 3
    assert l0["nrows"] == l2["nrows"] * 3
    validate_l0_l2_alignment(l0, l2, 3)


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        # Exactly on the L2 grid: never expanded.
        ((100.0, 190.0, 200.0, 290.0), (100.0, 190.0, 200.0, 290.0)),
        # One unit past each boundary, in turn: expanded outward only there.
        ((99.0, 190.0, 200.0, 290.0), (70.0, 190.0, 200.0, 290.0)),
        ((100.0, 191.0, 200.0, 290.0), (100.0, 220.0, 200.0, 290.0)),
        ((100.0, 190.0, 199.0, 290.0), (100.0, 190.0, 170.0, 290.0)),
        ((100.0, 190.0, 200.0, 291.0), (100.0, 190.0, 200.0, 320.0)),
        # Past every boundary at once.
        ((99.0, 191.0, 199.0, 291.0), (70.0, 220.0, 170.0, 320.0)),
    ],
)
def test_misaligned_bounds_only_expand_outward(bounds, expected):
    l0, l2 = aligned_l0_l2_headers(bounds, L0_ANCHOR, multiplier=3, unit="m")

    assert header_bounds(l2) == expected
    assert header_bounds(l0) == expected
    assert l0["ncols"] == l2["ncols"] * 3
    assert l0["nrows"] == l2["nrows"] * 3
    validate_l0_l2_alignment(l0, l2, 3)

    # Every L2 boundary is also an original L0 cell boundary.
    xmin, xmax, ymin, ymax = header_bounds(l2)
    for value, anchor in ((xmin, 100.0), (xmax, 100.0), (ymin, 200.0), (ymax, 200.0)):
        assert (value - anchor) % L0_ANCHOR["cellsize"] == 0.0


def test_saved_l2_header_rederives_the_same_l0_header():
    l0, l2 = aligned_l0_l2_headers(
        (99.0, 191.0, 199.0, 291.0), L0_ANCHOR, multiplier=3, unit="m"
    )

    assert l0_header_from_l2(l2, L0_ANCHOR["cellsize"], 3) == l0


def test_model_levels_use_integer_factors_without_rounding_dimensions():
    l2 = {
        "ncols": 12,
        "nrows": 8,
        "xllcorner": 100.0,
        "yllcorner": 200.0,
        "cellsize": 30.0,
        "unit": "m",
    }
    l0 = header_for_model_level(l2, 10.0, 10.0, "L0")
    l1 = header_for_model_level(l2, 10.000009, 10.0, "L1")
    l11 = header_for_model_level(l2, 60.0, 10.0, "L11")

    assert (l1["cellsize"], l1["nrows"], l1["ncols"]) == (10.0, 24, 36)
    assert (l11["cellsize"], l11["nrows"], l11["ncols"]) == (60.0, 4, 6)
    _headers, ratios = validate_grid_headers(
        {"L0": l0, "L1": l1, "L11": l11, "L2": l2}
    )
    assert ratios == {"L1_to_L2": 3, "L11_to_L2": 2, "L0_to_L1": 1}

    with pytest.raises(ValueError, match="L1/L0"):
        header_for_model_level(l2, 10.000011, 10.0, "L1")
    with pytest.raises(ValueError, match="L1/L2 or L2/L1"):
        header_for_model_level(l2, 40.0, 10.0, "L1")
    with pytest.raises(ValueError, match="not divisible"):
        header_for_model_level({**l2, "ncols": 11}, 60.0, 10.0, "L1")


def test_l0_header_from_l2_rejects_a_non_integer_multiple():
    _l0, l2 = aligned_l0_l2_headers(
        (99.0, 191.0, 199.0, 291.0), L0_ANCHOR, multiplier=3, unit="m"
    )

    with pytest.raises(ValueError, match="not an exact multiple of L0"):
        l0_header_from_l2(l2, 7.0, 3)


def test_axes_match_header_accepts_aligned_axes_and_rejects_shifted_ones():
    header = {
        "ncols": 4,
        "nrows": 3,
        "xllcorner": 100.0,
        "yllcorner": 200.0,
        "cellsize": 10.0,
    }
    aligned_x = [105.0, 115.0, 125.0, 135.0]
    aligned_y = [225.0, 215.0, 205.0]

    assert axes_match_header(aligned_x, aligned_y, header)
    # A subset of the same grid is still aligned; padding handles the rest.
    assert axes_match_header(aligned_x[1:3], aligned_y[:2], header)
    # Half-cell shift and wrong spacing are both rejected.
    assert not axes_match_header([value + 5.0 for value in aligned_x], aligned_y, header)
    assert not axes_match_header([100.0, 120.0, 140.0], aligned_y, header)


# The A26 case: a 1/1200 degree DEM with a 120x L2 multiplier. The cell size
# derived from the raster extent is one ulp below 1/1200, so 120x it is
# 0.09999999999999999 rather than 0.1. Flooring that to eight decimals yields
# 0.09999999 -- a 1e-8 error, ten times the alignment tolerance.
DEGREE_L0 = 0.0008333333333333333
assert 120 * DEGREE_L0 == 0.09999999999999999
DEGREE_ANCHOR = {
    "xllcorner": 78.99958333333333,
    "yllcorner": 25.999583333333334,
    "cellsize": DEGREE_L0,
    "unit": "deg",
}


def test_repeating_degree_l2_cell_size_is_never_rounded_away():
    from mhm_qgis.core.grid import floor_cellsize

    bounds = (78.99958333333333, 90.00041666666226, 26.0, 31.000416666666666)
    l0, l2 = aligned_l0_l2_headers(bounds, DEGREE_ANCHOR, multiplier=120, unit="deg")

    assert l0["cellsize"] == DEGREE_L0
    assert l2["cellsize"] == 120 * DEGREE_L0
    # This is the value that used to be truncated.
    assert floor_cellsize(l2["cellsize"], "deg") != l2["cellsize"]

    # Every downstream consumer must accept the exact pair.
    validate_l0_l2_alignment(l0, l2, 120)
    assert l0_header_from_l2(l2, DEGREE_L0, 120) == l0


def test_all_four_levels_validate_on_a_repeating_degree_grid(tmp_path):
    from mhm_qgis.core.grid import (
        header_for_existing_bounds,
        read_header_file,
        write_header_file,
    )
    from mhm_qgis.core.handlers.file.ascii.morphology import validate_grid_headers

    bounds = (78.99958333333333, 90.00041666666226, 26.0, 31.000416666666666)
    l0, l2 = aligned_l0_l2_headers(bounds, DEGREE_ANCHOR, multiplier=120, unit="deg")
    l1 = header_for_existing_bounds(l2, 60 * DEGREE_L0, "deg")

    _standardized, ratios = validate_grid_headers(
        {"L0": l0, "L1": l1, "L11": l1, "L2": l2}
    )
    assert ratios["L0_to_L1"] * ratios["L1_to_L2"] == 120

    # A written header must read back bit-for-bit, or the meteorology reuse
    # check rebuilds every variable on every run.
    path = tmp_path / "header.txt"
    write_header_file(path, l2)
    assert read_header_file(path, unit="deg")["cellsize"] == l2["cellsize"]


def test_raster_grid_upscales_from_the_corner_and_reads_mhm_netcdf_as_the_forcing_does(tmp_path):
    pytest.importorskip("osgeo")
    np = pytest.importorskip("numpy")
    xr = pytest.importorskip("xarray")
    from mhm_qgis.core.grid.raster import raster_grid

    asc = tmp_path / "dem.asc"
    asc.write_text("ncols 4\nnrows 2\nxllcorner 100\nyllcorner 200\ncellsize 10\nNODATA_value -9999\n" + "1 2 3 4\n" * 2)
    header = raster_grid(asc, "EPSG:32632", "EPSG:32632", "m", factor=3)["header"]
    assert (header["ncols"], header["nrows"], header["xllcorner"], header["yllcorner"], header["cellsize"]) == (
        2, 1, 100, 200, 30)  # the top and right padded to whole blocks
    nc = tmp_path / "pre.nc"  # mHM's layout: xc/yc are the left/upper cell edges, no CF grid for GDAL
    xr.Dataset({"pre": (("time", "yc", "xc"), np.ones((1, 2, 3)))},
               coords={"time": np.array(["2001-01-01"], dtype="datetime64[ns]"), "yc": [230., 220.],
                       "xc": [100., 110., 120.]}).to_netcdf(nc)
    header = raster_grid(f'NETCDF:"{nc}":pre', "EPSG:32632", "EPSG:32632", "m")["header"]
    assert (header["ncols"], header["nrows"], header["xllcorner"], header["yllcorner"]) == (3, 2, 100, 210)


def test_a_netcdf_grid_is_read_from_its_axes_never_through_gdal(tmp_path, monkeypatch):
    gdal = pytest.importorskip("osgeo.gdal")
    np = pytest.importorskip("numpy")
    xr = pytest.importorskip("xarray")
    from mhm_qgis.core.grid.raster import raster_grid

    nc = tmp_path / "tp.nc"
    xr.Dataset({"tp": (("time", "latitude", "longitude"), np.ones((2, 2, 3))), "q": (("time",), [1., 2.])},
               coords={"time": np.array(["2001-01-01", "2001-01-02"], dtype="datetime64[ns]"),
                       "latitude": [50.95, 50.85], "longitude": [9.05, 9.15, 9.25]}).to_netcdf(nc)
    monkeypatch.setattr(gdal, "Open", lambda *_args: pytest.fail("GDAL builds a band per time step"))
    header = raster_grid(f'NETCDF:"{nc}":tp', "EPSG:4326", "EPSG:4326", "deg")["header"]
    assert (header["ncols"], header["nrows"]) == (3, 2)
    assert header["xllcorner"] == pytest.approx(9.0) and header["yllcorner"] == pytest.approx(50.8)  # noqa: PT018
    assert raster_grid(f'NETCDF:"{nc}":tp', "EPSG:4326", "EPSG:32632", "m")["header"]["cellsize"] > 1000
    with pytest.raises(ValueError, match=r"tp.nc: no grid information .* Select another file"):
        raster_grid(f'NETCDF:"{nc}":q', "EPSG:4326", "EPSG:4326", "deg")


def test_a_window_copy_shifts_a_source_off_the_lattice_only_when_asked():
    from mhm_qgis.core.grid import aligned_window

    target = {"ncols": 10, "nrows": 10, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 10.0}
    source = {**target, "xllcorner": 23.0, "yllcorner": -4.0}  # 2.3 and -0.4 cells off
    with pytest.raises(ValueError, match="not aligned"):
        aligned_window(source, target)
    window = aligned_window(source, target, shift=True)  # moved by -3 and +4: under half a cell
    assert (window["target_x"], window["source_y"], window["copy_cols"]) == (2, 0, 8)


def test_the_first_netcdf_slice_is_north_first(tmp_path):
    np = pytest.importorskip("numpy")
    xr = pytest.importorskip("xarray")
    from mhm_qgis.core.meteorology.forcing.api import netcdf_first_slice

    values = np.arange(12, dtype="float32").reshape(2, 2, 3)
    xr.Dataset({"tp": (("time", "latitude", "longitude"), values)},
               coords={"time": np.array(["2001-01-01", "2001-01-02"], dtype="datetime64[ns]"),
                       "latitude": [50.0, 51.0], "longitude": [9.0, 10.0, 11.0]}).to_netcdf(tmp_path / "tp.nc")
    assert netcdf_first_slice(tmp_path / "tp.nc", "tp").tolist() == values[0][::-1].tolist()
