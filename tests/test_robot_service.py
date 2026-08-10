import threading
import time
from types import SimpleNamespace

from robot_feed import FeedSnapshot
from robot_link import ConnSnapshot, ConnState, ForceSnapshot
from robot_service import DO_PULSE_MAX_S, WeldFlexRobotService


def _connected_snapshot(generation: int) -> ConnSnapshot:
    return ConnSnapshot(
        state=ConnState.CONNECTED.value,
        connected=True,
        generation=generation,
    )


def _wait_for(predicate: object, timeout_s: float = 1.0) -> bool:
    done = threading.Event()

    def wait() -> None:
        if predicate():
            done.set()

    while not done.wait(0.01):
        wait()
        timeout_s -= 0.01
        if timeout_s <= 0:
            return False
    return True


def test_weld_telemetry_sampler_caches_successful_probe(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    sampled = threading.Event()
    probe_calls = []

    def fake_probe(stud_di, ready_di, sysvar_slots):
        probe_calls.append((stud_di, ready_di, sysvar_slots))
        sampled.set()
        return {
            "ft_err": 0,
            "fz": -88.0,
            "stud_di": stud_di,
            "stud_on_work": 1,
            "ready_di": ready_di,
            "weld_ready": 0,
            "sysvars": {1: 20.0, 2: 1.0},
            "tcp_z": 123.4,
            "program_state_raw": 2,
            "line": 42,
            "fault_main": None,
            "fault_sub": None,
        }

    monkeypatch.setattr(service, "snapshot", lambda: _connected_snapshot(7))
    monkeypatch.setattr(service, "weld_probe", fake_probe)

    service.start_weld_telemetry(1, 0, (1, 2), interval_s=60.0)
    assert sampled.wait(1.0)
    assert _wait_for(lambda: service.weld_telemetry_snapshot().sampled_ts is not None)

    reading = service.weld_telemetry_snapshot()
    assert probe_calls == [(1, 0, (1, 2))]
    assert reading.active
    assert reading.is_fresh()
    assert reading.generation == 7
    assert reading.ft_err == 0
    assert reading.fz == -88.0
    assert reading.stud_on_work == 1
    assert reading.weld_ready == 0
    assert reading.sysvar(1) == 20.0
    assert reading.sysvar(2) == 1.0
    assert reading.line == 42

    service.stop_weld_telemetry()
    assert not service.weld_telemetry_snapshot().active
    service.shutdown()


def test_weld_telemetry_sampler_rejects_cross_generation_probe(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    snapshots = iter((_connected_snapshot(7), _connected_snapshot(8)))

    monkeypatch.setattr(service, "snapshot", lambda: next(snapshots))
    monkeypatch.setattr(
        service,
        "weld_probe",
        lambda stud_di, ready_di, sysvar_slots: {
            "ft_err": 0,
            "fz": -88.0,
            "stud_di": stud_di,
            "stud_on_work": 1,
            "ready_di": ready_di,
            "weld_ready": 0,
            "sysvars": {},
            "tcp_z": None,
            "program_state_raw": 2,
            "line": 42,
            "fault_main": None,
            "fault_sub": None,
        },
    )

    service.start_weld_telemetry(1, 0, (), interval_s=60.0)
    assert _wait_for(lambda: service.weld_telemetry_snapshot().error is not None)

    reading = service.weld_telemetry_snapshot()
    assert reading.sampled_ts is None
    assert reading.generation is None
    assert reading.error == "Robot connection changed during weld telemetry sample"

    service.stop_weld_telemetry()
    service.shutdown()


def test_weld_probe_uses_lua_di_slots_not_blocking_host_di(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []

    class RawRobot:
        def GetSysVarValue(self, slot):
            calls.append(f"sysvar:{slot}")
            return [0, 1 if slot == 6 else 0]

        def GetActualTCPPose(self, frame):
            calls.append("pose")
            return [0, 1, 2, 3, 4, 5, 6]

        def GetDI(self, di_id, block):
            raise AssertionError("host GetDI must not run in the weld telemetry sampler")

    def fake_call(fn, **kwargs):
        return fn(SimpleNamespace(robot=RawRobot()))

    monkeypatch.setattr(service, "_call", fake_call)
    monkeypatch.setattr(
        service,
        "force_snapshot",
        lambda: ForceSnapshot(
            values=(1, 2, -88, 4, 5, 6),
            received_monotonic=time.monotonic(),
            generation=1,
            source="cnde",
        ),
    )

    reading = service.weld_probe(1, 0, (1, 6, 7))

    assert calls == ["sysvar:1", "sysvar:6", "sysvar:7", "pose"]
    assert reading["ft_err"] == 0
    assert reading["fz"] == -88
    assert reading["stud_on_work"] is None
    assert reading["weld_ready"] is None
    assert reading["sysvars"] == {1: 0.0, 6: 1.0, 7: 0.0}


def test_ft_read_prefers_fresh_cnde_force_snapshot(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service,
        "force_snapshot",
        lambda: ForceSnapshot(
            values=(1, 2, -3, 4, 5, 6),
            received_monotonic=time.monotonic(),
            generation=4,
            source="cnde",
        ),
    )
    monkeypatch.setattr(
        service,
        "_call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("raw RPC must not run")),
    )

    reading = service.ft_read()

    assert reading["fz"] == -3
    assert reading["source"] == "cnde"
    assert reading["age_s"] is not None


def test_get_universal_state_consolidates_robot_sources(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(service, "snapshot", lambda: _connected_snapshot(12))
    monkeypatch.setattr(
        service,
        "force_snapshot",
        lambda: ForceSnapshot(
            values=(0, 0, 100.0, 0, 0, 0),  # 100 N compression
            received_monotonic=time.monotonic(),
            generation=12,
            source="cnde",
        ),
    )

    fake_telemetry = SimpleNamespace(
        sampled_ts=time.time(),
        tcp_z=145.2,
        sysvar=lambda slot: {1: 31.0, 2: 0.0, 3: 150.0, 4: 4.8, 5: 1.0, 6: 1.0, 7: 1.0, 8: 20.0}.get(slot),
    )
    monkeypatch.setattr(service, "_weld_telemetry", fake_telemetry)

    ustate = service.get_universal_state()

    assert ustate.connected is True
    assert ustate.state == ConnState.CONNECTED.value
    assert ustate.generation == 12
    assert ustate.force_fresh is True
    assert round(ustate.fz_lbf, 1) == -22.5
    assert ustate.stud_on_work == 1
    assert ustate.weld_ready == 1
    assert ustate.weld_phase_code == 31
    assert ustate.weld_phase_label == "press: driving in"
    assert ustate.last_ft_return == 0
    assert ustate.contact_z == 150.0
    assert ustate.press_travel_mm == 4.8
    assert ustate.collision_guard_code == 1
    assert ustate.collision_guard_label == "custom thresholds"
    assert ustate.collision_guard_applied is True
    assert ustate.target_press_lbf == 20.0

# --- observation sourced from the port-8083 push --------------------------
#
# The two channels fail independently. XML-RPC stops answering while the
# controller is busy with a force operation; the pushed frame keeps arriving.
# These pin down which source wins, and — more importantly — that a live feed is
# never allowed to imply commands are deliverable.


def _feed_frame(**overrides) -> FeedSnapshot:
    fields = {
        "program_state": 2,
        "prog_cur_line": 17,
        "main_errcode": 0,
        "sub_errcode": 0,
    }
    fields.update(overrides)
    return FeedSnapshot(fields=fields, received_monotonic=time.monotonic(), generation=1)


def test_universal_state_prefers_the_feed_for_observation(monkeypatch):
    """A fresh frame outranks the RPC cache even when both are available."""
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service,
        "snapshot",
        lambda: ConnSnapshot(
            state=ConnState.CONNECTED.value,
            connected=True,
            generation=3,
            program_state_raw=1,
            current_line=999,
        ),
    )
    monkeypatch.setattr(service, "feed_snapshot", lambda: _feed_frame())

    ustate = service.get_universal_state()

    assert ustate.telemetry_source == "8083"
    assert ustate.program_state == "running"
    assert ustate.current_line == 17
    assert ustate.commands_available is True


def test_universal_state_reports_telemetry_when_only_the_feed_survives(monkeypatch):
    """The find-surface symptom: XML-RPC goes quiet while the robot runs on.

    "Offline" is a lie the operator can see through — the arm is plainly moving.
    "Online" is the dangerous one: commands ride XML-RPC, so Stop would silently
    do nothing. This state exists to say exactly what is true.
    """
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service,
        "snapshot",
        lambda: ConnSnapshot(state=ConnState.FAULTED.value, connected=False, generation=4),
    )
    monkeypatch.setattr(service, "feed_snapshot", lambda: _feed_frame())

    ustate = service.get_universal_state()

    assert ustate.state == "telemetry"
    assert ustate.feed_streaming is True
    assert ustate.commands_available is False
    assert ustate.connected is False
    assert ustate.program_state == "running"
    assert "commands cannot be delivered" in ustate.probe_error


def test_universal_state_falls_back_to_rpc_when_the_feed_is_stale(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service,
        "snapshot",
        lambda: ConnSnapshot(
            state=ConnState.CONNECTED.value,
            connected=True,
            generation=3,
            program_state_raw=1,
            current_line=42,
        ),
    )
    stale = FeedSnapshot(
        fields={"program_state": 2},
        received_monotonic=time.monotonic() - 3600.0,
        generation=1,
    )
    monkeypatch.setattr(service, "feed_snapshot", lambda: stale)

    ustate = service.get_universal_state()

    assert ustate.telemetry_source == "rpc"
    assert ustate.state == ConnState.CONNECTED.value
    assert ustate.program_state == "stopped"
    assert ustate.current_line == 42


def test_a_live_feed_does_not_mask_an_operator_disconnect(monkeypatch):
    """Disconnect is an intent, not a failure; the feed must not paper over it."""
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service,
        "snapshot",
        lambda: ConnSnapshot(state=ConnState.DISCONNECTED.value, connected=False),
    )
    monkeypatch.setattr(service, "feed_snapshot", lambda: _feed_frame())

    assert service.get_universal_state().state == ConnState.DISCONNECTED.value


def test_frame_fault_codes_distinguish_zero_from_absent(monkeypatch):
    """The frame reports 0 for "no fault"; ConnSnapshot uses None. Don't conflate."""
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service,
        "snapshot",
        lambda: ConnSnapshot(state=ConnState.CONNECTED.value, connected=True),
    )

    monkeypatch.setattr(service, "feed_snapshot", lambda: _feed_frame())
    clean = service.get_universal_state()
    assert clean.fault_main is None
    assert clean.has_fault is False

    monkeypatch.setattr(
        service, "feed_snapshot", lambda: _feed_frame(main_errcode=117, sub_errcode=4)
    )
    faulted = service.get_universal_state()
    assert (faulted.fault_main, faulted.fault_sub) == (117, 4)
    assert faulted.has_fault is True
    assert faulted.fault_source == "8083"


