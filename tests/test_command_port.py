"""Port-8080 command frames, and when robot_service falls back to them.

The fallback exists because the controller stops answering XML-RPC for the
whole of a force operation (live on the Pi, 2026-09-23: Pause during the press
timed out twice and the run went on). A fake socket server stands in for the
controller; nothing here touches a robot.
"""

import socket
import threading
from types import SimpleNamespace

import pytest

import command_port
from command_port import CMD_PAUSE, CMD_RESUME, CMD_STOP, CommandPortError
from robot_service import WeldFlexRobotService


def test_frames_match_the_vendor_examples():
    # The manual's STOP example and the SDK's PAUSE/RESUME strings.
    assert command_port.build_frame(4, CMD_STOP, "STOP") == "/f/bIII4III102III4IIISTOPIII/b/f"
    assert command_port.build_frame(0, CMD_PAUSE, "PAUSE") == "/f/bIII0III103III5IIIPAUSEIII/b/f"
    assert command_port.build_frame(0, CMD_RESUME, "RESUME") == "/f/bIII0III104III6IIIRESUMEIII/b/f"


def test_reply_parsing():
    assert command_port.parse_reply("/f/bIII4III102III1III1III/b/f", CMD_STOP) is True
    assert command_port.parse_reply("/f/bIII4III102III1III0III/b/f", CMD_STOP) is False
    with pytest.raises(CommandPortError):
        command_port.parse_reply("/f/bIII4III103III1III1III/b/f", CMD_STOP)  # wrong command
    with pytest.raises(CommandPortError):
        command_port.parse_reply("garbage", CMD_STOP)


class FakeController:
    """One-shot TCP server: records the frame it receives, answers `reply`."""

    def __init__(self, reply: bytes | None):
        self.reply = reply
        self.received = b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            self.received = conn.recv(1024)
            if self.reply is not None:
                # Split the reply, as a real TCP stream is free to.
                conn.sendall(self.reply[:7])
                conn.sendall(self.reply[7:])
            else:
                conn.recv(1024)  # hold the line open until the client gives up
        self.sock.close()


def test_send_command_round_trip():
    ctl = FakeController(b"/f/bIII9III103III1III1III/b/f")
    command_port.send_command("127.0.0.1", CMD_PAUSE, port=ctl.port)
    ctl.thread.join(2)
    assert b"III103III5IIIPAUSEIII/b/f" in ctl.received


def test_send_command_refused_raises():
    ctl = FakeController(b"/f/bIII9III104III1III0III/b/f")
    with pytest.raises(CommandPortError, match="refused RESUME"):
        command_port.send_command("127.0.0.1", CMD_RESUME, port=ctl.port)


def test_send_command_times_out_rather_than_hanging():
    ctl = FakeController(None)
    with pytest.raises(CommandPortError):
        command_port.send_command("127.0.0.1", CMD_STOP, port=ctl.port, reply_timeout_s=0.3)


# ---------------- robot_service: XML-RPC first, 8080 only when it is down ----------------


@pytest.fixture
def service(monkeypatch):
    svc = WeldFlexRobotService("127.0.0.1")
    sent = []
    monkeypatch.setattr(command_port, "send_command", lambda ip, cmd: sent.append(cmd))
    svc.sent = sent
    yield svc
    svc.shutdown()


def _connected(monkeypatch, svc, connected):
    monkeypatch.setattr(svc, "snapshot", lambda: SimpleNamespace(connected=connected))


def test_xmlrpc_down_uses_the_command_port(monkeypatch, service):
    _connected(monkeypatch, service, False)
    monkeypatch.setattr(service, "_call", lambda *a, **k: pytest.fail("XML-RPC is down"))
    service.pause_program()
    service.resume_program()
    service.stop_program()
    assert service.sent == [CMD_PAUSE, CMD_RESUME, CMD_STOP]


def test_xmlrpc_timing_out_mid_call_falls_through(monkeypatch, service):
    """Connected when pressed, but the force op started: the call times out."""
    _connected(monkeypatch, service, True)

    def timed_out(*a, **k):
        raise RuntimeError("Robot not connected (faulted): <lambda> timed out after 5s")

    monkeypatch.setattr(service, "_call", timed_out)
    service.pause_program()
    assert service.sent == [CMD_PAUSE]


def test_xmlrpc_answering_is_used_and_8080_is_not(monkeypatch, service):
    _connected(monkeypatch, service, True)
    monkeypatch.setattr(service, "_call", lambda fn, **k: 0)
    service.pause_program()
    assert service.sent == []


def test_a_refusal_from_an_answering_controller_is_final(monkeypatch, service):
    """A real "cannot pause now" must not be retried behind its back on 8080."""
    _connected(monkeypatch, service, True)
    monkeypatch.setattr(service, "_call", lambda fn, **k: 14)
    with pytest.raises(RuntimeError, match="code 14"):
        service.pause_program()
    assert service.sent == []


def test_both_paths_failing_reports_both(monkeypatch, service):
    _connected(monkeypatch, service, False)

    def refused(ip, cmd):
        raise CommandPortError("connect to 127.0.0.1:8080 failed: refused")

    monkeypatch.setattr(command_port, "send_command", refused)
    with pytest.raises(RuntimeError, match="XML-RPC not answering.*command port"):
        service.stop_program()
