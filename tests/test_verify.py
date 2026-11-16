"""core.verify: mHM's input checks, reproduced before a run."""
from types import SimpleNamespace

import numpy as np

from mhm_qgis.core import verify
from mhm_qgis.core.grid import floor_cellsize
from mhm_qgis.core.verify import rules
from mhm_qgis.vSpecific.common import resolution

# saale-v5: the DEM's cell and the typed L1 = L11 = L2 (120 x L0 in floating point)
SAALE_L0, SAALE_L1 = 0.0008333333333333324, 0.0999999999999999


def test_saale_resolution_reaches_the_namelist_as_a_whole_multiple_of_l0():
    dialog = SimpleNamespace(current_l1_resolution=lambda: SAALE_L1, current_grid_unit=lambda: "deg")
    l1 = resolution(dialog, "current_l1_resolution")

    assert l1 == 0.099999999999
    assert rules.resolutions(False, SAALE_L0, l1, l1, SAALE_L1) == []
    assert rules.resolutions(True, SAALE_L0, l1, l1, SAALE_L1) == []
    # mhm.nml held 0.09999999: 1.2e-5 off a whole ratio, and mHM stopped
    old = floor_cellsize(SAALE_L1, "deg", precision=8)
    assert "Two resolutions size do not confirm" in rules.resolutions(False, SAALE_L0, old)[0]


def test_v5_routing_is_the_hydrology_cell_or_an_even_multiple():
    assert rules.resolutions(False, 100, 1000, 1000) == []
    assert rules.resolutions(False, 100, 1000, 2000) == []
    assert "even multiple" in rules.resolutions(False, 100, 1000, 3000)[0]
    assert rules.resolutions(True, 100, 1000, 3000) == []
    problems = rules.resolutions(True, 100, 1000, 1500)
    assert len(problems) == 1
    assert "do not nest" in problems[0]


def test_classes_need_data_and_known_ids_inside_the_mask():
    mask = np.array([[True, True], [True, False]])
    values = np.array([[1, 2], [np.nan, 9.0]])  # 9 lies outside the mask

    problems = rules.classes("Soil", values, mask, {1, 2})
    assert len(problems) == 1
    assert "1 cells inside the DEM mask hold no data" in problems[0]
    values[1, 0] = 5
    assert "IDs [5]" in rules.classes("Soil", values, mask, {1, 2})[0]
    values[1, 0] = 2
    assert rules.classes("Soil", values, mask, {1, 2}) == []


def test_lai_needs_twelve_months_and_v5_its_range():
    mask = np.ones((2, 2), bool)
    assert "12 slices" in rules.lai(False, np.ones((11, 2, 2)), mask, monthly=True)[0]
    high = np.ones((12, 2, 2))
    high[3, 0, 0] = 31
    assert "[0, 30]" in rules.lai(False, high, mask, monthly=True)[0]
    assert rules.lai(True, high, mask, monthly=True) == []  # v6 clamps


def test_forcing_time_axis_coverage_and_v5_bounds():
    times = np.arange("2000-01-01", "2000-01-11", dtype="datetime64[D]")
    ok = {"units": "days since 2000-01-01", "times": times, "start": "2000-01-01", "end": "2000-01-10"}

    assert rules.forcing(False, "pre", **ok, low=0, high=10) == []
    assert "time units" in rules.forcing(False, "pre", **{**ok, "units": "days since 2000-01-01T00:00:00"})[0]
    assert "not the period" in rules.forcing(False, "pre", **{**ok, "end": "2000-01-11"})[0]
    assert "bounds [0, 2000]" in rules.forcing(False, "pre", **ok, low=-1, high=10)[0]
    assert "no data inside the domain" in rules.forcing(False, "pre", **ok, holes=[4])[0]
    assert rules.forcing(True, "pre", **ok, holes=[4]) == []  # v6 masks by the first step only


def _asc(path, rows, cellsize=100):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"ncols {len(rows[0])}\nnrows {len(rows)}\nxllcorner 0\nyllcorner 0\ncellsize {cellsize}\nNODATA_value -9999\n"
    path.write_text(header + "\n".join(" ".join(map(str, row)) for row in rows) + "\n")


def test_check_reads_a_v5_setup(tmp_path, monkeypatch):
    morph = tmp_path / "data/static/morph"
    _asc(morph / "dem.asc", [[1, 2, 3, 4]] * 4)
    _asc(morph / "geology_class.asc", [[1, 2, 3, 2]] * 4)
    (morph / "geology_classdefinition.txt").write_text(
        "nGeo_Formations 2\nGeoParam(i) ClassUnit Karstic Description\n1 1 0 a\n2 2 0 b\n\nIMPORTANT ::\n")
    (tmp_path / "data/meteo/pre").mkdir(parents=True)
    (tmp_path / "data/meteo/pre/header.txt").write_text("ncols 2\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 200\n")
    nml = {"mainconfig": {"resolution_hydrology": [200.0]}, "mainconfig_mhm_mrm": {"resolution_routing": [200.0]},
           "directories_general": {"dir_morpho": ["data/static/morph/"], "dircommonfiles": "data/static/morph/"},
           "directories_mhm": {"dir_precipitation": ["data/meteo/pre/"]},
           "geoparameter": {"geoparam": [[1, 1000, 100, 1, 1]] * 2}}
    monkeypatch.setattr(verify.datamodel, "read_namelists", lambda _folder: nml)
    monkeypatch.setattr(verify.layout, "is_v6", lambda _folder: False)

    assert verify.check(tmp_path, "grid") == []
    problems = verify.check(tmp_path, "morphology")
    assert len(problems) == 1
    assert "Geology: IDs [3]" in problems[0]
    nml["mainconfig"]["resolution_hydrology"] = [250.0]
    assert "not a whole multiple" in verify.check(tmp_path, "grid")[0]


def test_a_period_set_after_the_namelist_was_saved_is_reported(tmp_path):
    nml = {("time_periods", "eval_per"): {"yStart": 2001, "mStart": 1, "dStart": 1, "yEnd": 2005, "mEnd": 1, "dEnd": 1},
           ("time_periods", "warming_days"): 366}
    setup = SimpleNamespace(v6=False, folder=tmp_path, get=lambda group, key, default=None: nml.get((group, key), default),
                            settings={"time": {"sim_start": "2000-01-01T00:00", "eval_start": "2001-01-01T00:00",
                                               "sim_end": "2005-01-01T00:00"}})
    assert verify._stale(setup) == []
    setup.settings["time"]["sim_end"] = "2003-01-01T00:00"
    assert "Edit Namelists" in verify._stale(setup)[0]
