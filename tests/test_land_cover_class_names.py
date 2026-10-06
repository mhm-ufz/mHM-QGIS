"""Tests for land-cover labels used by elevation-band reports."""

import pandas as pd

from mhm_qgis.core.morphology.elevation_bands import (
    read_land_cover_class_names,
)


def test_names_are_keyed_by_formatted_class_field(monkeypatch):
    # Patch the lookup API, which is the boundary the code under test crosses.
    # Reaching into mhm_tools.common directly coupled this to where mhm-tools
    # happened to keep read_lookup_table, and it moved.
    from mhm_qgis.core.handlers import lookup

    table = pd.DataFrame(
        {
            "source_value": [10, 20],
            "class_id": [1, 2],
            "name": ["Forest", "Urban"],
        }
    )
    monkeypatch.setattr(
        lookup, "read", lambda _path: table
    )

    assert read_land_cover_class_names(
        {
            "lookup_table": "lookup.csv",
            "mapping_field": "source_value",
            "class_field": "class_id",
        }
    ) == {
        1: "Forest",
        2: "Urban",
    }


def test_missing_optional_label_column_is_not_an_error(monkeypatch):
    from mhm_qgis.core.handlers import lookup

    monkeypatch.setattr(
        lookup,
        "read",
        lambda _path: pd.DataFrame({"class_id": [1], "source_value": [10]}),
    )

    assert read_land_cover_class_names(
        {"lookup_table": "lookup.csv", "class_field": "class_id"}
    ) == {}
