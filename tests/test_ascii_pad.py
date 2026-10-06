"""Padding an ASCII grid must expand it exactly, with flat memory."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.handlers.file.ascii.pad import (
    ascii_window_offsets,
    pad_ascii_grid,
    read_ascii_header,
)


def _grid(path, ncols=2, nrows=2, xll=100.0, yll=200.0, cell=10.0, start=1):
    rows = []
    value = start
    for _ in range(nrows):
        rows.append(" ".join(str(value + column) for column in range(ncols)))
        value += ncols
    path.write_text(
        f"ncols         {ncols}\n"
        f"nrows         {nrows}\n"
        f"xllcorner     {xll}\n"
        f"yllcorner     {yll}\n"
        f"cellsize      {cell}\n"
        f"NODATA_value  -9999\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def _rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()[6:]
    return [line.split() for line in lines if line.strip()]


def test_header_is_read_including_centre_variants(tmp_path):
    path = _grid(tmp_path / "a.asc")
    header = read_ascii_header(path)
    assert header["ncols"] == 2 and header["nrows"] == 2
    assert header["xllcorner"] == 100.0 and header["cellsize"] == 10.0

    centred = tmp_path / "b.asc"
    centred.write_text(
        "ncols 1\nnrows 1\nxllcenter 105.0\nyllcenter 205.0\ncellsize 10.0\n"
        "NODATA_value -9999\n1\n",
        encoding="utf-8",
    )
    header = read_ascii_header(centred)
    assert header["xllcorner"] == 100.0 and header["yllcorner"] == 200.0


def test_a_file_without_a_header_is_rejected(tmp_path):
    path = tmp_path / "bad.asc"
    path.write_text("1 2 3\n4 5 6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="readable Arc/Info ASCII header"):
        read_ascii_header(path)


def test_offsets_locate_the_source_inside_the_target():
    source = {"ncols": 2, "nrows": 2, "xllcorner": 100.0, "yllcorner": 200.0,
              "cellsize": 10.0}
    # Target extends one cell left/right and one row above/below.
    target = {"ncols": 4, "nrows": 4, "xllcorner": 90.0, "yllcorner": 190.0,
              "cellsize": 10.0}
    assert ascii_window_offsets(source, target) == (1, 1)

    with pytest.raises(ValueError, match="does not match the target cell size"):
        ascii_window_offsets(source, {**target, "cellsize": 20.0})
    with pytest.raises(ValueError, match="not aligned to the target"):
        ascii_window_offsets({**source, "xllcorner": 103.0}, target)


def test_padding_surrounds_the_source_with_nodata(tmp_path):
    path = _grid(tmp_path / "a.asc")          # values 1 2 / 3 4
    target = {"ncols": 4, "nrows": 4, "xllcorner": 90.0, "yllcorner": 190.0,
              "cellsize": 10.0}

    pad_ascii_grid(path, target)

    header = read_ascii_header(path)
    assert (header["ncols"], header["nrows"]) == (4, 4)
    assert header["xllcorner"] == 90.0 and header["yllcorner"] == 190.0
    assert _rows(path) == [
        ["-9999", "-9999", "-9999", "-9999"],
        ["-9999", "1", "2", "-9999"],
        ["-9999", "3", "4", "-9999"],
        ["-9999", "-9999", "-9999", "-9999"],
    ]


def test_an_already_matching_grid_is_left_untouched(tmp_path):
    path = _grid(tmp_path / "a.asc")
    before = path.read_bytes()
    target = {"ncols": 2, "nrows": 2, "xllcorner": 100.0, "yllcorner": 200.0,
              "cellsize": 10.0}

    assert pad_ascii_grid(path, target) == path
    assert path.read_bytes() == before


def test_aligned_cropping_and_partial_overlap(tmp_path):
    path = _grid(tmp_path / "a.asc", ncols=4, nrows=4)
    smaller = {"ncols": 2, "nrows": 2, "xllcorner": 100.0, "yllcorner": 200.0,
               "cellsize": 10.0}
    pad_ascii_grid(path, smaller)
    assert _rows(path) == [["9", "10"], ["13", "14"]]
    pad_ascii_grid(path, {**smaller, "xllcorner": 110.0, "yllcorner": 190.0}, pad=1)
    assert _rows(path) == [["14", "1"], ["1", "1"]]


def test_a_row_count_mismatch_is_reported_and_leaves_the_file_intact(tmp_path):
    path = tmp_path / "short.asc"
    path.write_text(
        "ncols 2\nnrows 3\nxllcorner 100.0\nyllcorner 200.0\ncellsize 10.0\n"
        "NODATA_value -9999\n1 2\n3 4\n",          # header claims 3 rows
        encoding="utf-8",
    )
    before = path.read_bytes()
    target = {"ncols": 4, "nrows": 5, "xllcorner": 90.0, "yllcorner": 180.0,
              "cellsize": 10.0}
    with pytest.raises(ValueError, match="declares 3"):
        pad_ascii_grid(path, target)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".*.pad.tmp"))


def test_the_a26_geometry_pads_to_the_common_extent(tmp_path):
    """The real case: a DEM-sized soil grid expanded to the L0 extent."""
    cell = 0.0008333333333333333
    path = _grid(tmp_path / "soil_class.asc", ncols=3, nrows=2,
                 xll=78.99958333333333, yll=31.000416666666666 - 2 * cell,
                 cell=cell)
    target = {
        "ncols": 5, "nrows": 4,
        "xllcorner": 78.99958333333333,
        "yllcorner": 31.000416666666666 - 2 * cell - cell,
        "cellsize": cell,
    }
    pad_ascii_grid(path, target)

    rows = _rows(path)
    assert len(rows) == 4 and all(len(row) == 5 for row in rows)
    # One padded row on top, the data, then one below; two padded columns right.
    assert rows[0] == ["-9999"] * 5
    assert rows[1][:3] == ["1", "2", "3"] and rows[1][3:] == ["-9999", "-9999"]
    assert rows[3] == ["-9999"] * 5


def test_a_pad_value_fills_the_expansion_and_leaves_the_nodata_declared(tmp_path):
    """Class grids pad with a valid class while NODATA_value stays -9999."""
    path = _grid(tmp_path / "lc.asc")
    target = {
        "ncols": 4, "nrows": 4, "xllcorner": 90.0, "yllcorner": 190.0,
        "cellsize": 10.0,
    }

    pad_ascii_grid(path, target, nodata=-9999, pad=1)

    header = path.read_text(encoding="utf-8").splitlines()[:6]
    assert header[5].split() == ["NODATA_value", "-9999"]
    rows = _rows(path)
    assert rows[0] == ["1", "1", "1", "1"]
    assert rows[3] == ["1", "1", "1", "1"]
    assert rows[1] == ["1", "1", "2", "1"]
    assert rows[2] == ["1", "3", "4", "1"]
