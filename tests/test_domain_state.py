"""Tests for QGIS-free domain-delineation state handling."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# isort: off
from mhm_qgis.core.handlers.state.domain_state import (
    DEM_DOMAIN_ID,
    active_domain_records,
    default_state,
    domain_count,
    gauge_records,
    gauged_outlet_ids,
    load_state,
    resolve_input_path,
    resolve_output_path,
    save_state,
    serialize_input_path,
    serialize_output_path,
    state_path,
)
# isort: on


def test_missing_and_malformed_state_return_fresh_defaults(tmp_path: Path) -> None:
    project = tmp_path / "project"

    assert load_state(project) == default_state()

    path = state_path(project)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_state(project) == default_state()

    path.write_text('["not", "an", "object"]', encoding="utf-8")
    assert load_state(project) == default_state()


def test_path_serialization_uses_project_and_workspace_roots(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    discharge = project / "inputs" / "gauge.csv"
    external = tmp_path / "external" / "gauge.csv"
    mask = project / ".temp" / "Morphology" / "mask.tif"

    assert serialize_input_path(project, discharge) == "inputs/gauge.csv"
    assert resolve_input_path(project, "inputs/gauge.csv") == discharge.resolve()
    assert serialize_input_path(project, external) == str(external.resolve())
    assert resolve_input_path(project, external) == external.resolve()

    assert (
        serialize_output_path(project, mask)
        == ".temp/Morphology/mask.tif"
    )
    assert (
        resolve_output_path(project, ".temp/Morphology/mask.tif")
        == mask.resolve()
    )
    with pytest.raises(ValueError, match="plugin workspace"):
        serialize_output_path(project, external)
    with pytest.raises(ValueError, match="plugin workspace"):
        resolve_output_path(project, "../outside.tif")


def test_save_is_atomic_normalized_and_does_not_mutate_input(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    discharge = project / "inputs" / "gauge.csv"
    mask = project / ".temp" / "Morphology" / "mask.tif"
    vector = mask.with_suffix(".gpkg")
    domain_directory = project / "data" / "7"
    domain_dem = domain_directory / "dem.asc"
    state = {
        "version": 99,
        "pour_points_source": "inputs/outlets.gpkg",
        "outlet_id_field": "station",
        "dem_domain": True,
        "outlets": {
            7: {
                "is_gauged": True,
                "is_domain": True,
                "discharge_file": str(discharge),
                "mask_path": str(mask),
                "vector_path": str(vector),
                "domain_directory": str(domain_directory),
                "dem_path": str(domain_dem),
            }
        },
    }

    path = save_state(project, state)

    assert path == project / ".temp" / path.name
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert saved["outlet_order"] == ["7"]
    assert saved["outlets"]["7"]["domain_id"] == 1
    assert saved["dem_domain_id"] is None
    assert saved["outlets"]["7"]["discharge_file"] == "inputs/gauge.csv"
    assert saved["outlets"]["7"]["mask_path"] == ".temp/Morphology/mask.tif"
    assert saved["outlets"]["7"]["vector_path"] == ".temp/Morphology/mask.gpkg"
    assert saved["outlets"]["7"]["domain_directory"] == "data/7"
    assert saved["outlets"]["7"]["dem_path"] == "data/7/dem.asc"
    assert state["outlets"][7]["mask_path"] == str(mask)
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
    assert load_state(project) == saved


def test_active_domains_include_optional_dem_domain_and_gauges() -> None:
    state = {
        "dem_domain": True,
        "outlets": {
            "A": {"is_domain": True, "is_gauged": True},
            "B": {"is_domain": False, "is_gauged": True},
            "C": {"is_domain": True, "is_gauged": False},
        },
    }

    assert gauged_outlet_ids(state) == ["A", "B"]
    records = active_domain_records(state)
    assert [record["outlet_id"] for record in records] == [
        "A",
        "C",
    ]
    assert records[0]["is_dem_domain"] is False
    assert records[-1]["is_dem_domain"] is False
    assert [record["domain_id"] for record in records] == [1, 1]
    assert domain_count(state) == 1


def test_dem_domain_can_be_the_only_active_domain(tmp_path: Path) -> None:
    project = tmp_path / "project"
    save_state(
        project,
        {
            "definition_mode": "dem_extent",
            "dem_domain": True,
            "dem_domain_directory": "data/dem_extent",
            "dem_domain_path": "data/dem_extent/dem.asc",
            "outlets": {
                # Gauged but not delineated: no outlet-derived watershed exists.
                "A": {"is_domain": False, "is_gauged": True, "gauge_id": 7},
            },
        },
    )

    state = load_state(project)
    records = active_domain_records(state)

    assert domain_count(state) == 1
    assert len(records) == 1
    record = records[0]
    assert record["outlet_id"] == DEM_DOMAIN_ID
    assert record["is_dem_domain"] is True
    assert record["domain_id"] == 1
    assert record["gauged_outlet_ids"] == ["A"]
    assert record["domain_directory"] == "data/dem_extent"
    assert record["dem_path"] == "data/dem_extent/dem.asc"


def test_saved_order_and_gauge_metadata_survive_sorted_json(tmp_path: Path) -> None:
    output = (
        tmp_path
        / "project"
        / "data"
        / "observation"
        / "streamflow"
        / "001.txt"
    )
    state = {
        "version": 1,
        "definition_type": "domain_delineator",
        "outlet_order": ["20", "001", "3"],
        "outlets": {
            "001": {
                "is_gauged": True,
                "is_domain": True,
                "gauge_id": 1,
                "gauge_filename": "001.txt",
                "gauge_path": str(output),
                "domain_ids": [1],
                "gauge_point": {
                    "x": 12.5,
                    "y": 47.25,
                    "crs": "EPSG:4326",
                    "source": "picked",
                },
            },
            "20": {"is_domain": True},
            "3": {"is_domain": False},
        },
    }

    path = save_state(tmp_path / "project", state)
    loaded = load_state(tmp_path / "project")

    assert loaded["version"] == 2
    assert loaded["definition_mode"] == "domain_delineator"
    assert loaded["outlet_order"] == ["20", "001", "3"]
    assert loaded["outlets"]["20"]["domain_id"] == 1
    assert loaded["outlets"]["001"]["domain_id"] == 1
    assert loaded["outlets"]["001"]["gauge_path"] == (
        "data/observation/streamflow/001.txt"
    )
    assert loaded["outlets"]["001"]["gauge_point"]["source"] == "picked"
    assert gauge_records(loaded) == [
        {
            "outlet_id": "001",
            "gauge_id": 1,
            "gauge_filename": "001.txt",
            "gauge_path": "data/observation/streamflow/001.txt",
            "domain_ids": [1],
        }
    ]
    assert json.loads(path.read_text(encoding="utf-8"))["outlet_order"] == [
        "20",
        "001",
        "3",
    ]
