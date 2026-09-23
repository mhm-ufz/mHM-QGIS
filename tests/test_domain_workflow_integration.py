"""Focused integration tests for saved domain workflow choices."""
from __future__ import annotations

from pathlib import Path

import pytest



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.core import QgsCoordinateReferenceSystem  # noqa: E402

from mhm_qgis.core.executions.morphology.reset import clear_geometry  # noqa: E402
from mhm_qgis.core.handlers.state.domain_state import load_state, save_state  # noqa: E402
from mhm_qgis.core.handlers.store.layout import temp_morphology_folder  # noqa: E402
from mhm_qgis.qgis_bridge.layers import gauges  # noqa: E402
from mhm_qgis.qt.controllers.main import update_gauged_outlet_count  # noqa: E402
from mhm_qgis.vSpecific.common import domain_count  # noqa: E402
# isort: on


class _Label:
    def __init__(self):
        self.value = ""

    def setText(self, value):
        self.value = value


class _Fields:
    def names(self):
        return ["outlet_id"]


class _Point:
    def __init__(self, x, y):
        self._x = x
        self._y = y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Geometry:
    def __init__(self, x=0.0, y=0.0):
        self.point = _Point(x, y)

    def isEmpty(self):
        return False

    def asPoint(self):
        return self.point


class _Feature:
    def __init__(self, outlet_id, x=0.0, y=0.0):
        self.outlet_id = outlet_id
        self._geometry = _Geometry(x, y)

    def attribute(self, _field):
        return self.outlet_id

    def geometry(self):
        return self._geometry


class _Layer:
    def __init__(self, features=None, count=0):
        self._features = list(features or [])
        self._count = count
        self._crs = QgsCoordinateReferenceSystem("EPSG:4326")

    def isValid(self):
        return True

    def fields(self):
        return _Fields()

    def crs(self):
        return self._crs

    def getFeatures(self):
        return iter(self._features)

    def featureCount(self):
        return self._count


def test_saved_state_controls_gauge_and_domain_counts(tmp_path: Path) -> None:
    save_state(
        tmp_path,
        {
            "pour_points_source": "inputs/outlets.gpkg",
            "outlet_id_field": "outlet_id",
            "dem_domain": True,
            "outlets": {
                "1": {"is_gauged": True, "is_domain": True},
                "2": {"is_gauged": False, "is_domain": True},
            },
        },
    )
    label = _Label()
    selector = type("Selector", (), {"currentLayer": lambda self: _Layer(count=20)})()
    dialog = type(
        "Dialog",
        (),
        {
            "project_folder": str(tmp_path),
            "input_combo": lambda self, kind: selector,
            "label_numberOfGaugedOutletsValue": label,
        },
    )()

    assert update_gauged_outlet_count(dialog) == "1"
    assert label.value == "1"
    assert domain_count(dialog) == 1


def test_configured_project_keeps_one_domain_minimum(tmp_path: Path) -> None:
    save_state(
        tmp_path,
        {"outlet_id_field": "outlet_id", "outlets": {"1": {"is_domain": False}}},
    )
    dialog = type("Dialog", (), {"project_folder": str(tmp_path)})()

    assert domain_count(dialog) == 1


def test_gauge_coordinates_only_include_saved_gauged_ids(tmp_path, monkeypatch):
    save_state(
        tmp_path,
        {
            "outlet_id_field": "outlet_id",
            "outlet_order": ["1", "2"],
            "outlets": {
                "1": {"is_gauged": False},
                "2": {"is_gauged": True},
            },
        },
    )
    layer = _Layer([_Feature("1", 10, 11), _Feature("2", 20, 21)])
    monkeypatch.setattr(gauges, "open_layer", lambda *_args, **_kwargs: layer)
    monkeypatch.setattr(gauges, "crs_of", lambda _path: layer.crs())
    monkeypatch.setattr(gauges, "transform_between", lambda *_args: None)

    result = gauges.gauge_coordinates(tmp_path, "snapped.shp", "dem.tif")

    assert result == ((2, 20.0, 21.0),)


def test_configured_project_with_no_gauges_is_rejected(tmp_path, monkeypatch):
    save_state(
        tmp_path,
        {
            "outlet_id_field": "outlet_id",
            "outlets": {"1": {"is_gauged": False}},
        },
    )
    layer = _Layer([_Feature("1")])
    monkeypatch.setattr(gauges, "open_layer", lambda *_args, **_kwargs: layer)
    monkeypatch.setattr(gauges, "crs_of", lambda _path: layer.crs())
    monkeypatch.setattr(gauges, "transform_between", lambda *_args: None)

    with pytest.raises(ValueError, match="No outlets are marked as gauged"):
        gauges.gauge_coordinates(tmp_path, "snapped.shp", "dem.tif")


def test_geometry_reset_invalidates_only_derived_domain_outputs(tmp_path: Path) -> None:
    geometry = Path(temp_morphology_folder(tmp_path))
    generated = geometry / "Watersheds" / "mask.tif"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("generated", encoding="utf-8")
    save_state(
        tmp_path,
        {
            "pour_points_source": "inputs/outlets.gpkg",
            "outlet_id_field": "outlet_id",
            "dem_domain": True,
            "outlets": {
                "7": {
                    "is_gauged": True,
                    "is_domain": True,
                    "discharge_file": "inputs/gauge.csv",
                    "threshold_cells": 42,
                    "picked": {"x": 1.0, "y": 2.0},
                    "catchment_area_m2": 123.0,
                    "mask_path": str(generated),
                    "vector_path": str(generated.with_suffix(".gpkg")),
                }
            },
        },
    )

    assert clear_geometry(tmp_path) == 1

    record = load_state(tmp_path)["outlets"]["7"]
    assert record["is_gauged"] is True
    assert record["is_domain"] is True
    assert record["discharge_file"] == "inputs/gauge.csv"
    assert record["threshold_cells"] == 42
    assert "picked" not in record
    assert "catchment_area_m2" not in record
    assert "mask_path" not in record
    assert "vector_path" not in record
