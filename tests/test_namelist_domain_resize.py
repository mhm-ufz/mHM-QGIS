"""Legacy domain arrays are adapted without writing on editor cancel."""
from pathlib import Path
import pytest
model = pytest.importorskip("nml_tools.gui.model")
from mhm_qgis.externals.nml_tools_handler import _editor_folder

SCHEMAS = Path(__file__).parents[1] / "src/mhm_qgis/nml-schemas/v6"


@pytest.mark.parametrize("save", [False, True])
def test_legacy_domain_migration_preserves_original_until_editor_save(tmp_path, save):
    project = model.load_project(SCHEMAS, tmp_path)
    model.save_profiles(project, [(project.profile("main"), {
        "config_project": {"n_domains": 2},
        "config_input": {"dem_path": ["data/a/dem.nc", "data/b/dem.nc"]},
    }, {"n_domains": 2})])
    target = tmp_path / "mhm.nml"
    before = target.read_bytes()
    values = {"main": {"config_project": {"n_domains": 1},
                       "config_input": {"dem_path": ["data/morph/dem.nc"]}}}
    with _editor_folder(SCHEMAS, tmp_path, {"main": []}, values, {"n_domains": 1}) as folder:
        assert target.read_bytes() == before
        assert folder != tmp_path
        staged = model.load_project(SCHEMAS, folder, {"main": []})
        assert model.recover_dimensions(staged)["n_domains"] == 1
        if save:
            model.save_profiles(staged, [(staged.profile("main"), values["main"], {"n_domains": 1})])
    if save:
        assert model.recover_dimensions(project)["n_domains"] == 1
        assert target.with_suffix(".nml.before-single-domain").read_bytes() == before
    else:
        assert target.read_bytes() == before
