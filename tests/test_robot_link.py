from robot_link import ConnState, RobotLink, _ClientHandle
from robot_service import WeldFlexRobotService


def test_weld_probe_uses_short_timeout_for_live_telemetry(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []

    def fake_call(fn, timeout=5.0, retries=3):
        calls.append((timeout, retries))
        return (
            (0, None),
            None,
            None,
            [],
            None,
            None,
            None,
            None,
        )

    monkeypatch.setattr(service, "_call", fake_call)

    service.weld_probe(stud_di=1, ready_di=0, sysvar_slots=(1, 2))

    assert calls == [(1.0, 1)]


def test_tick_still_probes_when_fast_heartbeat_is_on_and_worker_is_busy(monkeypatch):
    link = RobotLink("127.0.0.1")
    link._enabled = True
    link._fast_heartbeat = True
    link._handle = _ClientHandle(gen=1, rpc=object(), ip="127.0.0.1")

    monkeypatch.setattr(link._worker, "is_busy", lambda: True)
    seen = []

    def fake_probe_body(client):
        seen.append(client)
        return {
            "latency_ms": 1.5,
            "state_raw": 2,
            "state_src": "rpc",
            "line": 42,
            "fault_main": None,
            "fault_sub": None,
            "fault_src": "none",
        }

    monkeypatch.setattr(link, "_probe_body", fake_probe_body)

    link._tick()

    assert seen == [link._handle.rpc]
    snap = link.snapshot()
    assert snap.state == ConnState.CONNECTED.value
    assert snap.program_state_raw == 2
    assert snap.current_line == 42
