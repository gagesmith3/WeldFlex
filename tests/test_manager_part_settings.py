"""The manager's Part Settings modal: its tabs, and the limits it shares with the
code that consumes the settings.

The limits are typed into the template. These tests hold them to the values the
weld code enforces, so changing one side fails here instead of the modal saving
a value weld.lua silently ignores. The app is imported with the robot link's
start() stubbed out, so nothing here reaches a controller.
"""

import importlib
import re
from pathlib import Path

import pytest

from lua_builder import DSC_CALIBRATED_ENV, STUD_RELOAD_MS_MAX, STUD_RELOAD_MS_MIN
from robot_service import WeldFlexRobotService

WELD_LUA = Path(__file__).resolve().parents[1] / "programs" / "weld.lua"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    return importlib.import_module("app").app.test_client()


def _designer(client) -> str:
    response = client.get("/ui/manager/part-designer")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    assert match, f"{name} missing from {tag}"
    return match.group(1)


def _input_attr(html: str, input_id: str, name: str) -> str:
    tag = re.search(rf'<input[^>]*\bid="{input_id}"[^>]*>', html)
    assert tag, f"no #{input_id} in the settings modal"
    return _attr(tag.group(0), name)


def test_every_settings_tab_controls_its_own_panel(client):
    html = _designer(client)
    tabs = re.findall(r'<button[^>]*\brole="tab"[^>]*>', html)
    assert [_attr(tab, "data-tab") for tab in tabs] == ["heights", "weld", "motion"]
    for tab in tabs:
        name = _attr(tab, "data-tab")
        assert _attr(tab, "aria-controls") == f"pds-panel-{name}"
        panel = re.search(rf'<section[^>]*\bid="pds-panel-{name}"[^>]*>', html)
        assert panel, f"no panel for the {name} tab"
        assert _attr(panel.group(0), "data-tab") == name


def test_pressure_limit_is_weld_lua_press_limit(client):
    lua = WELD_LUA.read_text(encoding="utf-8")
    limit = float(re.search(r"PRESS_TARGET_MAX_LBF\s*=\s*([\d.]+)", lua).group(1))
    assert float(_input_attr(_designer(client), "pd-modal-pressure", "max")) == limit


def test_stud_reload_limits_are_lua_builder_limits(client):
    html = _designer(client)
    assert int(_input_attr(html, "pd-modal-stud-reload-ms", "min")) == STUD_RELOAD_MS_MIN
    assert int(_input_attr(html, "pd-modal-stud-reload-ms", "max")) == STUD_RELOAD_MS_MAX


@pytest.mark.parametrize("calibrated", [False, True])
def test_dsc_warning_renders_only_on_an_uncalibrated_machine(client, monkeypatch, calibrated):
    if calibrated:
        monkeypatch.setenv(DSC_CALIBRATED_ENV, "1")
    else:
        monkeypatch.delenv(DSC_CALIBRATED_ENV, raising=False)
    assert ('id="pds-dsc-cal-note"' in _designer(client)) is not calibrated
