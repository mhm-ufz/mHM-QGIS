"""Fingerprinted reuse must skip rework only when the inputs are unchanged."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.handlers.state.cache import (  # noqa: E402
    cached_payload,
    fingerprint,
    load_state,
    outputs_present,
    state_path,
    store_payload,
)


def _input(tmp_path, name, text="a"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_fingerprint_tracks_size_and_mtime_not_content(tmp_path):
    first = _input(tmp_path, "one.tif")
    second = _input(tmp_path, "two.tif")
    digest = fingerprint([first, second])

    # Same files, same digest; order must not matter.
    assert fingerprint([second, first]) == digest
    # A changed size invalidates it.
    first.write_text("aa", encoding="utf-8")
    assert fingerprint([first, second]) != digest
    # So does a changed mtime at the same size.
    first.write_text("a", encoding="utf-8")
    os.utime(first, (1_000_000, 1_000_000))
    assert fingerprint([first, second]) != digest


def test_a_deleted_input_invalidates_rather_than_matching(tmp_path):
    path = _input(tmp_path, "one.tif")
    digest = fingerprint([path])
    path.unlink()
    assert fingerprint([path]) != digest
    # And a missing path is not the same as an absent path.
    assert fingerprint([path]) != fingerprint([])


def test_configuration_is_part_of_the_fingerprint(tmp_path):
    path = _input(tmp_path, "one.tif")
    base = fingerprint([path], {"method": "bilinear"})
    assert fingerprint([path], {"method": "nearest"}) != base
    assert fingerprint([path], {"method": "bilinear"}) == base


def test_a_payload_is_returned_only_for_a_matching_fingerprint(tmp_path):
    digest = fingerprint([_input(tmp_path, "in.tif")])
    store_payload(tmp_path, "stages", "geology", digest, {"outputs": ["a.asc"]})

    assert cached_payload(tmp_path, "stages", "geology", digest) == {
        "outputs": ["a.asc"]
    }
    assert cached_payload(tmp_path, "stages", "geology", "other") is None
    assert cached_payload(tmp_path, "stages", "lai", digest) is None


def test_stored_entries_share_one_state_file_without_clobbering(tmp_path):
    store_payload(tmp_path, "stages", "geology", "d1", {"outputs": ["g"]})
    store_payload(tmp_path, "stages", "lai", "d2", {"outputs": ["l"]})
    store_payload(tmp_path, "meteo_inspection", "pre", "d3", {"files": []})

    state = load_state(tmp_path)
    assert sorted(state["stages"]) == ["geology", "lai"]
    assert state["meteo_inspection"]["pre"]["fingerprint"] == "d3"
    assert state["stages"]["geology"]["payload"] == {"outputs": ["g"]}
    assert "updated_at" in state["stages"]["lai"]
    assert state_path(tmp_path).is_file()


def test_an_existing_state_file_is_preserved(tmp_path):
    store_payload(tmp_path, "stages", "geology", "d1", {"outputs": ["g"]})
    state = load_state(tmp_path)
    state["outputs"] = {"keep/me": {"exists": True}}
    from mhm_qgis.core.handlers.state.cache import save_state

    save_state(tmp_path, state)
    store_payload(tmp_path, "stages", "lai", "d2", {"outputs": ["l"]})

    reloaded = load_state(tmp_path)
    assert reloaded["outputs"] == {"keep/me": {"exists": True}}
    assert sorted(reloaded["stages"]) == ["geology", "lai"]


def test_outputs_present_requires_every_file_on_disk(tmp_path):
    first = _input(tmp_path, "one.asc")
    assert outputs_present({"outputs": [str(first)]})
    assert not outputs_present({"outputs": [str(first), str(tmp_path / "gone")]})
    assert not outputs_present({"outputs": []})
    assert not outputs_present(None)


def test_a_corrupt_state_file_is_treated_as_empty(tmp_path):
    path = state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_state(tmp_path) == {}
    assert cached_payload(tmp_path, "stages", "geology", "d") is None
    # Storing still works and repairs the file.
    store_payload(tmp_path, "stages", "geology", "d", {"outputs": ["g"]})
    assert load_state(tmp_path)["stages"]["geology"]["fingerprint"] == "d"


def test_outputs_are_adoptable_only_when_they_postdate_their_inputs(tmp_path):
    from mhm_qgis.core.handlers.state.cache import outputs_newer_than_inputs

    source = _input(tmp_path, "in.tif")
    output = _input(tmp_path, "out.asc")
    base = output.stat().st_mtime

    # Output written after the input: plausibly current, so adoptable.
    os.utime(source, (base - 10, base - 10))
    assert outputs_newer_than_inputs([source], [output])

    # Input edited after the output: certainly stale.
    os.utime(source, (base + 10, base + 10))
    assert not outputs_newer_than_inputs([source], [output])

    # A missing output or input is never adoptable.
    assert not outputs_newer_than_inputs([source], [tmp_path / "gone"])
    assert not outputs_newer_than_inputs([tmp_path / "gone"], [output])
    assert not outputs_newer_than_inputs([source], [])


def test_the_oldest_output_decides_adoptability(tmp_path):
    from mhm_qgis.core.handlers.state.cache import outputs_newer_than_inputs

    source = _input(tmp_path, "in.tif")
    fresh = _input(tmp_path, "fresh.asc")
    stale = _input(tmp_path, "stale.txt")
    base = fresh.stat().st_mtime
    os.utime(source, (base - 10, base - 10))
    os.utime(stale, (base - 20, base - 20))

    # One output older than the input blocks adoption of the whole stage.
    assert not outputs_newer_than_inputs([source], [fresh, stale])
    os.utime(stale, (base, base))
    assert outputs_newer_than_inputs([source], [fresh, stale])
