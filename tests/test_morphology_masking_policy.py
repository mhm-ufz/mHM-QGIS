"""Only the derivative bundle is watershed-masked; categories are published intact."""
from pathlib import Path
from mhm_qgis.core.morphology.layers.advanced_l0 import pad_spec_for


def test_categorical_and_lai_padding():
    for name in ("soil_class.asc", "geology_class.asc", "lc_2000.asc"):
        assert pad_spec_for(Path(name)) == (1, True)
    assert pad_spec_for(Path("lai.nc")) == (0.0, False)


def test_no_second_crop_workflow():
    from mhm_qgis.core.executions.morphology import PUBLICATION_STAGES
    assert [s.command for s in PUBLICATION_STAGES] == ["mask", "publish", "latlon", "write"]
