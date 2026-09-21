"""Only the DEM is watershed-masked; prepared categories are final grids."""


def test_publication_is_one_command_without_crop_or_latlon_stages():
    from mhm_qgis.core.executions.morphology import PUBLICATION_STAGES
    assert [s.command for s in PUBLICATION_STAGES] == ["publish"]
