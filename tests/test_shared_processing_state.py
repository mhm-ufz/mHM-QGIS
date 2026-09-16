"""The processing state has three writers; none may erase another's sections."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mhm_qgis.core.handlers.file import json as jsonio
from mhm_qgis.core.handlers.state import cache
from mhm_qgis.core.handlers.state import processing
from mhm_qgis.core.handlers.state.meteo_outputs import MeteorologyOutputState


class _Dialog:
    def __init__(self, project_folder):
        self.project_folder = str(project_folder)
        self.messages = []

    def log_message(self, message):
        self.messages.append(message)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "mhm-qgis").mkdir()
    # mark_prepared only records outputs that exist on disk.
    for name in ("pre.nc", "tavg.nc"):
        (tmp_path / name).write_bytes(b"")
    return tmp_path


def test_a_stale_meteorology_copy_does_not_erase_a_newer_cache_write(project):
    """The real exposure is read-modify-write, not a plain overwrite.

    A tight load-then-save round trip preserved everything, because load() read
    the whole file. What loses data is another writer landing in between: the
    stale in-memory copy is then written back over the newer sections.
    """
    state = MeteorologyOutputState(project)

    held = state.load()                                       # meteorology reads
    cache.store_payload(project, "stages", "lai", "d", {})    # cache writes
    state.save(held)                                          # stale copy goes back

    assert cache.cached_payload(project, "stages", "lai", "d") is not None


def test_the_cache_keeps_the_meteorology_outputs(project):
    dialog = _Dialog(project)
    state = MeteorologyOutputState(dialog.project_folder)
    state.mark_prepared(project / "pre.nc", "pre.nc")
    before = jsonio.read_mapping(cache.state_path(project)).get("outputs")
    assert before

    cache.store_payload(project, "stages", "geology", "digest-2", {"outputs": []})

    assert jsonio.read_mapping(cache.state_path(project)).get("outputs") == before


def test_unrelated_sections_written_by_anyone_else_survive(project):
    path = cache.state_path(project)
    jsonio.write(path, {"workflows": {"execute_all": "done"}, "grid": {"ncols": 10}})

    cache.store_payload(project, "stages", "soil", "digest-3", {"outputs": []})
    MeteorologyOutputState(project).mark_prepared(project / "tavg.nc")

    state = jsonio.read_mapping(path)
    assert state["workflows"] == {"execute_all": "done"}
    assert state["grid"] == {"ncols": 10}
    assert "stages" in state and "outputs" in state


def test_parallel_updates_to_one_section_do_not_lose_entries(project):
    def record(index):
        processing.set_entry(
            project, "stages", f"stage-{index}", {"value": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(40)))

    stages = processing.section(project, "stages")
    assert stages == {
        f"stage-{index}": {"value": index} for index in range(40)
    }
