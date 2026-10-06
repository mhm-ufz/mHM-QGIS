"""Focused checks for the shared L0/L2 extent contract."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.grid import (
    Extent,
    Grid,
    Resolution,
    aligned_l0_l2_headers,
    alignment_gap_limit_fraction,
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
        ((115, 985, 218, 812),),
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
    assert grid.l2_extent == grid.common_extent
    assert not grid.requires_l2_resampling


def test_user_multiplier_remains_authoritative_over_exact_raw_factor():
    grid = Grid.for_meteorology(
        {**L0_ANCHOR, "ncols": 100, "nrows": 100},
        ((115, 985, 218, 812),),
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


def test_alignment_gap_limit_comes_from_settings_and_material_gap_resamples(tmp_path):
    assert alignment_gap_limit_fraction() == pytest.approx(1.0e-7)
    settings = tmp_path / "settings.yaml"
    settings.write_text("grid_alignment_gap_limit: 0.02\n", encoding="utf-8")
    assert alignment_gap_limit_fraction(settings) == 0.02

    base = dict(
        l0_header={**L0_ANCHOR, "ncols": 100, "nrows": 100},
        domain_extents=((115, 985, 218, 812),),
        multiplier=3,
        crs="EPSG:32632",
        unit="m",
        raw_l2_resolution=(30, 30),
        raw_l2_crs="EPSG:32632",
        gap_limit_fraction=0.02,
    )
    within = Grid.for_meteorology(
        **base, raw_l2_extent=Extent(100.1, 1000.1, 200, 830)
    )
    beyond = Grid.for_meteorology(
        **base, raw_l2_extent=Extent(100.3, 1000.3, 200, 830)
    )

    assert within.alignment_gap == pytest.approx((-0.1, 0))
    assert not within.requires_l2_resampling
    assert beyond.alignment_gap == pytest.approx((-0.3, 0))
    assert beyond.requires_l2_resampling


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
    "bounds, expected",
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

    with pytest.raises(ValueError):
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
    from mhm_qgis.core.grid import (
        floor_cellsize,
        possible_resolutions,
    )

    bounds = (78.99958333333333, 90.00041666666226, 26.0, 31.000416666666666)
    l0, l2 = aligned_l0_l2_headers(bounds, DEGREE_ANCHOR, multiplier=120, unit="deg")

    assert l0["cellsize"] == DEGREE_L0
    assert l2["cellsize"] == 120 * DEGREE_L0
    # This is the value that used to be truncated.
    assert floor_cellsize(l2["cellsize"], "deg") != l2["cellsize"]

    # Every downstream consumer must accept the exact pair.
    validate_l0_l2_alignment(l0, l2, 120)
    assert l0_header_from_l2(l2, DEGREE_L0, 120) == l0
    assert possible_resolutions(DEGREE_L0, l2["cellsize"], "deg")


def test_all_four_levels_validate_on_a_repeating_degree_grid(tmp_path):
    from mhm_qgis.core.grid import (
        header_for_existing_bounds,
        possible_resolutions,
        read_header_file,
        write_header_file,
    )
    from mhm_qgis.core.handlers.file.ascii.morphology import validate_grid_headers

    bounds = (78.99958333333333, 90.00041666666226, 26.0, 31.000416666666666)
    l0, l2 = aligned_l0_l2_headers(bounds, DEGREE_ANCHOR, multiplier=120, unit="deg")
    options = possible_resolutions(DEGREE_L0, l2["cellsize"], "deg")
    l1 = header_for_existing_bounds(l2, options[len(options) // 2], "deg")

    _standardized, ratios = validate_grid_headers(
        {"L0": l0, "L1": l1, "L11": l1, "L2": l2}
    )
    assert ratios["L0_to_L1"] * ratios["L1_to_L2"] == 120

    # A written header must read back bit-for-bit, or the meteorology reuse
    # check rebuilds every variable on every run.
    path = tmp_path / "header.txt"
    write_header_file(path, l2)
    assert read_header_file(path, unit="deg")["cellsize"] == l2["cellsize"]