def test_pulse_do_drives_the_line_high_then_low_in_one_dispatch(monkeypatch):
    """The whole pulse is one worker submission. The link runs a single worker, so
    keeping both writes inside it is what guarantees nothing is interleaved between
    them and leaves a wired output latched high.
    """
    service = WeldFlexRobotService("127.0.0.1")
    writes = []
    kwargs_seen = {}

    class RawRobot:
        def SetDO(self, channel, status):
            writes.append((channel, status))
            return 0

    def fake_call(fn, **kwargs):
        kwargs_seen.update(kwargs)
        return fn(RawRobot())

    monkeypatch.setattr(service, "_call", fake_call)
    started = time.monotonic()
    service.pulse_do(1, 0.05)

    assert writes == [(1, 1), (1, 0)]
    assert time.monotonic() - started >= 0.05
    # A retry would advance the feeder a second time — the default 3 is wrong here.
    assert kwargs_seen["retries"] == 1
    # The hold is inside the call, so the timeout has to allow for it.
    assert kwargs_seen["timeout"] > 0.05


def test_pulse_do_drops_the_line_even_if_the_hold_is_interrupted(monkeypatch):
    """DO1 is the stud feeder. An exception mid-hold must not leave it energized."""
    service = WeldFlexRobotService("127.0.0.1")
    writes = []

    class RawRobot:
        def SetDO(self, channel, status):
            writes.append((channel, status))
            return 0

    def boom(_seconds):
        raise KeyboardInterrupt("interrupted mid-hold")

    monkeypatch.setattr(service, "_call", lambda fn, **kwargs: fn(RawRobot()))
    monkeypatch.setattr("robot_service.time.sleep", boom)

    try:
        service.pulse_do(1, 0.25)
    except KeyboardInterrupt:
        pass

    assert writes == [(1, 1), (1, 0)]


def test_pulse_do_clamps_the_hold_and_reports_a_failed_write(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    slept = []
    monkeypatch.setattr("robot_service.time.sleep", slept.append)

    class RawRobot:
        def SetDO(self, channel, status):
            return 0 if status == 1 else -1

    monkeypatch.setattr(service, "_call", lambda fn, **kwargs: fn(RawRobot()))

    try:
        service.pulse_do(1, 99.0)
    except RuntimeError as exc:
        assert "low code -1" in str(exc)
    else:
        raise AssertionError("a nonzero SetDO code must raise")

    # Clamped: the hold blocks the only command channel for its whole duration.
    assert slept == [DO_PULSE_MAX_S]
