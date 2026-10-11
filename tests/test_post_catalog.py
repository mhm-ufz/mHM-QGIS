"""Post P1: runs and the run archive, the catalog's registry join, and the L1 grid."""
import json
from pathlib import Path

import numpy as np
import pytest
from post_fixtures import make_run

from mhm_qgis.core.executions import post as post_exec
from mhm_qgis.core.handlers.store.layout import forget_project_version, input_state_path
from mhm_qgis.core.post import catalog, grid, runs

netCDF4 = pytest.importorskip("netCDF4")
RAPTI = Path("/home/bashyal/Works/Rapti/mhm-plugin")
V5 = {"directories_general": {"dir_out": ["output/"], "dirconfigout": "output/",
                              "mhm_file_restartout": ["restart/mHM_restart_001.nc"]},
      "mainconfig": {"write_restart": True, "resolution_hydrology": [2000.0]}}
V6 = {"config_mhm": {"output_path": ["output/mhm_output.nc"]},
      "config_mrm": {"output_path": ["output/mrm_output.nc"], "output_poi_path": ["mrm_poi_output.nc"]},
      "config_mpr": {"write_restart": [True], "restart_output_path": ["restart/mHM_restart_001.nc"]},
      "config_resolution": {"hydro": [2000.0]}}


def _state(folder, **values):
    path = Path(input_state_path(folder))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))
    forget_project_version(folder)


def _current(folder, values=V5):
    return catalog.open_run(runs.list_runs(folder)[0], values)


def test_variables_join_the_data_model(tmp_path):
    make_run(tmp_path)
    with netCDF4.Dataset(tmp_path / "output" / "mHM_Fluxes_States.nc", "a") as handle:
        handle.createVariable("custom", "f8", ("time", "northing", "easting"))
    run = _current(tmp_path)
    by_name = {v.name: v for v in run.variables}
    assert by_name["SWC_L02"].output == "SWC_L"
    assert by_name["SWC_L02"].layer == 2
    assert by_name["SWC_L02"].weight == "pervious"
    assert by_name["aET"].kind == "flux"
    assert by_name["aET"].aggregate == ("sum", "mean")
    assert by_name["aET"].units == "mm d-1"
    assert by_name["custom"].group == "other"
    assert by_name["custom"].aggregate == ("instant", "mean")
    assert not by_name["neutrons"].present
    assert "outputflxstate(18)" in by_name["neutrons"].message
    assert run.step_days == 1
    assert run.period[0] == np.datetime64("2000-01-01")
    assert run.files["discharge"].name == "discharge.nc"
    assert run.log_area_km2() > 0
    assert run.findings() == []


def test_v6_files_come_from_the_namelists(tmp_path):
    make_run(tmp_path, version="6", step="month", days=90)
    ref = runs.RunRef(tmp_path, tmp_path, tmp_path, tmp_path, "v6")
    run = catalog.open_run(ref, V6)
    assert run.files["mhm"] == tmp_path / "output" / "mhm_output.nc"
    routed = next(v for v in run.variables if v.output == "Qrouted")
    assert routed.present
    assert routed.name == "discharge"
    assert routed.file == "mrm"
    np.testing.assert_array_equal(run.bounds[0], np.array(["2000-01-01", "2000-02-01"], "datetime64[s]"))
    assert run.findings() == []


def test_geographic_cells_match_the_sphere():
    edges = np.arange(-180.0, 181.0, 1.0)
    lat = np.arange(90.0, -91.0, -1.0)
    sphere = grid.Axes(edges[:-1] + 0.5, lat[:-1] - 0.5, np.stack([edges[:-1], edges[1:]], 1),
                       np.stack([lat[:-1], lat[1:]], 1), geographic=True)
    total = grid.cell_area_m2(sphere).sum()
    assert total == pytest.approx(4 * np.pi * grid.EARTH_RADIUS_M ** 2, rel=1e-3)


def test_geographic_run_area_and_restart_coverage(tmp_path):
    truth = make_run(tmp_path, geographic=True)
    run = _current(tmp_path, {**V5, "mainconfig": {"write_restart": True, "resolution_hydrology": [1 / 120]}})
    axes = grid.axes(run.files["mhm"])
    np.testing.assert_allclose(grid.cell_area_m2(axes), truth.cell_area_km2 * 1e6, rtol=1e-3)
    weights, source = grid.domain_weights(run, axes)
    assert source == "restart"
    np.testing.assert_allclose(weights, truth.mask, atol=1e-9)
    assert run.findings() == []


