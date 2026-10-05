"""The Admin page's Weld Tuning: weld.lua's search speed and press gain, saved to a file."""

import importlib
import json
import re

import pytest

import weld_tuning
from robot_service import WeldFlexRobotService
from weld_tuning import (
    DEFAULT_PRESS_GAIN,
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
    assert weld_tuning.load() == WeldTuning(DEFAULT_SEARCH_SPEED_MMS, DEFAULT_PRESS_GAIN)


def test_the_default_gain_is_the_commissioned_one():
    """0.0003 pressed smoothly to 16 lbf live and dry on 2026-10-05."""
    assert DEFAULT_PRESS_GAIN == 0.0003


def test_a_saved_pair_loads_back(settings_path):
    weld_tuning.save(WeldTuning(9, 0.0002))
    assert weld_tuning.load() == WeldTuning(9.0, 0.0002)
    assert not settings_path.with_name(settings_path.name + ".tmp").exists()


def test_a_file_with_the_retired_press_keys_still_loads(settings_path):
    """The HMI's file on 2026-10-05 still had the press speed and mode."""
    settings_path.write_text(json.dumps({"search_speed_mms": 5.0, "press_speed_mms": 0.15,
                                         "press_mode": "force", "press_gain": 0.0003}))
    assert weld_tuning.load() == WeldTuning(5.0, 0.0003)
    weld_tuning.save(weld_tuning.load())
    assert json.loads(settings_path.read_text()) == {"search_speed_mms": 5.0, "press_gain": 0.0003}


def test_an_unusable_saved_value_falls_back_alone(settings_path):
    settings_path.write_text(json.dumps({"search_speed_mms": 50, "press_gain": 0.0002}))
    assert weld_tuning.load() == WeldTuning(DEFAULT_SEARCH_SPEED_MMS, 0.0002)
    settings_path.write_text(json.dumps({"search_speed_mms": 9, "press_gain": 0.01}))
    assert weld_tuning.load() == WeldTuning(9, DEFAULT_PRESS_GAIN)


@pytest.mark.parametrize("text", ["not json", "[1, 2]", ""])
def test_an_unreadable_file_is_the_defaults(settings_path, text):
    settings_path.write_text(text)
    assert weld_tuning.load() == WeldTuning()


@pytest.mark.parametrize("search,gain", [
    ("", "0.0003"), ("7,5", "0.0003"), ("nan", "0.0003"), ("10.01", "0.0003"), ("0.49", "0.0003"),
    ("7.5", ""), ("7.5", "abc"), ("7.5", "inf"), ("7.5", "0.00004"), ("7.5", "0.0011"), ("7.5", "0"),
])
def test_a_bad_value_is_refused(search, gain):
    with pytest.raises(ValueError):
        WeldTuning(search, gain)


def test_values_are_kept_to_the_places_the_builder_writes():
    assert WeldTuning("7.0625").search_speed_mms == 7.062
    assert WeldTuning(press_gain="0.00012345").press_gain == 0.000123
    assert format_gain(0.0003) == "0.0003"
    assert format_gain(0.00005) == "0.00005"
    assert format_gain(0.001) == "0.001"


def _field_value(page, name):
    match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', page, re.S)
    assert match, f"no {name} field on the page"
    return match.group(1)


def test_admin_shows_saves_and_reshows_the_settings(client, settings_path):
    page = client.get("/operator/admin").get_data(as_text=True)
    assert _field_value(page, "search_speed_mms") == "7.5"
    assert _field_value(page, "press_gain") == "0.0003"
    assert 'name="press_speed_mms"' not in page
    assert 'name="press_mode"' not in page

    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "9", "press_gain": "0.0002"}).get_data(as_text=True)
    assert "toast-ok" in body
    assert "Search 9 mm/s, press gain 0.0002" in body
    assert weld_tuning.load() == WeldTuning(9, 0.0002)

    page = client.get("/operator/admin").get_data(as_text=True)
    assert _field_value(page, "search_speed_mms") == "9"
    assert _field_value(page, "press_gain") == "0.0002"


def test_admin_refuses_an_out_of_range_value_and_saves_nothing(client, settings_path):
    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "12", "press_gain": "0.0003"}).get_data(as_text=True)
    assert "toast-error" in body
    assert "Search speed must be 0.5 to 10 mm/s" in body
    body = client.post("/ui/weld-tuning/save",
                       data={"search_speed_mms": "5", "press_gain": "0.002"}).get_data(as_text=True)
    assert "Press gain must be 0.00005 to 0.001" in body
    assert not settings_path.exists()


def test_the_running_app_reads_the_saved_settings_for_each_run(client, settings_path):
    app = importlib.import_module("app")
    weld_tuning.save(WeldTuning(4, 0.0002))
    assert app.job._weld_tuning() == WeldTuning(4, 0.0002)


def test_run_history_shows_the_gain_or_an_older_runs_press_speed(client):
    template = importlib.import_module("app").app.jinja_env.get_template("partials/job_history.html")
    html = template.render(runs=[
        {"started_at": "2026-10-05T09:30:00", "search_speed_mms": 5.0, "press_gain": 0.0003},
        {"started_at": "2026-10-02T14:50:00", "search_speed_mms": 5.0, "press_speed_mms": 0.15},
    ])
    assert "5 / gain 0.0003" in html
    assert "5 / 0.15 mm/s" in html
