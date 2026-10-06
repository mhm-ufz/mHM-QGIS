"""Version-contract tests for the embedded nml-tools GUI projects."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from nml_tools.gui.arrays import axis_labels
from nml_tools.gui.model import (
    load_project,
)
from nml_tools.gui.model import render_profile as _render_profile

from mhm_qgis.core.handlers.store.paths import temp_morphology_folder
from mhm_qgis.vSpecific import build_dimensions, build_initial_values

SCHEMAS = Path(__file__).resolve().parents[1] / "src" / "mhm_qgis" / "nml-schemas"


def render_profile(*args):
    return "\n".join(_render_profile(*args).values())


def profile_values(document, profile):
    return document.get(profile.key, {})
V5_MAIN_GROUPS = [
    "project_description",
    "mainconfig",
    "mainconfig_mhm_mrm",
    "mainconfig_mrm",
    "config_riv_temp",
    "directories_general",
    "directories_mHM",
    "directories_mRM",
    "optional_data",
    "processSelection",
    "LCover",
    "time_periods",
    "soildata",
    "LAI_data_information",
    "LCover_MPR",
    "directories_MPR",
    "evaluation_gauges",
    "inflow_gauges",
    "panEvapo",
    "nightDayRatio",
    "Optimization",
    "baseflow_config",
]
V5_PARAMETER_GROUPS = [
    "interception1",
    "snow1",
    "soilmoisture1",
    "soilmoisture2",
    "soilmoisture3",
    "soilmoisture4",
    "directRunoff1",
    "PETminus1",
    "PET0",
    "PET1",
    "PET2",
    "PET3",
    "interflow1",
    "percolation1",
    "routing1",
    "routing2",
    "routing3",
    "neutrons1",
    "neutrons2",
    "geoparameter",
]
MONTH_LABELS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
PARAMETER_COMPONENT_LABELS = [
    "Lower bound",
    "Upper bound",
    "Value",
    "Flag",
    "Scaling",
]


class Dialog:
    def __init__(self, project_folder: Path):
        self.project_folder = str(project_folder)
        self._input_adapters = {}

    def input_combo(self, kind):
        return self._input_adapters.get(kind)

    def current_l1_resolution(self):
        return 1000.0

    def current_l11_resolution(self):
        return 2000.0

    def current_grid_unit(self):
        return "m"


class Field:
    def __init__(self, name: str):
        self._name = name

    def name(self):
        return self._name


class Feature:
    def __init__(self, is_domain: bool):
        self.is_domain = is_domain

    def attribute(self, name: str):
        return self.is_domain if name == "IS_DOMAIN" else None


class Layer:
    def isValid(self):
        return True

    def fields(self):
        return [Field("IS_DOMAIN")]

    def getFeatures(self):
        return [Feature(True), Feature(False), Feature(True)]


class LayerWidget:
    def currentLayer(self):
        return Layer()


def _document(version: str, dialog: Dialog):
    key = "v5.13" if version.startswith("5") else "v6"
    project = load_project(SCHEMAS / key, dialog.project_folder)
    dimensions = build_dimensions(version, dialog)
    document = build_initial_values(version, dialog)
    return project, dimensions, document


def test_version_projects_have_model_filenames_and_dimensions(tmp_path: Path) -> None:
    v5 = load_project(SCHEMAS / "v5.13", tmp_path)
    v6 = load_project(SCHEMAS / "v6", tmp_path)

    # nml-tools canonicalises dimension names to lower case, so the camelCase
    # spellings in nml-config.toml are for readers only.
    assert v5.default_dimensions == {
        "ndomains": 3,
        "ngeounits": 16,
        "nsoilhorizons": 6,
        "nlcoverscene": 5,
        "ngaugestotal": 20,
    }
    assert {profile.name: profile.default_file for profile in v5.profiles} == {
        "main": "mhm.nml",
        "output": "mhm_outputs.nml",
        "parameter": "mhm_parameter.nml",
    }
    assert {profile.name: profile.default_file for profile in v6.profiles} == {
        "meta": "combine.nml",
        "main": "mhm.nml",
        "output": "outputs.nml",
        "parameter": "parameters.nml",
    }


def test_v513_schemas_match_reference_groups_and_fields(tmp_path: Path) -> None:
    root = SCHEMAS / "v5.13"
    project = load_project(root, tmp_path)
    main = project.profile("main")
    parameter = project.profile("parameter")

    assert [page.name for page in main.pages] == V5_MAIN_GROUPS
    assert [page.name for page in parameter.pages] == V5_PARAMETER_GROUPS
    assert not list((root / "nml-schemas").glob("legacy_*.yml"))
    assert "legacy_" not in (root / "nml-config.toml").read_text(encoding="utf-8")

    schemas = {page.name: page.schema for page in main.pages}
    assert list(schemas["config_riv_temp"]["properties"]) == [
        "albedo_water",
        "pt_a_water",
        "emissivity_water",
        "turb_heat_ex_coeff",
        "max_iter",
        "delta_iter",
        "step_iter",
        "riv_widths_file",
        "riv_widths_name",
        "dir_riv_widths",
    ]
    assert list(schemas["optional_data"]["properties"]) == [
        "dir_soil_moisture",
        "nSoilHorizons_sm_input",
        "timeStep_sm_input",
        "dir_neutrons",
        "dir_evapotranspiration",
        "timeStep_et_input",
        "dir_tws",
        "timeStep_tws_input",
        "dir_spf",
        "timeStep_spf_input",
        "weight_for_optional_data",
        "snow_water_equivalent_threshold_for_spf",
    ]
    assert list(schemas["inflow_gauges"]["properties"]) == [
        "nInflowGaugesTotal",
        "NoInflowGauges_domain",
        "InflowGauge_id",
        "InflowGauge_filename",
        "InflowGauge_Headwater",
    ]
    assert "bound_error" in schemas["directories_mHM"]["properties"]
    assert {"Gauge_id", "gauge_filename"} <= set(
        schemas["evaluation_gauges"]["properties"]
    )
    for name in ("LCoverYearStart", "LCoverYearEnd", "LCoverfName"):
        field = schemas["LCover"]["properties"][name]
        assert field["x-fortran-shape"] == "nLcoverScene"
        assert "x-fortran-flex-tail-dims" not in field


def test_v513_array_axis_labels_match_version_contract(tmp_path: Path) -> None:
    project = load_project(SCHEMAS / "v5.13", tmp_path)
    schemas = {page.name: page.schema for page in project.namelists}

    pan_evaporation = schemas["panEvapo"]["properties"]["evap_coeff"]
    assert axis_labels(pan_evaporation, 1, 12) == MONTH_LABELS

    night_ratios = schemas["nightDayRatio"]["properties"]
    for name in (
        "fnight_prec",
        "fnight_pet",
        "fnight_temp",
        "fnight_ssrd",
        "fnight_strd",
    ):
        assert axis_labels(night_ratios[name], 1, 12) == MONTH_LABELS

    parameter_fields = [
        field
        for page in project.namelists
        for field in page.schema.get("properties", {}).values()
        if field.get("x-fortran-shape") == 5
    ]
    # 136 after `rivertemp1` was dropped: it was registered but listed in no
    # file profile, so its four parameters were never reachable from the GUI.
    assert len(parameter_fields) == 136
    for field in parameter_fields:
        assert axis_labels(field, 1, 5) == PARAMETER_COMPONENT_LABELS

    geo_parameter = schemas["geoparameter"]["properties"]["GeoParam"]
    assert axis_labels(geo_parameter, 2, 5) == PARAMETER_COMPONENT_LABELS
    assert geo_parameter["x-nml-tools-ui"]["table"] == {
        "row-axis": 1,
        "column-axis": 2,
    }


def test_initial_values_render_version_specific_main_groups(tmp_path: Path) -> None:
    dialog = Dialog(tmp_path)

    v5, v5_dimensions, v5_document = _document("5.13", dialog)
    v5_main = v5.profile("main")
    rendered_v5 = render_profile(
        v5,
        v5_main,
        profile_values(v5_document, v5_main),
        v5_dimensions,
    )
    assert "&mainconfig\n" in rendered_v5
    assert "&directories_mHM\n" in rendered_v5
    assert "resolution_Hydrology(1) = 1000.0" in rendered_v5
    assert 'dir_Morpho(1) = "data/static/morph/"' in rendered_v5
    assert "&config_input" not in rendered_v5

    v6, v6_dimensions, v6_document = _document("6.0", dialog)
    v6_main = v6.profile("main")
    rendered_v6 = render_profile(
        v6,
        v6_main,
        profile_values(v6_document, v6_main),
        v6_dimensions,
    )
    assert "&config_input\n/" in rendered_v6
    assert "&config_resolution\n" in rendered_v6
    assert "hydro(1) = 1000.0" in rendered_v6


def test_domain_dimensions_and_paths_follow_plugin_domains(tmp_path: Path) -> None:
    dialog = Dialog(tmp_path)
    dialog._input_adapters["pour_points"] = LayerWidget()

    assert build_dimensions("5.13", dialog)["nDomains"] == 1
    assert build_dimensions("6.0", dialog)["n_domains"] == 1
    v5_values = build_initial_values("5.13", dialog)
    v6_values = build_initial_values("6.0", dialog)
    assert len(v5_values["main"]["directories_general"]["dir_Morpho"]) == 1
    assert v6_values["main"]["config_input"] == {}


def test_geoparameter_orientation_is_version_specific(tmp_path: Path) -> None:
    metadata = Path(temp_morphology_folder(tmp_path)) / "geology_class_metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps(
            {
                "classes": [
                    {"geo_param": 1, "parameter_value": 42.0},
                    {"geo_param": 2, "parameter_value": 84.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    dialog = Dialog(tmp_path)

    v5, dims, values = _document("5.13", dialog)
    geo = values["parameter"]["geoparameter"]["GeoParam"]
    assert [row[2] for row in geo] == [42.0, 84.0]
    assert "42" in render_profile(v5, v5.profile("parameter"), values["parameter"], dims)
    v6, dims, values = _document("6.0", dialog)
    geo = values["parameter"]["baseflow_1"]["baseflow_recession"]
    assert [row["value"] for row in geo] == [42.0, 84.0]
    rendered = render_profile(v6, v6.profile("parameter"), values["parameter"], dims)
    assert "&baseflow_1" in rendered and "%value" in rendered


def test_every_profile_form_constructs_and_renders(tmp_path: Path) -> None:
    pytest.importorskip("qgis.PyQt")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from nml_tools.gui.app import ProfileTab
    from qtpy.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    dialog = Dialog(tmp_path)
    from mhm_qgis.core.handlers.state.nml_settings import update_section

    # Supply real soil configuration rather than depending on the external
    # editor's zero padding of an incomplete schema example.
    update_section(tmp_path, "soil", {"horizons": [
        {"lower_depth": 200}, {"lower_depth": 500}, {"lower_depth": 1000}]})
    for version in ("5.13", "6.0"):
        project, dimensions, document = _document(version, dialog)
        # The editor window normalizes names before building its tabs.
        dimensions = {name.lower(): size for name, size in dimensions.items()}
        for profile in project.profiles:
            editor = ProfileTab(
                project,
                profile,
                profile_values(document, profile),
                dimensions,
            )
            values = {
                page.name: editor.forms[page.name].values()
                for page in profile.pages
            }
            rendered = render_profile(project, profile, values, dimensions)
            assert rendered
            assert re.findall(r"(?m)^&([A-Za-z][A-Za-z0-9_]*)$", rendered) == [
                page.name for page in profile.pages
            ]
            if version == "5.13" and profile.name == "main":
                assert "&config_riv_temp\n" in rendered
                assert "&optional_data\n" in rendered
                assert "&inflow_gauges\n" in rendered
            if version == "5.13" and profile.name == "output":
                assert "&NLoutputResults\n" in rendered
                assert "outputFlxState(1)" in rendered
            if version == "5.13" and profile.name == "parameter":
                assert rendered.index("&PETminus1\n") < rendered.index("&PET0\n")
                assert "&rivertemp1\n" not in rendered
            editor.close()
    assert application is not None


def test_v513_parameters_share_one_base_type(tmp_path: Path) -> None:
    """Each v5.13 parameter $refs parameter.yml instead of repeating the array."""
    import yaml

    root = SCHEMAS / "v5.13" / "nml-schemas"
    base = yaml.safe_load((root / "parameter.yml").read_text())["$defs"]["parameter"]
    assert base["x-fortran-shape"] == 5
    assert base["x-nml-tools-ui"]["axes"]["1"]["labels"] == PARAMETER_COMPONENT_LABELS

    referencing = repeated = 0
    for path in sorted(root.glob("parameter_*.yml")):
        for name, field in yaml.safe_load(path.read_text())["properties"].items():
            if field.get("$ref") == "parameter.yml#/$defs/parameter":
                referencing += 1
                # The $ref carries the shape; repeating it here would let the
                # two drift apart.
                assert "x-fortran-shape" not in field, f"{path.name}:{name}"
                assert "x-nml-tools-ui" not in field, f"{path.name}:{name}"
            elif field.get("x-fortran-shape") == 5:
                repeated = repeated + 1
    assert referencing == 136
    assert repeated == 0, "a five-element parameter still spells the array out"

    # GeoParam is the one genuine exception: a [nGeoUnits, 5] table.
    geo = yaml.safe_load((root / "parameter_geoparameter.yml").read_text())
    assert geo["properties"]["GeoParam"]["x-fortran-shape"] == ["nGeoUnits", 5]


def test_v513_arrays_are_sized_to_the_project_not_to_a_maximum(tmp_path: Path) -> None:
    """Each dimension comes from a real count, so no array is zero-padded."""
    from mhm_qgis.core.handlers.state.nml_settings import update_section

    dialog = Dialog(tmp_path)
    update_section(tmp_path, "soil", {"horizons": [
        {"lower_depth": 200}, {"lower_depth": 500}, {"lower_depth": 1000}]})
    update_section(tmp_path, "gauges", [
        {"gauge_id": 1, "domain_ids": [1]},
        {"gauge_id": 2, "domain_ids": [1]},
    ])
    project, dimensions, document = _document("5.13", dialog)

    assert dimensions["nSoilHorizons"] == 3
    assert dimensions["nLcoverScene"] == 1
    assert dimensions["nGaugesTotal"] == 2

    main = project.profile("main")
    rendered = render_profile(project, main, profile_values(document, main), dimensions)

    # The padding these dimensions replaced wrote 50 land-cover entries and ten
    # soil depths for a project that has one scene and three horizons.
    assert "LCoverfName(1)" in rendered and "LCoverfName(2)" not in rendered
    assert "soil_Depth(3)" in rendered and "soil_Depth(4)" not in rendered
    assert "Gauge_id(1,2)" in rendered and "Gauge_id(1,3)" not in rendered