def test_basin_weights_are_the_l0_block_mean():
    axes = grid.Axes(np.array([1.0, 3.0]), np.array([3.0, 1.0]), np.array([[0, 2], [2, 4.0]]),
                     np.array([[4, 2], [2, 0.0]]), geographic=False)
    x = y = np.arange(-0.25, 4.5, 0.5)  # L0 at 0.5: wider than the L1 grid, factor 4
    inside = np.zeros((10, 10), bool)
    inside[:, :] = (x[None, :] > 0) & (x[None, :] < 1) & (y[::-1][:, None] > 0)
    weights = grid.basin_weights(axes, inside[::-1], x, y[::-1])
    np.testing.assert_allclose(weights, [[0.5, 0], [0.5, 0]])
    with pytest.raises(ValueError, match="does not divide"):
        grid.basin_weights(axes, inside, np.arange(0, 4, 0.3), y)


def test_sealed_fraction_from_restart_then_land_cover(tmp_path):
    truth = make_run(tmp_path, version="6")
    ref = runs.RunRef(tmp_path, tmp_path, tmp_path, tmp_path, "v6")
    run = catalog.open_run(ref, V6)
    axes = grid.axes(run.files["mhm"])
    sealed, source = grid.sealed_fraction(run, axes)
    assert source == "restart"
    np.testing.assert_allclose(sealed[truth.mask], truth.f_sealed[truth.mask])
    cover = np.where(np.add.outer(np.arange(16), np.arange(24)) % 3 == 0, 2, 5)  # L0 = L1 / 4
    with netCDF4.Dataset(tmp_path / "lc.nc", "w") as handle:
        for name, centres in (("x", 500000.0 + 500 * (np.arange(24) + 0.5)),
                              ("y", 3000000.0 - 500 * (np.arange(16) + 0.5))):
            handle.createDimension(name, len(centres))
            handle.createVariable(name, "f8", (name,))[:] = centres
        handle.createVariable("land_cover", "i4", ("y", "x"))[:] = cover
    values = {**V6, "config_mpr": {"write_restart": [False], "land_cover_path": ["lc.nc"],
                                   "land_cover_var": ["land_cover"], "fracsealed_cityarea": [0.5]}}
    sealed, source = grid.sealed_fraction(catalog.open_run(ref, values), axes)
    expected = (cover == 2).reshape(4, 4, 6, 4).mean(axis=(1, 3)) * 0.5
    assert source == "land cover"
    np.testing.assert_allclose(sealed, expected)


def test_archive_moves_the_run_atomically(tmp_path):
    make_run(tmp_path)
    source = tmp_path / "output" / "mHM_Fluxes_States.nc"
    inode = source.stat().st_ino
    (tmp_path / "mhm.nml").write_text("&mainconfig\n/\n")
    expected = _current(tmp_path).bounds
    [ref] = runs.archive(tmp_path, "baseline", note="before calibration", values=V5)
    moved = tmp_path / "runs" / "baseline" / "output" / "mHM_Fluxes_States.nc"
    assert moved.stat().st_ino == inode  # moved, not copied
    assert (tmp_path / "output").is_dir()
    assert not any((tmp_path / "output").iterdir())
    assert (tmp_path / "restart" / "mHM_restart_001.nc").is_file()  # the restart is copied
    assert (tmp_path / "runs" / "baseline" / "restart" / "mHM_restart_001.nc").is_file()
    assert (tmp_path / "runs" / "baseline" / "namelists" / "mhm.nml").is_file()
    meta = runs.run_meta(ref)
    assert meta["note"] == "before calibration"
    assert meta["period"][0].startswith("2000-01-01")
    archived = catalog.open_run(ref, V5)
    np.testing.assert_array_equal(archived.bounds, expected)
    assert runs.list_runs(tmp_path) == [ref]  # output/ is empty now
    for name, message in (("baseline", "already archived"), ("a b", "letters"), ("next", "no run")):
        with pytest.raises(ValueError, match=message):
            runs.archive(tmp_path, name, values=V5)


def test_v6_archive_takes_outputs_written_outside_output(tmp_path):
    make_run(tmp_path, version="6")
    _state(tmp_path, mhm_version="6")
    (tmp_path / "mrm_poi_output.nc").write_bytes(b"poi")
    [ref] = runs.archive(tmp_path, "r1", values=V6)
    assert (tmp_path / "runs" / "r1" / "mrm_poi_output.nc").read_bytes() == b"poi"
    assert not (tmp_path / "mrm_poi_output.nc").exists()
    assert catalog.open_run(ref, V6).files["discharge"] == tmp_path / "runs" / "r1" / "mrm_poi_output.nc"


