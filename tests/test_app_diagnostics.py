"""Settings → Robot Diagnostics: the condensed one-screen summary.

Every value on it is a cache read. These tests fail the moment rendering it
issues robot RPC, the same guard the header fault views run under.
"""
import importlib
import re
import time

import pytest

import fault_codes
from robot_feed import FeedSnapshot
from robot_link import ConnSnapshot, ConnState
from robot_service import WeldFlexRobotService


def _frame(**overrides) -> FeedSnapshot:
    fields = {"program_state": 2, "prog_cur_line": 42, "error_code": 0,
              "main_errcode": 0, "sub_errcode": 0, "emergency_stop": 0}
    fields.update(overrides)
    return FeedSnapshot(fields=fields, received_monotonic=time.monotonic(), generation=1)


@pytest.fixture
def diag_app(monkeypatch):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module.robot, "_call",
        lambda *args, **kwargs: pytest.fail("the diagnostics summary must never issue robot RPC"),
    )
    monkeypatch.setattr(module.robot, "snapshot", lambda: ConnSnapshot(
        state=ConnState.CONNECTED.value, connected=True, generation=1,
    ))
    return module


def _summary(module, monkeypatch, **fields) -> str:
    monkeypatch.setattr(module.robot, "feed_snapshot", lambda: _frame(**fields))
    response = module.app.test_client().get("/ui/diagnostics")
    assert response.status_code == 200
    return re.sub(r"\s+", " ", response.get_data(as_text=True))


def test_it_renders_with_no_robot_at_all(monkeypatch):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    html = re.sub(r"\s+", " ", module.app.test_client().get("/ui/diagnostics").get_data(as_text=True))
    assert "Commands" in html and "Status feed" in html
    assert "No reading" in html


def test_commands_and_feed_are_separate_lights(diag_app, monkeypatch):
    html = _summary(diag_app, monkeypatch)
    assert "Commands</span> <strong>Online</strong>" in html
    assert "Status feed</span> <strong>" in html
    assert "running" in html and "line 42" in html
    assert "Fault</span> <strong class=\"dg-good\">None</strong>" in html


def test_a_fault_shows_its_code_and_text(diag_app, monkeypatch):
    html = _summary(diag_app, monkeypatch, error_code=3, main_errcode=117, sub_errcode=4)
    assert "117/4" in html
    assert fault_codes.describe(117, 4).description in html


def test_an_engaged_estop_says_so(diag_app, monkeypatch):
    html = _summary(diag_app, monkeypatch, emergency_stop=1)
    assert "E-stop engaged" in html and "ENGAGED" in html


def test_io_levels_come_from_the_frame(diag_app, monkeypatch):
    # DI0 (welder ready) high, DI1 (stud on work) low; DO1 (feeder) high.
    html = _summary(diag_app, monkeypatch, cl_dgt_input_l=0b01, cl_dgt_output_l=0b10)
    assert re.search(r"DI0 welder ready</span><strong class=\"dg-high\">HIGH", html)
    assert re.search(r"DI1 stud on work</span><strong class=\"dg-low\">low", html)
    assert re.search(r"DO0 weld trigger</span><strong class=\"dg-low\">low", html)
    assert re.search(r"DO1 feeder</span><strong class=\"dg-high\">HIGH", html)


def test_the_page_keeps_its_actions_and_drops_the_full_feed_panel(diag_app):
    html = diag_app.app.test_client().get("/operator/settings/diagnostics").get_data(as_text=True)
    for endpoint in ("/ui/connection/connect", "/ui/diagnostics/reconnect", "/ui/connection/disconnect",
                     "/ui/diagnostics/reset-errors", "/ui/diagnostics/stop-program"):
        assert f'hx-post="{endpoint}"' in html
    assert "/ui/diagnostics/feed" not in html
