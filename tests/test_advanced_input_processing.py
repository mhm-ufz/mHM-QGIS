"""Integration tests for advanced-input publishing and namelist state."""

from pathlib import Path

from mhm_qgis.core.handlers.lookup import (
    LandUseInput,
    LandUsePeriod,
    SoilHorizon,
    SoilInput,
)
from mhm_qgis.core.handlers.state.nml_settings import load_settings
from mhm_qgis.core.handlers.store.layout import ensure_project_structure
from mhm_qgis.core.morphology.layers import advanced_inputs as processing
from mhm_qgis.vSpecific import build_initial_values


class Dialog:
    def __init__(self, project_folder):
        self.project_folder = str(project_folder)

    def current_l1_resolution(self):
        return 1000

    def current_l11_resolution(self):
        return 2000

    def current_grid_unit(self):
        return "m"


def _touch(path, content=b"raster"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_land_cover_processing_writes_period_state(tmp_path, monkeypatch):
    ensure_project_structure(tmp_path, "5.13")
    first = _touch(tmp_path / "lc_a.tif")
    second = _touch(tmp_path / "lc_b.tif")
    lookup = _touch(tmp_path / "lookup.csv", b"source,class\n1,2\n")
    dem = _touch(tmp_path / "dem.tif")
    value = LandUseInput(
        (
            LandUsePeriod(1990, 1999, first),
            LandUsePeriod(2000, 2010, second),
        ),
        lookup,
        "source",
        "class",
    )
    manifests = []

    def fake_formatter(input_file, dem_file, output_path, *args, **kwargs):
        manifests.append(Path(input_file))
        output = Path(output_path)
        assert kwargs["output_type"] == "nc"
        return (_touch(output / "lc_periods.nc"),)

    monkeypatch.setattr(processing, "prepare_land_cover_periods", fake_formatter)
    outputs = processing.process_land_cover_input(
        tmp_path, "5.13", value, dem
    )

    assert [path.name for path in outputs] == ["lc_periods.nc"]
    # mHM-tools reads the manifest itself, so it must receive the CSV.
    assert manifests[0].is_file()
    assert manifests[0].suffix == ".csv"

    state = load_settings(tmp_path)["land_cover"]
    assert state["variable"] == "land_cover"
    assert [scene["output_path"] for scene in state["scenes"]] == [
        "data/static/morph/lc_1990_1999.asc",
        "data/static/morph/lc_2000_2010.asc",
    ]
    values = build_initial_values("5.13", Dialog(tmp_path))["main"]["LCover"]
    assert values["nLCoverScene"] == 2
    assert values["LCoverfName"][:2] == [
        "lc_1990_1999.asc",
        "lc_2000_2010.asc",
    ]


def test_soil_processing_saves_v6_schema_values_and_units(tmp_path, monkeypatch):
    from dataclasses import replace

    from mhm_qgis.core.native_worker import run

    ensure_project_structure(tmp_path, "6.0")
    sources = [_touch(tmp_path / f"soil_{index}.tif") for index in range(4)]
    dem = _touch(tmp_path / "dem.tif")
    value = SoilInput((SoilHorizon(1, 0, 300, *sources),), "kg/m3",
                      composition_step=2.5, bulk_density_step=0.05)
    digest = processing.input_fingerprint(value, dem, "6.0")
    for changed in (replace(value, composition_step=5.0),
                    replace(value, bulk_density_step=0.1)):
        assert processing.input_fingerprint(changed, dem, "6.0") != digest

    manifests = []

    def fake_formatter(input_file, dem_file, output_path, **kwargs):
        assert kwargs["composition_step"] == 2.5
        assert kwargs["bulk_density_step"] == 0.05
        manifests.append(Path(input_file))
        output = Path(output_path)
        return (
            _touch(output / "soil_horizon_class.nc"),
            _touch(
                output / "soil_classdefinition_iFlag_soilDB_1.txt",
                b"nSoil_Types 1\n",
            ),
        )

    monkeypatch.setattr(processing, "prepare_soil_horizons", fake_formatter)
    result = run({"kind": "soil", "project_folder": str(tmp_path),
                  "version": "6.0", "value": value.as_dict(), "filled_dem": str(dem)})
    data, definition = map(Path, result["outputs"])

    assert manifests[0].is_file()
    assert manifests[0].suffix == ".csv"

    state = load_settings(tmp_path)["soil"]
    assert data.name == "soil_horizon_class.nc"
    assert definition.name == "soil_classdefinition_iFlag_soilDB_1.txt"
    assert state["source_bulk_density_unit"] == "kg/m3"
    assert state["bulk_density_unit"] == "g/cm3"
    assert state["composition_step"] == 2.5
    assert state["bulk_density_step"] == 0.05
    assert state["composition_normalization"] == "component_sum_percent"
    assert state["horizons"][0]["lower_depth"] == 300

    main = build_initial_values("6.0", Dialog(tmp_path))["main"]
    assert main["config_mpr"]["soil_db_mode"] == [1]
    assert main["config_mpr"]["n_layers"] == [1]
    assert main["config_mpr"]["soil_depth"][0] == [300]
    assert main["config_input"]["soil_horizon_class_path"] == [
        ".temp/Morphology/morph/soil_horizon_class.nc"
    ]


def test_v5_horizons_use_the_shared_netcdf_workflow(tmp_path, monkeypatch):
    ensure_project_structure(tmp_path, "5.13")
    sources = [_touch(tmp_path / f"soil_{index}.tif") for index in range(4)]
    value = SoilInput((SoilHorizon(1, 0, 300, *sources),), "kg/m3")
    dem = _touch(tmp_path / "dem.tif")

    def formatter(_manifest, _dem, output, **options):
        assert options["output_type"] == "nc"
        return (
            _touch(Path(output) / "soil_horizon_class.nc"),
            _touch(Path(output) / "soil_classdefinition_iFlag_soilDB_1.txt"),
        )

    monkeypatch.setattr(processing, "prepare_soil_horizons", formatter)
    data, definition = processing.process_soil_input(tmp_path, "5.13", value, dem)
    assert (data.name, definition.name) == (
        "soil_horizon_class.nc",
        "soil_classdefinition_iFlag_soilDB_1.txt",
    )
    assert load_settings(tmp_path)["soil"]["soil_db_mode"] == 1


def test_soil_state_projects_horizons_to_v5_soildata(tmp_path):
    ensure_project_structure(tmp_path, "5.13")
    from mhm_qgis.core.handlers.state.nml_settings import update_section

    update_section(
        tmp_path,
        "soil",
        {
            "mode": "multi_horizon",
            "soil_db_mode": 1,
            "horizons": [
                {"lower_depth": 100},
                {"lower_depth": 300},
            ],
        },
    )

    soil = build_initial_values("5.13", Dialog(tmp_path))["main"]["soildata"]
    assert soil["iFlag_soilDB"] == 1
    assert soil["nSoilHorizons_mHM"] == 2
    assert soil["soil_Depth"] == [100, 300]