def test_v5_multi_lists_and_archives_each_domain(tmp_path):
    root = tmp_path / "multi"
    _state(root, mhm_version="5.13", setup="multi")
    from mhm_qgis.core.handlers.state.nml_settings import update_section

    update_section(root, "multi_domain", {"domains": ["../a", "../b"]})
    values = {"directories_general": {"dir_out": ["output/a/", "output/b/"]}}
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        make_run(tmp_path / f"run_{name}")
        (root / "output").mkdir(parents=True, exist_ok=True)
        (tmp_path / f"run_{name}" / "output").rename(root / "output" / name)
    refs = runs.list_runs(root)
    assert [(r.index, r.domain.name, r.label) for r in refs] == [
        (1, "a", "Current (output/) · a"), (2, "b", "Current (output/) · b")]
    assert catalog.open_run(refs[1], values).files["mhm"] == root / "output" / "b" / "mHM_Fluxes_States.nc"
    runs.archive(root, "two", values=values)
    assert sorted(p.name for p in (root / "output").iterdir()) == ["a", "b"]  # dir_Out kept
    assert [r.domain.name for r in runs.list_runs(root)] == ["a", "b"]


@pytest.mark.skipif(not (RAPTI / "output" / "mHM_Fluxes_States.nc").is_file(), reason="Rapti run not present")
def test_rapti_run_is_sound():
    pytest.importorskip("osgeo")
    ref = runs.RunRef(RAPTI, RAPTI, RAPTI, RAPTI, "v5.13")
    run = catalog.open_run(ref)
    assert grid.domain_weights(run, grid.axes(run.files["mhm"]))[1] == "dem"
    assert run.findings() == []


def test_a_named_run_writes_into_its_folder_and_is_listed(tmp_path):
    """The namelists point a named run's outputs into runs/<name>/; afterwards it is
    listed by name and its files resolve from the setup folder."""
    make_run(tmp_path)
    nml = tmp_path / "mhm.nml"
    nml.write_text('&directories_general\n  dirConfigOut = "output/"\n  dir_Out(1) = "output/"\n'
                   '  dir_Total_Runoff(1) = "output/"\n  mhm_file_RestartOut(1) = "restart/r.nc"\n/\n')
    made = runs.link_outputs(tmp_path, "case1")
    text = nml.read_text()
    assert 'dir_Out(1) = "runs/case1/output/"' in text
    assert 'dirConfigOut = "runs/case1/output/"' in text
    assert 'dir_Total_Runoff(1) = "output/"' in text  # an input
    assert 'mhm_file_RestartOut(1) = "restart/r.nc"' in text
    assert (tmp_path / "runs" / "case1" / "output") in made
    (tmp_path / "output").rename(tmp_path / "runs" / "case1" / "output")  # as mHM would have written it
    [ref] = runs.finish_named(tmp_path, "case1")
    assert ref.label == f"case1 · {tmp_path.name}"
    assert ref.root == tmp_path
    assert runs.run_meta(ref)["linked"] is True
    values = {**V5, "directories_general": {**V5["directories_general"], "dir_out": ["runs/case1/output/"]}}
    assert catalog.open_run(ref, values).files["mhm"] == tmp_path / "runs" / "case1" / "output" / "mHM_Fluxes_States.nc"
    make_run(tmp_path)  # an older unnamed run in output/, the setup's namelists still linked to case1
    current = next(ref for ref in runs.list_runs(tmp_path) if not ref.name)
    assert catalog.open_run(current, values).files["mhm"] == tmp_path / "output" / "mHM_Fluxes_States.nc"
    runs.link_outputs(tmp_path, "")  # an unnamed run writes to output/ again
    assert 'dir_Out(1) = "output/"' in nml.read_text()
    with pytest.raises(ValueError, match="letters"):
        runs.link_outputs(tmp_path, "a b")


def test_a_run_that_wrote_no_output_variable_says_so(tmp_path):
    """Every out_* switch off: mHM writes only the grid, without a time axis."""
    truth = make_run(tmp_path, version="6")
    _state(tmp_path, mhm_version="6")
    with netCDF4.Dataset(truth.files["mhm"], "w") as handle:  # what mHM v6 then writes
        handle.createDimension("x", 3)
        handle.createDimension("y", 2)
        handle.createVariable("x", "f8", ("x",))[:] = [0.5, 1.5, 2.5]
        handle.createVariable("y", "f8", ("y",))[:] = [1.5, 0.5]
    [ref] = runs.list_runs(tmp_path)
    run = catalog.open_run(ref, V6)
    [finding] = run.findings()
    assert "no time axis" in finding
    with pytest.raises(ValueError, match="Turn on outputs"):
        post_exec.scan_job(ref, run=run)
