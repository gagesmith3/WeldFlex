import hashlib
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from robot_feed import FeedSnapshot
from robot_link import ConnSnapshot, ConnState, ForceSnapshot
from robot_service import (
    DO_PULSE_MAX_S,
    FileTransferResult,
    WeldFlexRobotService,
    WeldTelemetrySnapshot,
    transfer_file,
)


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

    assert calls == ["sysvar:1", "sysvar:6", "sysvar:7"]
    assert reading["ft_err"] == 0
    assert reading["fz"] == -88
    assert reading["stud_on_work"] is None
    assert reading["weld_ready"] is None
    assert reading["sysvars"] == {1: 0.0, 6: 1.0, 7: 0.0}


def test_weld_probe_uses_feed_pose_and_di_without_rpc_duplicates(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []
    monkeypatch.setattr(service, "feed_snapshot", lambda: _feed_frame(
        cl_dgt_input_l=2, tl_cur_pos=[0, 0, 123.0, 0, 0, 0],
    ))

    def fake_call(fn, **kwargs):
        calls.append(kwargs["coalesce_key"])
        return 0, 31

    monkeypatch.setattr(service, "_call", fake_call)
    reading = service.weld_probe(1, 0, (1, 6, 7))
    assert calls == ["weld-sysvar:1"]
    assert reading["tcp_z"] == 123.0
    assert reading["stud_on_work"] == 1
    assert reading["weld_ready"] == 0
    state = service.get_universal_state()
    assert state.tcp_z == 123.0
    assert state.stud_on_work == 1
    assert state.weld_ready == 0


def test_weld_probe_aborts_after_first_transport_failure(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []

    def fake_call(fn, **kwargs):
        calls.append(kwargs["coalesce_key"])
        raise RuntimeError("transport timed out")

    monkeypatch.setattr(service, "_call", fake_call)
    with pytest.raises(RuntimeError, match="transport timed out"):
        service.weld_probe(1, 0, tuple(range(1, 9)))
    assert calls == ["weld-sysvar:1"]


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


def test_ft_read_prefers_fresh_8083_force_data_over_cnde(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service,
        "feed_snapshot",
        lambda: FeedSnapshot(
            fields={"ft_data": [1, 2, -3, 4, 5, 6], "ft_act_status": 1},
            received_monotonic=time.monotonic(),
            generation=4,
        ),
    )
    monkeypatch.setattr(
        service,
        "force_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("CNDE must not run")),
    )

    reading = service.ft_read()

    assert reading == {
        "fx": 1.0,
        "fy": 2.0,
        "fz": -3.0,
        "mx": 4.0,
        "my": 5.0,
        "mz": 6.0,
        "active": True,
        "source": "8083",
        "age_s": pytest.approx(0.0, abs=0.1),
    }


@pytest.mark.parametrize("age_s", [0.6, 4.0])
def test_stale_force_and_universal_state_never_issue_rpc(monkeypatch, age_s):
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service, "feed_snapshot",
        lambda: FeedSnapshot(
            fields={"ft_data": [1, 2, -3, 4, 5, 6], "ft_act_status": 1},
            received_monotonic=time.monotonic() - age_s,
        ),
    )
    monkeypatch.setattr(
        service, "_call",
        lambda *args, **kwargs: pytest.fail("cached state must never issue RPC"),
    )

    assert service.ft_read()["source"] == "none"
    assert service.ft_read()["fz"] is None
    state = service.get_universal_state()
    assert state.fz_lbf is None
    assert not state.force_fresh


def test_ft_setup_configures_and_activates_without_changing_tare(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []

    class FakeRobot:
        def FT_SetConfig(self, company, device):
            calls.append(("config", company, device))
            return 0

        def FT_SetRCS(self, reference):
            calls.append(("rcs", reference))
            return 0

        def FT_Activate(self, state):
            calls.append(("activate", state))
            return 59 if state == 1 else 0

        def FT_SetZero(self, state):
            calls.append(("zero", state))
            return 62 if state == 1 else 0

    monkeypatch.setattr(service, "_call", lambda fn, **kwargs: fn(FakeRobot()))
    monkeypatch.setattr("robot_service.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match=r"FT_Activate\(1\) failed \(code 59\)"):
        service.ft_setup()

    assert calls == [
        ("config", 24, 0),
        ("rcs", 0),
        ("activate", 0),
        ("activate", 1),
    ]


def test_ft_compensation_reads_payload_and_center_of_gravity_in_one_dispatch(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    call_count = 0

    class RawRobot:
        def GetForceSensorPayload(self):
            return 0, 1.25

        def GetForceSensorPayloadCog(self):
            return 0, 12.0, -3.5, 48.0

    def fake_call(fn, **_kwargs):
        nonlocal call_count
        call_count += 1
        return fn(RawRobot())

    monkeypatch.setattr(service, "_call", fake_call)

    assert service.ft_compensation() == {
        "payload_kg": 1.25,
        "cog_mm": (12.0, -3.5, 48.0),
    }
    assert call_count == 1


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
        is_fresh=lambda: True,
        generation=12,
        tcp_z=145.2,
        sysvar=lambda slot: {1: 31.0, 2: 0.0, 3: 150.0, 4: 4.8, 5: 1.0, 6: 1.0, 7: 1.0, 8: 20.0, 9: 0.6, 10: -0.2}.get(slot),
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
    assert ustate.press_hold_travel_mm == 0.6
    assert ustate.weld_jolt_travel_mm == -0.2
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


@pytest.mark.parametrize("age_s,generation", [(2.0, 12), (0.0, 11)])
def test_universal_state_rejects_old_lua_telemetry(monkeypatch, age_s, generation):
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(service, "snapshot", lambda: _connected_snapshot(12))
    monkeypatch.setattr(service, "_weld_telemetry", WeldTelemetrySnapshot(
        sampled_ts=time.time() - age_s,
        generation=generation,
        sysvars=((1, 31.0), (6, 1.0), (7, 1.0), (8, 20.0)),
        tcp_z=100.0,
    ))
    state = service.get_universal_state()
    assert state.weld_phase_code is None
    assert state.stud_on_work is None
    assert state.weld_ready is None
    assert state.target_press_lbf is None
    assert state.tcp_z is None


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


def test_frame_coarse_error_and_estop_reach_universal_state(monkeypatch):
    """The header's State chip reads these; the coarse class is the only label."""
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(service, "snapshot", lambda: _connected_snapshot(1))

    monkeypatch.setattr(service, "feed_snapshot", lambda: _feed_frame(
        error_code=0, emergency_stop=0,
    ))
    clean = service.get_universal_state()
    assert clean.has_fault is False
    assert clean.fault_known is True
    assert clean.fault_label is None
    assert clean.emergency_stop is False

    monkeypatch.setattr(service, "feed_snapshot", lambda: _feed_frame(
        error_code=3, main_errcode=117, sub_errcode=4, emergency_stop=1,
    ))
    faulted = service.get_universal_state()
    assert faulted.has_fault is True
    assert faulted.fault_class == 3
    assert faulted.fault_label == "collision"
    assert faulted.emergency_stop is True


def test_a_blank_fault_code_from_a_dead_source_is_not_clear(monkeypatch):
    """No feed and no XML-RPC: fault_main is None, which must read as unknown."""
    service = WeldFlexRobotService("127.0.0.1")
    monkeypatch.setattr(
        service, "snapshot",
        lambda: ConnSnapshot(state=ConnState.FAULTED.value, connected=False),
    )
    monkeypatch.setattr(service, "feed_snapshot", lambda: FeedSnapshot())

    ustate = service.get_universal_state()
    assert ustate.has_fault is False
    assert ustate.fault_known is False
    assert ustate.emergency_stop is None


def _reset_service(monkeypatch, snapshot, feed, reply=0):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []

    class RawRobot:
        def ResetAllError(self):
            calls.append("ResetAllError")
            return reply

    monkeypatch.setattr(service, "snapshot", lambda: snapshot)
    monkeypatch.setattr(service, "feed_snapshot", lambda: feed)
    monkeypatch.setattr(service, "_call", lambda fn, **kwargs: fn(RawRobot()))
    return service, calls


def test_reset_errors_sends_reset_and_records_when(monkeypatch):
    service, calls = _reset_service(
        monkeypatch, _connected_snapshot(1), _feed_frame(main_errcode=117, emergency_stop=0),
    )
    assert service.last_reset_age_s() is None
    service.reset_errors()
    assert calls == ["ResetAllError"]
    assert service.last_reset_age_s() is not None


def test_reset_errors_is_refused_without_commands(monkeypatch):
    """The telemetry window: the feed is live, XML-RPC is not — the verb can't arrive."""
    service, calls = _reset_service(
        monkeypatch,
        ConnSnapshot(state=ConnState.FAULTED.value, connected=False),
        _feed_frame(main_errcode=117),
    )
    with pytest.raises(RuntimeError, match="Commands are unavailable"):
        service.reset_errors()
    assert calls == []


def test_reset_errors_is_refused_while_estop_is_engaged(monkeypatch):
    service, calls = _reset_service(
        monkeypatch, _connected_snapshot(1), _feed_frame(emergency_stop=1),
    )
    with pytest.raises(RuntimeError, match="E-stop"):
        service.reset_errors()
    assert calls == []


def test_reset_errors_reports_a_refused_reset(monkeypatch):
    service, _ = _reset_service(monkeypatch, _connected_snapshot(1), _feed_frame(), reply=14)
    with pytest.raises(RuntimeError, match="code 14"):
        service.reset_errors()


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


# --- Lua upload: the raw :20010 transfer, one reported failure per step ---


class _FakeFilePort:
    """A one-shot stand-in for the controller's file port on localhost.

    Reads until the SDK frame's "/b/f" trailer, records what arrived, then
    answers with `reply` (None closes the socket without answering, and
    "silent" holds it open without answering).
    """

    def __init__(self, reply=b"SUCCESS", bind_port=0):
        self.reply = reply
        self.received = b""
        self._server = socket.create_server(("127.0.0.1", bind_port))
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _ = self._server.accept()
        with conn:
            while not self.received.endswith(b"/b/f"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.received += chunk
            if self.reply == "silent":
                time.sleep(1.0)
            elif self.reply is not None:
                conn.sendall(self.reply)
        self._server.close()

    def join(self):
        self._thread.join(timeout=2.0)


class _FileRpc:
    def __init__(self, upload_rtn=0, check=(0, "")):
        self.upload_rtn = upload_rtn
        self.check = check
        self.calls = []

    def FileUpload(self, file_type, name):
        self.calls.append(("FileUpload", file_type, name))
        return self.upload_rtn

    def LuaUpLoadUpdate(self, name):
        self.calls.append(("LuaUpLoadUpdate", name))
        return list(self.check)


def _lua(tmp_path, body=b"PTP(zerozero, 25, -1, 0)\n"):
    path = tmp_path / "goto.lua"
    path.write_bytes(body)
    return path


def test_transfer_file_sends_the_sdk_frame_byte_for_byte(tmp_path):
    body = b"PTP(zerozero, 25, -1, 0)\n"
    path = _lua(tmp_path, body)
    port = _FakeFilePort()
    rpc = _FileRpc()

    result = transfer_file(rpc, "127.0.0.1", path, port=port.port, timeout=2.0)
    port.join()

    assert result.ok, result.detail
    assert rpc.calls == [("FileUpload", 0, "goto.lua")]
    total = len(body) + 46 + 4
    md5 = hashlib.md5(body).hexdigest()
    assert port.received == f"/f/b{total:10d}{md5}".encode() + body + b"/b/f"


def test_transfer_file_reports_a_refused_fileupload_without_touching_the_port(tmp_path):
    rpc = _FileRpc(upload_rtn=-1)
    result = transfer_file(rpc, "127.0.0.1", _lua(tmp_path), port=1, timeout=0.5)
    assert result.stage == "rpc"
    assert "code -1" in result.detail


def test_transfer_file_reports_an_unreachable_port_instead_of_raising(tmp_path):
    """Raising OSError here would make RobotLink tear down a healthy XML-RPC session."""
    with socket.create_server(("127.0.0.1", 0)) as s:
        dead_port = s.getsockname()[1]
    result = transfer_file(
        _FileRpc(), "127.0.0.1", _lua(tmp_path), port=dead_port, timeout=0.5, connect_retry_s=0.3,
    )
    assert result.stage == "connect"
    # Linux reports each refusal at once, so the retry loop is what gives up.
    # Windows retries a refused SYN inside the stack (the behavior that hid the
    # controller's slow listener on the dev box) and times out first.
    assert f"127.0.0.1:{dead_port}" in result.detail
    if sys.platform != "win32":
        assert "refused all" in result.detail


def test_transfer_file_waits_out_a_listener_that_opens_late(tmp_path):
    """The Pi case: the controller accepts FileUpload, but :20010 refuses the
    immediate connect. Linux gives up on the first refusal, and Windows does not.
    """
    with socket.create_server(("127.0.0.1", 0)) as s:
        late_port = s.getsockname()[1]
    ports = []

    def open_late():
        time.sleep(0.4)
        ports.append(_FakeFilePort(bind_port=late_port))

    threading.Thread(target=open_late, daemon=True).start()
    result = transfer_file(_FileRpc(), "127.0.0.1", _lua(tmp_path), port=late_port, timeout=2.0)

    assert result.ok, result.detail
    ports[0].join()
    assert ports[0].received.endswith(b"/b/f")


@pytest.mark.parametrize(
    "reply, expect",
    [
        (b"FAIL md5", "b'FAIL md5'"),
        (None, "closed the socket without replying"),
        ("silent", "no reply within"),
    ],
)
def test_transfer_file_reports_what_the_controller_answered(tmp_path, reply, expect):
    port = _FakeFilePort(reply=reply)
    result = transfer_file(_FileRpc(), "127.0.0.1", _lua(tmp_path), port=port.port, timeout=0.3)
    port.join()
    assert result.stage == "reply"
    assert expect in result.detail


def _upload_service(monkeypatch, rpc, transfer):
    service = WeldFlexRobotService("127.0.0.1")
    raw = SimpleNamespace(robot=rpc, ip_address="192.168.58.2", LuaDelete=lambda name: 0)
    monkeypatch.setattr(service, "_call", lambda fn, **kwargs: fn(raw))
    monkeypatch.setattr("robot_service.transfer_file", transfer)
    return service


def test_upload_program_names_the_failed_step(monkeypatch, tmp_path):
    rpc = _FileRpc()
    service = _upload_service(
        monkeypatch, rpc,
        lambda r, host, path: FileTransferResult("connect", f"could not connect to {host}:20010"),
    )
    with pytest.raises(RuntimeError, match=r"failed at the connect step: could not connect to 192\.168\.58\.2:20010"):
        service.upload_program(str(_lua(tmp_path)))
    # The post-upload check never runs after a failed transfer.
    assert rpc.calls == []


def test_upload_program_reports_the_controllers_own_check_refusal(monkeypatch, tmp_path):
    rpc = _FileRpc(check=(-1, "line 3: pcall is not allowed"))
    service = _upload_service(monkeypatch, rpc, lambda r, host, path: FileTransferResult("ok"))
    with pytest.raises(RuntimeError, match="post-upload check: line 3: pcall is not allowed"):
        service.upload_program(str(_lua(tmp_path)))
    assert rpc.calls == [("LuaUpLoadUpdate", "goto.lua")]


def test_upload_program_succeeds_through_both_steps(monkeypatch, tmp_path):
    rpc = _FileRpc()
    service = _upload_service(monkeypatch, rpc, lambda r, host, path: FileTransferResult("ok"))
    assert service.upload_program(str(_lua(tmp_path)), replace=True) == "goto.lua"
    assert rpc.calls == [("LuaUpLoadUpdate", "goto.lua")]
