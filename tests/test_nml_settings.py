"""Tests for the project-local namelist handoff."""



from mhm_qgis.core.handlers.state.domain_state import save_state
from mhm_qgis.core.handlers.state.nml_settings import load_settings, sync_domain_settings, update_section
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


def test_sections_are_merged_without_overwriting_future_inputs(tmp_path):
    update_section(tmp_path, "land_cover", {"variable": "land_cover"})
    update_section(tmp_path, "soil", {"variable": "soil_class"})

    state = load_settings(tmp_path)
    assert state["land_cover"]["variable"] == "land_cover"
    assert state["soil"]["variable"] == "soil_class"


def test_domain_gauges_project_to_v5_namelist_values(tmp_path):
    save_state(
        tmp_path,
        {
            "definition_mode": "domain_delineator",
            "pour_points_source": "pour_points.shp",
            "outlet_id_field": "station_id",
            "outlet_order": ["001"],
            "dem_domain": False,
            "outlets": {
                "001": {
                    "is_domain": True,
                    "is_gauged": True,
                    "gauge_id": 1,
                    "gauge_filename": "001.txt",
                    "gauge_path": "data/observation/streamflow/001.txt",
                    "domain_directory": "data/001",
                    "dem_path": "data/static/morph/dem.asc",
                    "domain_ids": [1],
                }
            },
        },
    )
    sync_domain_settings(tmp_path)

    state = load_settings(tmp_path)
    assert state["domains"] == [
        {
            "data_path": "data/",
            "dem_path": "data/static/morph/dem.asc",
            "directory": "data/static/morph",
            "domain_id": 1,
            "gauge_ids": [1],
            "is_dem_domain": False,
            "name": "master",
            "outlet_id": "",
        }
    ]
    assert state["gauges"][0]["gauge_filename"] == "001.txt"

    gauges = build_initial_values("5.13", Dialog(tmp_path))["main"][
        "evaluation_gauges"
    ]
    assert gauges["nGaugesTotal"] == 1
    assert gauges["NoGauges_domain"] == [1]
    assert gauges["Gauge_id"][0][0] == 1
    assert gauges["gauge_filename"][0][0] == "001.txt"
    directories = build_initial_values("5.13", Dialog(tmp_path))["main"][
        "directories_general"
    ]
    assert directories["dir_Morpho"] == ["data/static/morph/"]
    assert directories["dirCommonFiles"] == "data/static/morph/"


def test_sole_dem_domain_projects_with_its_gauges(tmp_path):
    save_state(
        tmp_path,
        {
            "definition_mode": "dem_extent",
            "pour_points_source": "pour_points.shp",
            "outlet_id_field": "station_id",
            "outlet_order": ["001"],
            "dem_domain": True,
            "dem_domain_directory": "data/dem_extent",
            "dem_domain_path": "data/dem_extent/dem.asc",
            "outlets": {
                "001": {
                    "is_domain": False,
                    "is_gauged": True,
                    "gauge_id": 1,
                    "gauge_filename": "001.txt",
                    "gauge_path": "data/observation/streamflow/001.txt",
                    "domain_ids": [1],
                }
            },
        },
    )
    sync_domain_settings(tmp_path)

    state = load_settings(tmp_path)
    assert state["domain_definition"] == {
        "mode": "dem_extent",
        "dem_domain": True,
    }
    # The valid-DEM domain is the only merged domain and owns domain_id 1.
    assert state["domains"] == [
        {
            "data_path": "data/",
            "dem_path": "data/static/morph/dem.asc",
            "directory": "data/static/morph",
            "domain_id": 1,
            "gauge_ids": [1],
            "is_dem_domain": True,
            "name": "master",
            "outlet_id": "",
        }
    ]

    directories = build_initial_values("5.13", Dialog(tmp_path))["main"][
        "directories_general"
    ]
    assert directories["dir_Morpho"] == ["data/static/morph/"]
    assert directories["dirCommonFiles"] == "data/static/morph/"


def test_shared_gauges_are_counted_once_per_domain(tmp_path):
    """nGaugesTotal is the sum over subdomains, not the number of distinct gauges.

    A DEM domain repeats the gauges of the pour-point domains it contains, so the
    same gauge id shows up in several domains. mHM sizes its gauge arrays from
    nGaugesTotal while indexing them once per (domain, gauge) pair, and aborts with
    an out-of-bounds error in mrm_write when the two disagree.
    """
    save_state(
        tmp_path,
        {
            "definition_mode": "domain_delineator",
            "pour_points_source": "pour_points.shp",
            "outlet_id_field": "station_id",
            "outlet_order": ["240", "280", "350"],
            "dem_domain": True,
            "dem_domain_directory": "data/dem_extent",
            "dem_domain_path": "data/dem_extent/dem.asc",
            "outlets": {
                "240": {
                    "is_domain": True,
                    "is_gauged": True,
                    "gauge_id": 240,
                    "gauge_filename": "240.txt",
                    "gauge_path": "data/observation/streamflow/240.txt",
                    "domain_directory": "data/240",
                    "dem_path": "data/240/dem.asc",
                    "domain_ids": [1, 3],
                },
                "280": {
                    "is_domain": False,
                    "is_gauged": True,
                    "gauge_id": 280,
                    "gauge_filename": "280.txt",
                    "gauge_path": "data/observation/streamflow/280.txt",
                    "domain_ids": [1, 3],
                },
                "350": {
                    "is_domain": True,
                    "is_gauged": True,
                    "gauge_id": 350,
                    "gauge_filename": "350.txt",
                    "gauge_path": "data/observation/streamflow/350.txt",
                    "domain_directory": "data/350",
                    "dem_path": "data/350/dem.asc",
                    "domain_ids": [2, 3],
                },
            },
        },
    )
    sync_domain_settings(tmp_path)

    gauges = build_initial_values("5.13", Dialog(tmp_path))["main"][
        "evaluation_gauges"
    ]
    # Guard the fixture itself: three gauges spread over three domains, the DEM
    # domain repeating all of them.
    assert gauges["NoGauges_domain"] == [3]
    assert len({gid for row in gauges["Gauge_id"] for gid in row if gid}) == 3

    assert gauges["nGaugesTotal"] == 3
    assert gauges["nGaugesTotal"] == sum(gauges["NoGauges_domain"])


def test_lai_settings_project_to_both_namelist_versions(tmp_path):
    update_section(
        tmp_path,
        "lai",
        {
            "time_step": -2,
            "output_path": "data/lai/lai.nc",
            "variable": "lai",
        },
    )

    v5 = build_initial_values("5.13", Dialog(tmp_path))["main"]
    v6 = build_initial_values("6.0", Dialog(tmp_path))["main"]

    assert v5["LAI_data_information"] == {
        "timeStep_LAI_input": -2,
        "inputFormat_gridded_LAI": "nc",
    }
    assert v6["config_mpr"]["lai_time_step"] == [-2]
    assert v6["config_mpr"]["lai_path"] == ["data/lai/lai.nc"]
    assert v6["config_mpr"]["lai_var"] == ["lai"]
