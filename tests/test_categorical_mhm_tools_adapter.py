"""Tests for the unified path-based categorical mhm-tools adapter."""

import contextlib
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from mhm_qgis.core.handlers.lookup import table as lookup_table
from mhm_qgis.externals import mhm_tools_handler as categorical


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _install_fake_mhm_tools(monkeypatch, **functions):
    package = ModuleType("mhm_tools")
    pre = ModuleType("mhm_tools.pre")
    for name, function in functions.items():
        setattr(pre, name, function)
    package.pre = pre
    # The fake shadows the installed package, so the CSV reader the adapter
    # reaches for through `lookup.read` has to be present too.
    common = ModuleType("mhm_tools.common")
    lookup_handler = ModuleType("mhm_tools.common.lookup_handler")
    lookup_handler.read_lookup_table = lambda path: pd.read_csv(path)
    common.lookup_handler = lookup_handler
    package.common = common
    monkeypatch.setitem(sys.modules, "mhm_tools", package)
    monkeypatch.setitem(sys.modules, "mhm_tools.pre", pre)
    monkeypatch.setitem(sys.modules, "mhm_tools.common", common)
    monkeypatch.setitem(
        sys.modules, "mhm_tools.common.lookup_handler", lookup_handler
    )


def _capture_counter(monkeypatch):
    calls = []

    @contextlib.contextmanager
    def fake_capture(log):
        calls.append(log)
        yield

    monkeypatch.setattr(categorical, "capture_messages", fake_capture)
    return calls


def test_raster_soil_formats_and_moves_canonical_outputs(tmp_path, monkeypatch):
    input_file = _touch(tmp_path / "input" / "soil.asc")
    dem_file = _touch(tmp_path / "input" / "dem.tif")
    lookup_table = _touch(tmp_path / "input" / "soil.csv")
    output_file = tmp_path / "geometry" / "soil_class.nc"
    definition_file = tmp_path / "morph" / "soil_classdefinition.txt"
    formatter_calls = []

    def format_soil_data(**kwargs):
        formatter_calls.append(kwargs)
        kwargs["output_path"].joinpath("soil_class.nc").write_bytes(b"soil")
        kwargs["output_path"].joinpath("soil_classdefinition.txt").write_text(
            "definition\n", encoding="utf-8"
        )

    _install_fake_mhm_tools(monkeypatch, format_soil_data=format_soil_data)
    captures = _capture_counter(monkeypatch)
    log = lambda _message: None

    result = categorical.prepare_categorical_file(
        "soil",
        input_file,
        dem_file,
        output_file,
        lookup_table,
        "map_code",
        "SOIL_CLASS",
        classdefinition_file=definition_file,
        input_crs="EPSG:4326",
        dem_crs="EPSG:32645",
        log=log,
    )

    assert result == output_file
    assert output_file.read_bytes() == b"soil"
    assert definition_file.read_text(encoding="utf-8") == "definition\n"
    assert captures == [log]
    assert len(formatter_calls) == 1
    call = formatter_calls[0]
    temporary_path = call.pop("output_path")
    assert temporary_path.parent == output_file.parent
    assert call == {
        "input_file": input_file,
        "dem_file": dem_file,
        "lookup_table": lookup_table,
        "mapping_field": "map_code",
        "class_field": "SOIL_CLASS",
        "output_type": "nc",
        "input_crs": "EPSG:4326",
        "dem_crs": "EPSG:32645",
    }
    assert not temporary_path.exists()


def test_vector_geology_rasterizes_then_uses_identity_mapping(tmp_path, monkeypatch):
    input_file = _touch(tmp_path / "input" / "geology.shp")
    dem_file = _touch(tmp_path / "input" / "dem.asc")
    lookup_table = _touch(tmp_path / "input" / "geology.csv")
    output_file = tmp_path / "geometry" / "geology_class.nc"
    definition_file = tmp_path / "morph" / "geology_classdefinition.txt"
    rasterize_calls = []
    formatter_calls = []

    # Signature copied from mhm_tools.pre.rasterize_map_data: `burn_field` is
    # positional-or-keyword and required, so a stale call shape fails here
    # instead of in QGIS.
    def rasterize_map_data(
        input_file, dem_file, output_file, burn_field, **kwargs
    ):
        rasterize_calls.append(
            dict(
                kwargs,
                input_file=input_file,
                dem_file=dem_file,
                output_file=output_file,
                burn_field=burn_field,
            )
        )
        output_file.write_bytes(b"rasterized")

    def format_geology_data(**kwargs):
        formatter_calls.append(kwargs)
        assert kwargs["input_file"].read_bytes() == b"rasterized"
        kwargs["output_path"].joinpath("geology_class.nc").write_bytes(b"geology")
        kwargs["output_path"].joinpath("geology_classdefinition.txt").write_text(
            "definition\n", encoding="utf-8"
        )

    _install_fake_mhm_tools(
        monkeypatch,
        rasterize_map_data=rasterize_map_data,
        format_geology_data=format_geology_data,
    )
    captures = _capture_counter(monkeypatch)

    categorical.prepare_categorical_file(
        "geology",
        input_file,
        dem_file,
        output_file,
        lookup_table,
        "Class",
        "GEOLOGY_CLASS",
        is_vector=True,
        classdefinition_file=definition_file,
        input_crs="EPSG:4326",
        dem_crs="EPSG:32645",
    )

    assert output_file.read_bytes() == b"geology"
    assert definition_file.read_text(encoding="utf-8") == "definition\n"
    assert captures == [None]
    assert len(rasterize_calls) == len(formatter_calls) == 1
    rasterize_call = rasterize_calls[0]
    temporary_input = rasterize_call.pop("output_file")
    # Lookup-based preparation burns the class field and joins on the mapping
    # field, both passed through under the names the lookup table uses.
    assert rasterize_call == {
        "input_file": input_file,
        "dem_file": dem_file,
        "burn_field": "GEOLOGY_CLASS",
        "lookup_table": lookup_table,
        "mapping_field": "Class",
        "input_crs": "EPSG:4326",
        "dem_crs": "EPSG:32645",
    }
    formatter_call = formatter_calls[0]
    assert formatter_call["input_file"] == temporary_input
    assert formatter_call["output_path"] == temporary_input.parent
    assert "input_crs" not in formatter_call
    assert formatter_call == {
        "input_file": temporary_input,
        "dem_file": dem_file,
        "output_path": temporary_input.parent,
        "lookup_table": lookup_table,
        "mapping_field": "GEOLOGY_CLASS",
        "class_field": "GEOLOGY_CLASS",
        "output_type": "nc",
        "dem_crs": "EPSG:32645",
    }
    assert not temporary_input.parent.exists()


