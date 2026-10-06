"""Tests for plugin-specific geology parameter metadata."""

import json
import sys
from types import ModuleType

import pandas as pd

from mhm_qgis.core.handlers.lookup import geology as geology_metadata


def test_metadata_uses_selected_class_field(tmp_path, monkeypatch):
    table = pd.DataFrame(
        {
            "Mapped Unit": [20, 10],
            "GEO_CLASS": [2, 1],
            "KARSTIC": [False, True],
            "PARAMETER_VALUE": [200, 100],
        }
    )
    common = ModuleType("mhm_tools.common.lookup_handler")
    common.read_lookup_table = lambda _path: table
    monkeypatch.setitem(
        sys.modules, "mhm_tools.common.lookup_handler", common)

    output = tmp_path / "geology_class_metadata.json"
    result = geology_metadata.write_geology_metadata(
        tmp_path / "lookup.csv", "mapped unit", output
    )

    assert result == output
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "version": 1,
        "geology_class_count": 2,
        "classes": [
            {
                "geo_param": 1,
                "geology_class": 10,
                "karstic": 1,
                "parameter_value": 100,
            },
            {
                "geo_param": 2,
                "geology_class": 20,
                "karstic": 0,
                "parameter_value": 200,
            },
        ],
    }


def test_metadata_ignores_starred_geo_id(tmp_path, monkeypatch):
    table = pd.DataFrame(
        {
            "GEOLOGY_CLASS [count]": [1],
            "*GEO_ID [count]": [2],
            "KARSTIC [bool]": [0],
            "PARAMETER_VALUE [int]": [100],
        }
    )
    from mhm_qgis.core.handlers.lookup import table as lookup_table

    monkeypatch.setattr(lookup_table, "read", lambda _path: table)
    output = tmp_path / "geology.json"

    geology_metadata.write_geology_metadata(
        tmp_path / "lookup.csv", "GEOLOGY_CLASS [count]", output
    )

    record = json.loads(output.read_text(encoding="utf-8"))["classes"][0]
    assert record["geo_param"] == 1
    assert record["geology_class"] == 1
