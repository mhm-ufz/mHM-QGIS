"""Tests for discharge files that do not require a valid QGIS table layer."""
from pathlib import Path

# isort: off
from mhm_qgis.core.handlers.file.discharge import (
    records_from_layer,
    streamflow_filename,
    write_streamflow_observation,
)
# isort: on


class _FileLayer:
    def __init__(self, path):
        self._path = str(path)

    def source(self):
        return self._path

    def name(self):
        return Path(self._path).name


def test_semicolon_delimited_txt_is_supported(tmp_path):
    source = tmp_path / "gauge.txt"
    source.write_text(
        "date;discharge\n2020-01-01;1.25\n2020-01-02;2.50\n",
        encoding="utf-8",
    )
    layer = _FileLayer(source)

    records = records_from_layer(layer)
    assert [record.value for record in records] == [1.25, 2.5]

    output = write_streamflow_observation(layer, "7", tmp_path / "output")
    assert output.name == "7.txt"
    assert "2020  01  02" in output.read_text(encoding="utf-8")


def test_observation_filename_preserves_leading_zeroes(tmp_path):
    source = tmp_path / "gauge.csv"
    source.write_text(
        "date,discharge\n2020-01-01,1.25\n",
        encoding="utf-8",
    )

    output = write_streamflow_observation(
        _FileLayer(source),
        "001",
        tmp_path / "output",
    )

    assert output.name == "001.txt"
    assert streamflow_filename("001") == "001.txt"


def test_observation_filename_rejects_path_components():
    import pytest

    with pytest.raises(ValueError, match="safe for a filename"):
        streamflow_filename("../7")


def test_quality_is_not_mistaken_for_a_q_discharge_column(tmp_path):
    source = tmp_path / "gauge.csv"
    source.write_text(
        "date,quality,flow_rate\n2020-01-01,9,1.25\n",
        encoding="utf-8",
    )

    records = records_from_layer(_FileLayer(source))

    assert [record.value for record in records] == [1.25]


def test_an_mhm_ready_gauge_file_reads_as_a_discharge_table(tmp_path):
    from types import SimpleNamespace

    from mhm_qgis.core.handlers.file.discharge import records_from_layer

    path = tmp_path / "280.txt"
    path.write_text("280 Gauge\nnodata -9999\nn 1\nstart 2001 01 01 00 00\nend 2001 01 02 00 00\n"
                    "2001 01 01 00 00 1.5\n2001 01 02 00 00 -9999\n", encoding="utf-8")

    records = records_from_layer(SimpleNamespace(source=lambda: str(path)))

    assert [record.timestamp.day for record in records] == [1, 2]
    assert records[0].value == 1.5
