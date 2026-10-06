"""The introduction wizard's content: schema, effects, tips and the namelist handoff."""
import pytest

from mhm_qgis import intro

ANSWERS = {"setup_type": "single", "resolution": "standard", "gauges": "with_gauges",
           "meteo": ["pre", "tavg", "tmin", "tmax"]}


def test_schema_loads_and_unknown_effects_are_rejected():
    questions = intro.load_questions()
    assert [page["id"] for page in questions["pages"]][:2] == ["welcome", "setup_type"]
    with pytest.raises(ValueError, match="unknown effect"):
        intro._check_effect("defaults.colour")
    with pytest.raises(ValueError, match="unknown effect"):
        intro._check_effect("processes.lake")


def test_answers_give_defaults_processes_and_the_pet_method_per_version():
    questions = intro.load_questions()
    for version in ("6", "5.13"):
        effects = intro.effects(questions, ANSWERS, version)
        assert effects["defaults.work_type"] == 0
        assert effects["defaults.l1_factor"] == 40
        assert effects["processes.routing"] == 3
        assert effects["processes.pet"] == 1  # Hargreaves-Samani from tavg, tmin, tmax
        assert effects["processes.snow"] == 1
    rich = {"meteo": ["pre", "pet", "tavg", "lai"]}
    assert intro.effects(questions, rich, "6")["processes.pet"] == -1
    assert intro.effects(questions, {"meteo": ["pre", "pet"]}, "5.13")["processes.pet"] == -1


def test_missing_data_leaves_pet_unset_switches_snow_off_and_says_why():
    questions = intro.load_questions()
    effects = intro.effects(questions, {"meteo": ["pre"]}, "6")
    assert "processes.pet" not in effects
    assert effects["processes.snow"] == 0
    tips = intro.tips(questions, {"meteo": ["pre"]}, "6")
    assert any("No PET method fits" in tip for tip in tips)
    assert any("Snow is switched off" in tip for tip in tips)


def test_summary_names_the_methods_and_the_forcing():
    effects = intro.effects(intro.load_questions(), ANSWERS, "6")
    lines = intro.summary("6", effects)
    assert "PET case 1 (Hargreaves-Samani)" in lines
    assert "Forcing you will prepare: pre, tavg, tmin, tmax" in lines


def test_handoff_v6_and_v513():
    questions = intro.load_questions()
    v6 = intro.handoff("6", intro.effects(questions, ANSWERS, "6"))
    assert v6["main"]["config_processes"] == {"routing": 3, "pet": 1, "snow": 1}
    assert v6["output"]["output_mhm"]["out_satSTW"] is True
    saved = {"processselection": {"processcase": [1, 1, 1, 1, -1, 1, 1, 1, 1, 0, 0]}}
    v5 = intro.handoff("5.13", intro.effects(questions, ANSWERS, "5.13"), saved)
    assert v5["main"]["processSelection"]["processCase"] == [1, 1, 1, 1, 1, 1, 1, 3, 1, 0, 0]
    flags = v5["output"]["NLoutputResults"]["outputFlxState"]
    assert len(flags) == 21
    assert flags[7] is True  # satSTW, switch 8


def test_show_if_hides_a_page_until_its_answer_is_given():
    page = {"id": "extra", "show_if": {"gauges": ["with_gauges"]}}
    assert intro.visible(page, {"gauges": "with_gauges"})
    assert not intro.visible(page, {"gauges": "no_gauges"})
    assert intro.visible({"id": "plain"}, {})
