"""Domain identity, not list position, controls saved editor values."""
import json
from pathlib import Path

import pytest

model = pytest.importorskip("nml_tools.gui.model")
from mhm_qgis.applications.nml_tools_handler import reconcile_domain_values


@pytest.mark.parametrize("order", [(1,), (1, 0), (1, 0, 2)])
@pytest.mark.parametrize("version", ["v5.13", "v6"])
def test_resize_preserves_surviving_domains_and_does_not_save(tmp_path, order, version):
    schemas = Path(__file__).parents[1] / "src/mhm_qgis/nml-schemas" / version
    project = model.load_project(schemas, tmp_path)
    dimension = "max_domains" if version == "v5.13" else "n_domains"
    document = model.empty_document(project)
    document["dimensions"][dimension] = 2
    if version == "v5.13":
        block, field = "directories_general", "dir_Morpho"
        custom_block, custom_field = "time_periods", "warming_Days"
        paths = ["data/350/", "data/550/"]
        custom = [7, 19]
    else:
        block, field = "config_input", "dem_path"
        custom_block, custom_field = "config_meteo", "frac_night_pre"
        paths = ["data/350/dem.nc", "data/550/dem.nc"]
        custom = [[0.2, 0.7] for _ in range(12)]
    saved = {block: {field: paths}, custom_block: {custom_field: custom}}
    document = model.merge_initial_values(document, {"main": saved}, project)
    path = tmp_path / "nml.json"
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    new_paths = [*paths, "data/new/dem.nc"]
    initial = {"main": {block: {field: [new_paths[index] for index in order]}}}
    dims = {dimension: len(order)}
    values = reconcile_domain_values(schemas, tmp_path, initial, dims)
    merged = model.merge_initial_values(
        model.merge_initial_dimensions(document, dims, project), values, project,
    )
    actual = merged["file_profiles"]["main"]["values"][custom_block][custom_field]
    if version == "v5.13":
        assert actual[:2] == [custom[i] for i in order if i < 2]
    else:
        assert len(actual) == 12
        assert actual[0][:2] == [custom[0][i] for i in order if i < 2]
        assert all(len(row) == len(order) for row in actual)
    assert path.read_bytes() == before
    assert initial == {"main": {block: {field: [new_paths[index] for index in order]}}}
