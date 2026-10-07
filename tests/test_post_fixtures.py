"""The synthetic mHM runs match real output structure and reproduce their truth."""
from pathlib import Path

import numpy as np
import pytest
from post_fixtures import FILL, STATES, make_run

netCDF4 = pytest.importorskip("netCDF4")
REAL_V5 = Path("/home/bashyal/Works/mhm-debug/test_domain/output_b1/mHM_Fluxes_States.nc")


def _structure(path):
    with netCDF4.Dataset(path) as handle:
        dims = {name: dim.isunlimited() for name, dim in handle.dimensions.items()}
        variables = {}
        for name, var in handle.variables.items():
            if var.ndim == 3:
                filters = var.filters()
                variables[name] = (var.dimensions, var.chunking(), filters["zlib"], filters["shuffle"],
                                   filters["complevel"], float(var._FillValue), float(var.missing_value))
        return dims, variables, handle["time"].dtype, handle["time"].bounds


@pytest.mark.parametrize(("version", "geographic", "xy"), [
    ("5.13", False, ("easting", "northing")), ("5.13", True, ("lon", "lat")), ("6", False, ("x", "y"))])
def test_fixture_has_the_real_layout(tmp_path, version, geographic, xy):
    truth = make_run(tmp_path, version=version, geographic=geographic, shape=(4, 6))
    dims, variables, time_dtype, bounds = _structure(truth.files["mhm"])
    assert dims["time"] is True
    assert bounds == "time_bnds"
    assert time_dtype == np.int32
    for spec in variables.values():
        assert spec == (("time", xy[1], xy[0]), [1, 4, 6], True, True, 6, FILL, FILL)
    with netCDF4.Dataset(truth.files["mhm"]) as handle:
        y = handle[xy[1]][:]
        assert y[0] > y[-1]  # north first
        expected_dtype = np.float32 if version == "6" else np.float64
        assert handle["aET"].dtype == expected_dtype
        assert handle["SM_L01"].units == ("1" if version == "6" else "mm mm-1")
        assert handle["time"].units.startswith("days since" if version == "6" else "hours since")


@pytest.mark.skipif(not REAL_V5.is_file(), reason="mhm-debug sample output not present")
def test_fixture_matches_a_real_v513_file_variable_by_variable(tmp_path):
    truth = make_run(tmp_path, shape=(9, 6))
    real_dims, real_vars, real_time, _ = _structure(REAL_V5)
    dims, variables, time_dtype, _ = _structure(truth.files["mhm"])
    assert dims == {k: v for k, v in real_dims.items() if k in dims}
    assert time_dtype == real_time
    for name, spec in variables.items():
        assert spec == real_vars[name], name


def test_values_reproduce_the_truth_and_the_balance_closes(tmp_path):
    truth = make_run(tmp_path, step="month", days=90, stamp="start")
    with netCDF4.Dataset(truth.files["mhm"]) as handle:
        aet = handle["aET"][:]
        snow = handle["snowpack"][:]
        bounds = handle["time_bnds"][:]
        assert handle["time"][0] == bounds[0, 0]  # start stamp
    (a, b), mask = truth.intervals[1], truth.mask
    np.testing.assert_allclose(aet[1][mask], truth.et[a:b].sum(axis=0)[mask])
    np.testing.assert_allclose(snow[1][mask], STATES["snowpack"] * truth.storage[a + 1:b + 1].mean(axis=0)[mask])
    assert np.ma.getmaskarray(aet[0])[0, -1]  # fill outside the domain
    assert bounds[1].tolist() == [a * 24, b * 24]
    change = truth.storage[1:] - truth.storage[:-1]
    np.testing.assert_allclose(change, truth.p - truth.et - truth.q - truth.loss)
    assert truth.loss.sum() > 0  # the residual a closed balance must show


def test_gauges_forcing_and_restart(tmp_path):
    truth = make_run(tmp_path, gauges=(398, 7))
    with netCDF4.Dataset(truth.files["discharge"]) as handle:
        assert not handle.dimensions["time"].isunlimited()
        assert handle["time"].units == "hours since       2000-01-01 00:00:00"
        assert {"Qsim_0000000398", "Qobs_0000000007"} <= set(handle.variables)
        assert np.ma.getmaskarray(handle["Qobs_0000000398"][:])[0]
    with netCDF4.Dataset(truth.files["pre"]) as handle:
        assert handle["pre"].shape == (62, 2, 3)  # L2 = 2 x L1
    with netCDF4.Dataset(truth.files["restart"]) as handle:
        assert handle["L1_fSealed"].dimensions == ("L1_LandCoverPeriods", "ncols1", "nrows1")
        np.testing.assert_allclose(handle["L1_fSealed"][0][truth.mask], truth.f_sealed[truth.mask])
    log = (tmp_path / "output" / "ConfigFile.log").read_text()
    assert f"{truth.cell_area_km2[truth.mask].sum():.3f}" in log


def test_geographic_cells_shrink_towards_the_pole(tmp_path):
    area = make_run(tmp_path, geographic=True).cell_area_km2
    assert area[0, 0] < area[-1, 0]  # row 0 is the northernmost
    assert area[0, 0] == pytest.approx((6371.0088 * np.radians(1 / 120)) ** 2 * np.cos(np.radians(28.5 - 1 / 240)),
                                       rel=1e-6)
