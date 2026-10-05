"""The Admin page's Weld Tuning: weld.lua's search/press settings, saved to a file."""

import importlib
import json
import re

import pytest

import weld_tuning
from robot_service import WeldFlexRobotService
from weld_tuning import (
    DEFAULT_PRESS_GAIN,
    DEFAULT_PRESS_SPEED_MMS,
    DEFAULT_SEARCH_SPEED_MMS,
    WeldTuning,
    format_gain,
)


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "weld_tuning.json"
    monkeypatch.setattr(weld_tuning, "SETTINGS_PATH", path)
    return path


@pytest.fixture
def client(monkeypatch, settings_path):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    return importlib.import_module("app").app.test_client()


def test_nothing_saved_is_the_defaults(settings_path):
    assert weld_tuning.load() == WeldTuning(DEFAULT_SEARCH_SPEED_MMS, DEFAULT_PRESS_SPEED_MMS)


def test_a_saved_pair_loads_back(settings_path):
    weld_tuning.save(WeldTuning(9, 0.3))
    assert weld_tuning.load() == WeldTuning(9.0, 0.3)
    assert not settings_path.with_name(settings_path.name + ".tmp").exists()


def test_a_saved_press_mode_and_gain_load_back(settings_path):
    weld_tuning.save(WeldTuning(9, 0.3, "feed", 0.0002))
    assert weld_tuning.load() == WeldTuning(9.0, 0.3, "feed", 0.0002)


def test_a_file_from_before_the_press_modes_loads_as_force_only(settings_path):
    """The HMI's file on 2026-10-05 had only the two speeds. The saved press
    speed is kept for Feed mode, but the press it runs is Force only."""
    settings_path.write_text(json.dumps({"search_speed_mms": 5.0, "press_speed_mms": 0.35}))
    tuning = weld_tuning.load()
    assert tuning == WeldTuning(5.0, 0.35, "force", DEFAULT_PRESS_GAIN)
    assert tuning.press_feed_mms == 0.0


def test_the_press_feed_is_zero_unless_in_feed_mode():
    assert WeldTuning().press_mode == "force"
    assert WeldTuning(press_speed_mms=0.3).press_feed_mms == 0.0
    assert WeldTuning(press_speed_mms=0.3, press_mode="feed").press_feed_mms == 0.3


def test_an_unusable_saved_mode_or_gain_falls_back_alone(settings_path):
    settings_path.write_text(json.dumps({"press_speed_mms": 0.3, "press_mode": "fast",
                                         "press_gain": 0.01}))
    assert weld_tuning.load() == WeldTuning(DEFAULT_SEARCH_SPEED_MMS, 0.3)


def test_an_unusable_saved_speed_falls_back_alone(settings_path):
    settings_path.write_text(json.dumps({"search_speed_mms": 50, "press_speed_mms": 0.3}))
    assert weld_tuning.load() == WeldTuning(DEFAULT_SEARCH_SPEED_MMS, 0.3)


@pytest.mark.parametrize("text", ["not json", "[1, 2]", ""])
def test_an_unreadable_file_is_the_defaults(settings_path, text):
    settings_path.write_text(text)
    assert weld_tuning.load() == WeldTuning()


@pytest.mark.parametrize("search,press", [
    ("", "0.15"), ("7.5", "abc"), ("7,5", "0.15"), ("nan", "0.15"), ("7.5", "inf"),
    ("10.01", "0.15"), ("0.49", "0.15"), ("7.5", "1.01"), ("7.5", "0.049"),
])
def test_a_bad_speed_is_refused(search, press):
    with pytest.raises(ValueError):
        WeldTuning(search, press)


def test_speeds_are_kept_to_the_three_places_the_builder_writes():
    assert WeldTuning("7.5", "0.0625").press_speed_mms == 0.062


@pytest.mark.parametrize("mode,gain", [
    ("", "0.0001"), ("Force", "0.0001"), ("force", ""), ("force", "abc"), ("force", "nan"),
    ("force", "0.00004"), ("force", "0.0011"), ("feed", "0"),
])
def test_a_bad_press_mode_or_gain_is_refused(mode, gain):
    with pytest.raises(ValueError):
        WeldTuning(7.5, 0.15, mode, gain)


def test_the_gain_is_kept_to_the_six_places_it_is_written_with():
    assert WeldTuning(press_gain="0.00012345").press_gain == 0.000123
    assert format_gain(0.0001) == "0.0001"
    assert format_gain(0.00005) == "0.00005"
    assert format_gain(0.001) == "0.001"


def _field_value(page, name):
    match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', page, re.S)
    assert match, f"no {name} field on the page"
    return match.group(1)


def _selected_mode(page):
    select = re.search(r'<select id="press_mode" name="press_mode">(.*?)</select>', page, re.S)
    assert select, "no press_mode field on the page"
    selected = re.findall(r'<option value="([^"]*)" selected>', select.group(1))
    assert len(selected) == 1, selected
    return selected[0]


def test_admin_shows_saves_and_reshows_the_speeds(client, settings_path):
    page = client.get("/operator/admin").get_data(as_text=True)
    assert _field_value(page, "search_speed_mms") == "7.5"
    assert _field_value(page, "press_speed_mms") == "0.15"
    assert _field_value(page, "press_gain") == "0.0001"
    assert _selected_mode(page) == "force"

    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "9", "press_speed_mms": "0.3",
                             "press_mode": "feed", "press_gain": "0.0002"}).get_data(as_text=True)
    assert "toast-ok" in body
    assert "Search 9 mm/s, feed press 0.3 mm/s, gain 0.0002" in body
    assert weld_tuning.load() == WeldTuning(9, 0.3, "feed", 0.0002)

    page = client.get("/operator/admin").get_data(as_text=True)
    assert _field_value(page, "search_speed_mms") == "9"
    assert _field_value(page, "press_speed_mms") == "0.3"
    assert _field_value(page, "press_gain") == "0.0002"
    assert _selected_mode(page) == "feed"


def test_admin_saves_a_force_only_press(client, settings_path):
    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "5", "press_speed_mms": "0.15",
                             "press_mode": "force", "press_gain": "0.0001"}).get_data(as_text=True)
    assert "Search 5 mm/s, force-only press, gain 0.0001" in body
    assert weld_tuning.load() == WeldTuning(5, 0.15, "force", 0.0001)


def test_admin_refuses_an_out_of_range_speed_and_saves_nothing(client, settings_path):
    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "12", "press_speed_mms": "0.3",
                             "press_mode": "force", "press_gain": "0.0001"}).get_data(as_text=True)
    assert "toast-error" in body
    assert "Search speed must be 0.5 to 10 mm/s" in body
    assert not settings_path.exists()


def test_the_running_app_reads_the_saved_speeds_for_each_run(client, settings_path):
    app = importlib.import_module("app")
    weld_tuning.save(WeldTuning(4, 0.2))
    assert app.job._weld_tuning() == WeldTuning(4, 0.2)
