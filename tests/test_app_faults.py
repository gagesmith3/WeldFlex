"""Header Faults chip, fault modal and the operator Reset route."""
import importlib
import time

import pytest

from robot_feed import FeedSnapshot
from robot_link import ConnSnapshot, ConnState
from robot_service import WeldFlexRobotService


def _frame(**overrides) -> FeedSnapshot:
    fields = {"program_state": 1, "prog_cur_line": 0, "error_code": 0,
              "main_errcode": 0, "sub_errcode": 0, "emergency_stop": 0}
    fields.update(overrides)
    return FeedSnapshot(fields=fields, received_monotonic=time.monotonic(), generation=1)


@pytest.fixture
def fault_app(monkeypatch):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module.robot, "_call",
        lambda *args, **kwargs: pytest.fail("status views must never issue robot RPC"),
    )
    monkeypatch.setattr(module.robot, "snapshot", lambda: ConnSnapshot(
        state=ConnState.CONNECTED.value, connected=True, generation=1,
    ))
    return module


def _use_frame(module, monkeypatch, **fields):
    monkeypatch.setattr(module.robot, "feed_snapshot", lambda: _frame(**fields))


def test_state_chip_shows_program_state_when_there_is_no_fault(fault_app, monkeypatch):
    _use_frame(fault_app, monkeypatch)
    html = fault_app.app.test_client().get("/ui/connection").get_data(as_text=True)
    assert "stopped" in html
    assert "FAULT" not in html
    assert "WfFault.open()" not in html


def test_state_chip_becomes_a_tappable_fault(fault_app, monkeypatch):
    _use_frame(fault_app, monkeypatch, error_code=3, main_errcode=117, sub_errcode=4)
    html = fault_app.app.test_client().get("/ui/connection").get_data(as_text=True)
    assert "FAULT" in html
    assert "collision" in html
    assert "WfFault.open()" in html


def test_state_chip_shows_estop(fault_app, monkeypatch):
    _use_frame(fault_app, monkeypatch, emergency_stop=1)
    html = fault_app.app.test_client().get("/ui/connection").get_data(as_text=True)
    assert "E-STOP" in html
    assert "WfFault.open()" in html


def test_fault_panel_renders_codes_without_rpc(fault_app, monkeypatch):
    _use_frame(fault_app, monkeypatch, error_code=3, main_errcode=117, sub_errcode=4)
    html = fault_app.app.test_client().get("/ui/fault/status").get_data(as_text=True)
    assert "Robot fault" in html
    assert "117/4" in html


def test_fault_reset_refused_during_estop_without_rpc(fault_app, monkeypatch):
    _use_frame(fault_app, monkeypatch, emergency_stop=1)
    html = fault_app.app.test_client().post("/ui/fault/reset").get_data(as_text=True)
    assert "Failed" in html
    assert "E-stop" in html


def test_fault_reset_sends_reset_all_error(fault_app, monkeypatch):
    _use_frame(fault_app, monkeypatch, error_code=3, main_errcode=117)
    sent = []

    class RawRobot:
        def ResetAllError(self):
            sent.append(True)
            return 0

    monkeypatch.setattr(fault_app.robot, "_call", lambda fn, **kwargs: fn(RawRobot()))
    html = fault_app.app.test_client().post("/ui/fault/reset").get_data(as_text=True)
    assert sent == [True]
    assert "Reset Errors: OK" in html
