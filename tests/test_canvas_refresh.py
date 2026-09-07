"""A display name must not hide a changed source or rewritten file."""
from mhm_qgis.qgis_bridge.display import canvas


def test_reuses_layer_but_refreshes_replaced_sources(tmp_path, monkeypatch):
    class Layer:
        def __init__(self):
            self.uri = ""
            self.properties = {}
            self.reloads = 0

        def source(self):
            return self.uri

        def providerType(self):
            return "gdal"

        def setDataSource(self, uri, name, provider):
            self.uri = uri
            self.reloads += 1

        def customProperty(self, key, default):
            return self.properties.get(key, default)

        def setCustomProperty(self, key, value):
            self.properties[key] = value

        def triggerRepaint(self):
            pass

    layer = Layer()
    def refresh(layer, source, name, **kwargs):
        layer.setDataSource(source, name, "gdal")
        return layer

    monkeypatch.setattr(canvas.layers, "find_by_name", lambda name: layer)
    monkeypatch.setattr(canvas.layers, "refresh_source", refresh)
    first, second = tmp_path / "2015.asc", tmp_path / "2016.asc"
    first.write_text("first")
    second.write_text("second")
    for path in (first, first, second, second):
        assert canvas.show_raster(None, path, "Land Cover") is layer
    assert layer.reloads == 2
    second.write_text("rewritten data")
    canvas.show_raster(None, second, "Land Cover")
    assert layer.reloads == 3


def test_returning_to_first_netcdf_band_selects_band_one(monkeypatch):
    from mhm_qgis.qgis_bridge.display import morphology
    from mhm_qgis.core.handlers.store.morphology import DisplayOutput
    from pathlib import Path

    bands = []
    monkeypatch.setattr(morphology, "show_raster", lambda *args, **kwargs: bands.append(kwargs["band"]))
    for band in (3, 1):
        morphology.show_output(None, DisplayOutput(Path("lc_periods.nc"), "Land Cover", band=band))
    assert bands == [3, 1]
