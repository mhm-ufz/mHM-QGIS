"""Saved namelists are read through our schemas into lower-case, typed values."""
import pytest

pytest.importorskip("nml_tools.gui.model")

from mhm_qgis.core.handlers.store.paths import schemas_folder
from mhm_qgis.externals.nml_tools_handler import read_namelists


def test_v513_multi_domain_arrays_and_processes(tmp_path):
    (tmp_path / "mhm.nml").write_text(
        "&mainconfig\n  nDomains = 2\n/\n"
        "&processSelection\n  processCase = 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0\n/\n"
        '&directories_general\n  dir_Morpho(1) = "a/morph/"\n  dir_Morpho(2) = "b/morph/"\n/\n'
        "&LCover_MPR\n  fracSealed_cityArea = 0.6\n/\n"
    )
    values = read_namelists(tmp_path, schemas_folder("5.13"))
    assert values["mainconfig"]["ndomains"] == 2
    assert values["processselection"]["processcase"] == [1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0]
    assert values["directories_general"]["dir_morpho"] == ["a/morph/", "b/morph/"]
    assert values["lcover_mpr"]["fracsealed_cityarea"] == 0.6


def test_v6_processes_and_per_domain_sealed_fraction(tmp_path):
    (tmp_path / "mhm.nml").write_text(
        "&config_processes\n  pet = 1\n  routing = 1\n/\n"
        "&config_mpr\n  fracSealed_cityArea = 0.5\n/\n"
    )
    values = read_namelists(tmp_path, schemas_folder("6"))
    assert values["config_processes"] == {"pet": 1, "routing": 1}
    assert values["config_mpr"]["fracsealed_cityarea"] == [0.5]


def test_no_saved_namelist_reads_empty_and_a_broken_one_names_the_folder(tmp_path):
    assert read_namelists(tmp_path, schemas_folder("6")) == {}
    (tmp_path / "mhm.nml").write_text("&config_processes\n  pet = \n")
    with pytest.raises(ValueError, match="Could not read the namelists"):
        read_namelists(tmp_path, schemas_folder("6"))
