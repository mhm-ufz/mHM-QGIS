"""Filesystem-safe matching for loaded QGIS layer sources."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from mhm_qgis.qgis_bridge.layers import project as layer_project


@pytest.mark.skipif(
    os.path.normcase("A.tif") == os.path.normcase("a.tif"),
    reason="The platform filesystem is case-insensitive",
)
def test_source_normalization_preserves_case_on_case_sensitive_systems(tmp_path):
    upper = tmp_path / "A.tif"
    lower = tmp_path / "a.tif"
    upper.touch()
    lower.touch()

    assert layer_project.normalized_source(upper) != layer_project.normalized_source(lower)


def test_remove_under_does_not_match_a_sibling_prefix(tmp_path, monkeypatch):
    folder = tmp_path / "morph"
    sibling = tmp_path / "morph-backup"
    folder.mkdir()
    sibling.mkdir()
    layers = {
        "inside": SimpleNamespace(source=lambda: str(folder / "dem.tif"), name=lambda: "DEM"),
        "sibling": SimpleNamespace(source=lambda: str(sibling / "dem.tif"), name=lambda: "Backup"),
    }
    removed = []
    project = SimpleNamespace(
        mapLayers=lambda: layers,
        removeMapLayer=removed.append,
    )
    monkeypatch.setattr(layer_project, "_project", lambda: project)

    assert layer_project.remove_under(folder) == 1
    assert removed == ["inside"]