def test_vector_mapping_field_is_inferred_from_lookup_values(monkeypatch):
    import geopandas as gpd

    monkeypatch.setattr(
        gpd,
        "read_file",
        lambda *_args, **_kwargs: pd.DataFrame(
            {"identity": ["a", "b"], "xx": ["su", "ss"]}
        ),
    )
    monkeypatch.setattr(
        lookup_table,
        "read",
        lambda _path: pd.DataFrame({"Class": ["su", "ss", "wb"]}),
    )

    assert (
        lookup_table.resolve_vector_mapping_field("input.shp", "lookup.csv", "Class")
        == "xx"
    )


def test_land_cover_dispatches_without_classdefinition(tmp_path, monkeypatch):
    input_file = _touch(tmp_path / "input" / "lc.nc")
    dem_file = _touch(tmp_path / "input" / "dem.nc")
    lookup_table = tmp_path / "input" / "lc.txt"
    lookup_table.parent.mkdir(parents=True, exist_ok=True)
    lookup_table.write_text(
        "grid_value\tLC_CLASS\n1\t2\n",
        encoding="utf-8",
    )
    output_file = tmp_path / "geometry" / "lc.nc"
    formatter_calls = []
    lookup_text = []

    def format_lc_data(**kwargs):
        formatter_calls.append(kwargs)
        lookup_text.append(kwargs["lookup_table"].read_text(encoding="utf-8"))
        kwargs["output_path"].joinpath("lc.nc").write_bytes(b"land cover")

    _install_fake_mhm_tools(monkeypatch, format_lc_data=format_lc_data)

    categorical.prepare_categorical_file(
        "lc",
        input_file,
        dem_file,
        output_file,
        lookup_table,
        "grid_value",
        "LC_CLASS",
    )

    assert output_file.read_bytes() == b"land cover"
    call = formatter_calls[0]
    temporary_path = call.pop("output_path")
    normalized_lookup = call.pop("lookup_table")
    assert call == {
        "input_file": input_file,
        "dem_file": dem_file,
        "mapping_field": "grid_value",
        "class_field": "LC_CLASS",
        "output_type": "nc",
    }
    # `as_csv` owns a scratch subdirectory rather than writing into the
    # directory it is handed -- the other call site hands it a real output
    # folder, which must not be left with a stray lookup.csv.
    assert normalized_lookup.name == "lookup.csv"
    assert temporary_path in normalized_lookup.parents
    assert lookup_text == ["grid_value,LC_CLASS\n1,2\n"]
    assert not normalized_lookup.exists()


def test_rejects_unknown_kind_and_land_cover_definition(tmp_path):
    common = (
        tmp_path / "input.tif",
        tmp_path / "dem.tif",
        tmp_path / "output.nc",
        tmp_path / "lookup.csv",
        "source",
        "target",
    )
    with pytest.raises(ValueError, match="kind must"):
        categorical.prepare_categorical_file("water", *common)
    with pytest.raises(ValueError, match="does not write"):
        categorical.prepare_categorical_file(
            "lc", *common, classdefinition_file=tmp_path / "definition.txt"
        )


def test_rejects_an_output_name_that_misstates_the_written_format(tmp_path):
    """mhm-tools writes GeoTIFF; a `.asc` name reaches publication as text."""
    assert categorical.categorical_output_suffix("geology") == ".nc"
    with pytest.raises(ValueError, match="must be named .nc"):
        categorical.prepare_categorical_file(
            "geology",
            tmp_path / "input.tif",
            tmp_path / "dem.tif",
            tmp_path / "geology_class.asc",
            tmp_path / "lookup.csv",
            "source",
            "target",
        )
