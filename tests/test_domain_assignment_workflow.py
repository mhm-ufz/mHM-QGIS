"""QGIS-light tests for shared domain/gauge assignment validation."""
from __future__ import annotations

from pathlib import Path

import pytest



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.core import QgsVectorLayer  # noqa: E402

from mhm_qgis.core.morphology.hydrology.outlets import (  # noqa: E402
    OutletAssignment,
)
from mhm_qgis.core.handlers.state.domain_state import (  # noqa: E402
    DOMAIN_MODE_DEM_EXTENT,
    DOMAIN_MODE_DELINEATOR,
    gauge_records,
)
from mhm_qgis.qgis_bridge.layers.domain import (  # noqa: E402
    DomainWorkflow,
)
# isort: on


class _MainDialog:
    def __init__(self, project_folder: Path):
        self.project_folder = str(project_folder)
        self.messages = []

    def log_message(self, message):
        self.messages.append(message)


class _PourPoints:
    def __init__(self, source: Path):
        self._source = str(source)

    def source(self):
        return self._source


class _UriTable:
    def __init__(self, path: Path):
        self.path = path

    def source(self):
        return self.path.as_uri() + "?type=csv&delimiter=,"

    def isValid(self):
        return True

    def name(self):
        return self.path.name


def _table(path: Path) -> QgsVectorLayer:
    path.write_text(
        "date,discharge\n2020-01-01,1.5\n2020-01-02,2.5\n",
        encoding="utf-8",
    )
    return QgsVectorLayer(str(path), path.name, "ogr")


def _workflow(tmp_path: Path, outlet_ids=("001", "2")) -> DomainWorkflow:
    dialog = _MainDialog(tmp_path)
    return DomainWorkflow(
        tmp_path,
        _PourPoints(tmp_path / "outlets.gpkg"),
        "outlet_id",
        outlet_ids,
        log=dialog.log_message,
    )


def test_gauges_are_all_validated_before_any_observation_is_written(tmp_path):
    workflow = _workflow(tmp_path)
    valid = _table(tmp_path / "valid.csv")
    assignments = [
        OutletAssignment("001", True, False, valid),
        OutletAssignment("2", True, False, None),
    ]

    with pytest.raises(ValueError, match="outlet 2"):
        workflow.validate_gauge_assignments(assignments)
    assert not list(tmp_path.rglob("*.txt"))


def test_show_for_changed_point_source_preserves_committed_domains(tmp_path):
    from mhm_qgis.core.handlers.state.domain_state import load_state, save_state, save_preview

    workflow = _workflow(tmp_path, ("1",))
    save_state(tmp_path, {"pour_points_source": "previous.gpkg", "outlet_id_field": "id",
                         "outlets": {"1": {"is_domain": True, "mask_path": "old.tif"}}})
    source = workflow.layer_source(workflow.pour_points_layer)
    save_preview(tmp_path, source, "outlet_id", "1", {"raster_path": "preview.tif"})
    committed = load_state(tmp_path)
    assert committed["pour_points_source"] == "previous.gpkg"
    assert committed["outlets"]["1"]["is_domain"]
    assert committed["outlets"]["1"]["mask_path"] == "old.tif"
    draft = workflow.load_synced_state(DOMAIN_MODE_DELINEATOR)
    assert not draft["outlets"]["1"].get("is_domain")
    assert draft["outlets"]["1"]["delineation"]["raster_path"] == "preview.tif"

def test_assignment_state_separates_filename_text_from_numeric_gauge_id(tmp_path):
    workflow = _workflow(tmp_path)
    layer = _table(tmp_path / "valid.csv")
    assignments = [
        OutletAssignment("001", True, False, layer),
        OutletAssignment("2", False, False, None),
    ]
    prepared = workflow.validate_gauge_assignments(assignments)
    state = workflow.load_synced_state(DOMAIN_MODE_DEM_EXTENT, True)

    workflow.apply_assignment_records(state, assignments, prepared)
    workflow.validate_unique_state_gauge_ids(state)

    assert gauge_records(state) == [
        {
            "outlet_id": "001",
            "gauge_id": 1,
            "gauge_filename": "001.txt",
            "gauge_path": str(tmp_path / "data" / "observation" / "streamflow" / "001.txt"),
            "domain_ids": [],
        }
    ]


def test_file_provider_uri_is_persisted_as_a_reloadable_local_path(tmp_path):
    workflow = _workflow(tmp_path, ("1",))
    path = tmp_path / "valid.csv"
    _table(path)
    assignments = [OutletAssignment("1", True, False, _UriTable(path))]

    prepared = workflow.validate_gauge_assignments(assignments)
    state = workflow.load_synced_state(DOMAIN_MODE_DEM_EXTENT, True)
    workflow.apply_assignment_records(state, assignments, prepared)

    assert state["outlets"]["1"]["discharge_file"] == str(path.resolve())


def test_numeric_gauge_ids_must_be_unique_even_when_filenames_differ(tmp_path):
    workflow = _workflow(tmp_path, ("001", "1"))
    layer = _table(tmp_path / "valid.csv")

    with pytest.raises(ValueError, match="same numeric gauge ID 1"):
        workflow.validate_gauge_assignments(
            [
                OutletAssignment("001", True, False, layer),
                OutletAssignment("1", True, False, layer),
            ]
        )


def test_assignment_rows_must_still_match_selected_feature_order(tmp_path):
    workflow = _workflow(tmp_path)

    with pytest.raises(ValueError, match="no longer match"):
        workflow._validated_assignment_list(
            [
                OutletAssignment("2", False),
                OutletAssignment("001", False),
            ]
        )


def test_delineator_without_selected_catchments_uses_dem_extent(tmp_path):
    workflow = _workflow(tmp_path)
    state = workflow.load_synced_state(DOMAIN_MODE_DELINEATOR, False)

    workflow.require_active_domain(state)

    state["outlets"]["2"]["is_domain"] = True
    workflow.require_active_domain(state)


def test_snapped_points_are_regenerated_after_inputs_may_have_moved(
    tmp_path, monkeypatch
):
    from mhm_qgis.qgis_bridge.layers import domain

    prepared = []

    def prepare():
        prepared.append(True)
        channel = Path(workflow.channel_network_path)
        channel.parent.mkdir(parents=True, exist_ok=True)
        channel.write_text("channel", encoding="utf-8")

    workflow = DomainWorkflow(
        tmp_path,
        _PourPoints(tmp_path / "outlets.gpkg"),
        "outlet_id",
        ["1"],
        prepare=prepare,
    )
    channel_layer = object()
    monkeypatch.setattr(domain, "open_layer", lambda *_args, **_kwargs: channel_layer)

    def snap(points, channel, output, **_kwargs):
        assert points is workflow.pour_points_layer
        assert channel is channel_layer
        path = Path(output)
        path.write_text("fresh", encoding="utf-8")
        return str(path)

    monkeypatch.setattr(domain, "snap_points_to_network", snap)

    result = workflow.regenerate_snapped_points()

    assert prepared == [True]
    assert result == workflow.snapped_points_path
    assert Path(result).read_text(encoding="utf-8") == "fresh"
