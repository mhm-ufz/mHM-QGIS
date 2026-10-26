"""Legacy domain arrays are adapted without writing on editor cancel."""
from pathlib import Path

import pytest

model = pytest.importorskip("nml_tools.gui.model")
from nml_tools.gui import GuiProjectRequest  # noqa: E402

from mhm_qgis.externals.nml_tools_handler import (  # noqa: E402
    _editor_folder,
    launch_editor,
)

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
    request = GuiProjectRequest(
        "classic", tmp_path, {"main": []}, values, {"n_domains": 1}
    )
    with _editor_folder(SCHEMAS, request) as folder:
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


def test_v5_camel_case_dimensions_still_shrink_legacy_domains(tmp_path):
    """The plugin names v5 sizes `nDomains`; nml-tools recovers them lower-cased."""
    schemas = SCHEMAS.parent / "v5.13"
    project = model.load_project(schemas, tmp_path)
    model.save_profiles(project, [(project.profile("main"), {
        "mainconfig": {"nDomains": 2, "resolution_Hydrology": [12000.0, 24000.0]},
    }, {"ndomains": 2})])
    values = {"main": {"mainconfig": {"nDomains": 1, "resolution_Hydrology": [12000.0]}}}
    request = GuiProjectRequest("v5", tmp_path, {"main": []}, values, {"nDomains": 1})
    with _editor_folder(schemas, request) as folder:
        assert folder != tmp_path
        staged = model.load_project(schemas, folder, {"main": []})
        assert model.recover_dimensions(staged)["ndomains"] == 1


def test_multi_domain_editor_uses_one_combined_project_request(tmp_path, monkeypatch):
    project = model.load_project(SCHEMAS, tmp_path, "combine")
    model.save_profiles(
        project,
        [
            (
                project.profile("meta"),
                {
                    "config_project": {"n_domains": 2},
                    "config_domain": {"domain_dirs": ["one", "two"]},
                },
                {"n_domains": 2},
            )
        ],
    )
    before = (tmp_path / "combine.nml").read_bytes()
    calls = []
    monkeypatch.setattr(
        "nml_tools.gui.launch_gui",
        lambda *args, **kwargs: calls.append((args, kwargs)) or 0,
    )
    values = {
        "meta": {
            "config_project": {"n_domains": 1},
            "config_domain": {"domain_dirs": ["one"]},
        }
    }
    assert launch_editor(
        SCHEMAS,
        tmp_path,
        "combine",
        namelists={"meta": []},
        initial_values=values,
        initial_dimensions={"n_domains": 1},
    ) == 0
    ((schemas, requests), kwargs) = calls[0]
    assert schemas == SCHEMAS
    assert kwargs == {}
    assert len(requests) == 1
    request = requests[0]
    assert request.project_profile == "combine"
    assert request.output_dir != tmp_path
    assert request.output_dir.parent == tmp_path
    assert request.file_profiles == {"meta": []}
    assert request.initial_values == values
    assert request.initial_dimensions == {"n_domains": 1}
    assert (tmp_path / "combine.nml").read_bytes() == before
