"""QGIS-free tests for pour-point outlet ID helpers."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
from mhm_qgis.core.morphology.hydrology.outlets import (
    StationIdError,
    find_outlet_id_field,
    find_station_id_field,
    outlet_ids_from_layer,
    station_id_int,
)
# isort: on


class _Fields:
    def __init__(self, names):
        self._names = list(names)

    def names(self):
        return list(self._names)


class _Feature:
    def __init__(self, values):
        self._values = dict(values)

    def attribute(self, name):
        return self._values.get(name)


class _Layer:
    def __init__(self, field_names, rows=(), valid=True):
        self._fields = _Fields(field_names)
        self._features = [_Feature(row) for row in rows]
        self._valid = valid

    def isValid(self):
        return self._valid

    def fields(self):
        return self._fields

    def getFeatures(self):
        return iter(self._features)


def test_outlet_field_lookup_accepts_case_and_shapefile_truncation():
    layer = _Layer(["name", "outlet_id", "station_id"])

    assert find_outlet_id_field(layer, "OUTLET_ID") == "outlet_id"
    assert find_outlet_id_field(layer, "station_identifier") == "station_id"


def test_station_field_lookup_remains_backward_compatible():
    layer = _Layer(["station_id"])

    assert find_station_id_field(layer) == "station_id"
    with pytest.raises(StationIdError, match="selected outlet ID field"):
        find_outlet_id_field(layer, "missing")


def test_selected_outlet_values_preserve_string_ids_and_must_be_unique():
    layer = _Layer(
        ["code"],
        [{"code": " 01 "}, {"code": 2.0}, {"code": "three"}],
    )

    assert outlet_ids_from_layer(layer, "code") == ["01", "2", "three"]

    duplicate = _Layer(["code"], [{"code": "01"}, {"code": " 01 "}])
    with pytest.raises(StationIdError, match="Duplicate outlet ID '01'"):
        outlet_ids_from_layer(duplicate, "code")

    empty = _Layer(["code"], [{"code": None}])
    with pytest.raises(StationIdError, match="empty code"):
        outlet_ids_from_layer(empty, "code")


def test_gauge_raster_ids_reject_fractional_values():
    assert station_id_int("7.0") == 7
    with pytest.raises(StationIdError, match="integer"):
        station_id_int("7.5")
