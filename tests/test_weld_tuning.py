"""The Admin page's Weld Tuning: weld.lua's search/press speeds, saved to a file."""

import importlib
import json
import re

import pytest

import weld_tuning
from robot_service import WeldFlexRobotService
from weld_tuning import (
    DEFAULT_PRESS_SPEED_MMS,
    DEFAULT_SEARCH_SPEED_MMS,
    WeldTuning,
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


def _field_value(page, name):
    match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', page, re.S)
    assert match, f"no {name} field on the page"
    return match.group(1)


def test_admin_shows_saves_and_reshows_the_speeds(client, settings_path):
    page = client.get("/operator/admin").get_data(as_text=True)
    assert _field_value(page, "search_speed_mms") == "7.5"
    assert _field_value(page, "press_speed_mms") == "0.15"

    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "9", "press_speed_mms": "0.3"}).get_data(as_text=True)
    assert "toast-ok" in body
    assert "Search 9 mm/s, press 0.3 mm/s" in body
    assert weld_tuning.load() == WeldTuning(9, 0.3)

    page = client.get("/operator/admin").get_data(as_text=True)
    assert _field_value(page, "search_speed_mms") == "9"
    assert _field_value(page, "press_speed_mms") == "0.3"


def test_admin_refuses_an_out_of_range_speed_and_saves_nothing(client, settings_path):
    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "12", "press_speed_mms": "0.3"}).get_data(as_text=True)
    assert "toast-error" in body
    assert "Search speed must be 0.5 to 10 mm/s" in body
    assert not settings_path.exists()


def test_the_running_app_reads_the_saved_speeds_for_each_run(client, settings_path):
    app = importlib.import_module("app")
    weld_tuning.save(WeldTuning(4, 0.2))
    assert app.job._weld_tuning() == WeldTuning(4, 0.2)
