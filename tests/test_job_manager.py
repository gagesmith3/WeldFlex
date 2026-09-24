"""The job state machine and its persistence, against a stubbed robot service.

No SDK and no robot: `state_map` is injected so `job_manager` never reaches into
`robot_service`, and the stub answers every verb the manager calls.
"""

import json
import threading
import time
from dataclasses import dataclass

import pytest

from job_manager import (
    ACTIVE_STATES,
    MONITOR_INTERVAL_S,
    TERMINAL_STATES,
    JobError,
    JobManager,
    JobState,
)
from lua_builder import RunMode, build_weldflex_lua

STATE_MAP = {-1: "offline", 0: "stopped", 1: "stopped", 2: "running", 3: "paused"}

# Line values are taken from a real build of programs/WeldFlex.lua, which is what
# the manager itself measures at start(). BODY is a line inside the loop body; the
# loop head is never used, because the controller never reports it — assuming it
# did is how the counter came to latch after cycle 1 and the gate stopped re-arming.
_BUILT = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, run_mode=RunMode("dry"), gate_mode="none")
BODY = _BUILT.loop_start_line + 1        # inside the loop, below the boundary dwell
PAST_MARKER = _BUILT.cycle_marker_line   # the boundary dwell itself


@dataclass
class FakeSnap:
    state: str = "connected"
    connected: bool = True
    program_state_raw: int | None = 2
    current_line: int | None = BODY
    line_edge_seq: int = 0
    fault_main: int | None = None
    fault_sub: int | None = None


class FakeRobot:
    """Every verb JobManager calls, plus a scriptable line feed."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.snap = FakeSnap()
        self.calls: list[str] = []
        self.fail = fail or set()
        self.running_hint = None
        self._lock = threading.Lock()

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} failed (code -1)")

    def snapshot(self):
        with self._lock:
            return FakeSnap(**vars(self.snap))

    def get_universal_state(self):
        return self.snapshot()

    def set_running_hint(self, running):
        self.running_hint = running

    def upload_program(self, path, replace=False):
        self._maybe_fail("upload_program")
        return "WeldFlex.lua"

    def ft_config(self):
        self._maybe_fail("ft_config")
        return {"number": 7, "company": 24, "device": 0}

    def start_job_telemetry(self):
        self._maybe_fail("start_job_telemetry")

    def stop_job_telemetry(self):
        self._maybe_fail("stop_job_telemetry")

    def run_program(self, name):
        self._maybe_fail("run_program")

    def pause_program(self):
        self._maybe_fail("pause_program")

    def resume_program(self):
        self._maybe_fail("resume_program")

    def stop_program(self):
        self._maybe_fail("stop_program")

    def set_manual_mode(self):
        self._maybe_fail("set_manual_mode")

    # --- test control ---

    def feed(self, line, program_state=2):
        with self._lock:
            self.snap.current_line = line
            self.snap.line_edge_seq += 1
            self.snap.program_state_raw = program_state


def make_manager(tmp_path, robot=None, **kw):
    return JobManager(
        robot or FakeRobot(),
        history_path=tmp_path / "run_history.jsonl",
        events_path=tmp_path / "run_events.jsonl",
        state_map=STATE_MAP,
        **kw,
    )


def wait_for(fn, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    return None


def wait_state(mgr, state, timeout=5.0):
    got = wait_for(lambda: mgr.snapshot().state == state, timeout)
    assert got, f"expected {state!r}, still {mgr.snapshot().state!r}"


# ---------------- transitions ----------------


def test_starts_idle(tmp_path):
    mgr = make_manager(tmp_path)
    snap = mgr.snapshot()
    assert snap.state == JobState.IDLE.value
    assert snap.run_id is None
    assert not snap.active and not snap.terminal


def test_load_then_start_reaches_running(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    snap = mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=3, arm_mode="dry", gate_mode="none")
    assert snap.state == JobState.QUEUED.value
    assert snap.part_name == "Bracket"
    assert snap.cycles_target == 3

    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    assert "upload_program" in robot.calls and "run_program" in robot.calls
    assert "start_job_telemetry" in robot.calls
    assert robot.running_hint is True
    mgr.shutdown()


def test_monitor_uses_8083_first_observation_during_force_operations(tmp_path):
    """The RPC heartbeat stops during an F/T operation while 8083 still reports run."""
    robot = FakeRobot()
    rpc_snapshot = FakeSnap(state="faulted", connected=False, program_state_raw=1)
    feed_snapshot = FakeSnap(state="telemetry", connected=False, program_state_raw=2)
    robot.snapshot = lambda: rpc_snapshot
    robot.get_universal_state = lambda: feed_snapshot
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=1, arm_mode="dry", gate_mode="none")
    mgr.start()

    assert wait_for(lambda: mgr._session is not None and mgr._session.seen_running)
    assert mgr.snapshot().state == JobState.RUNNING.value
    mgr.shutdown()


def test_single_shot_kind_builds_with_the_single_shot_lua_builder(tmp_path, monkeypatch):
    """load(..., kind="single_shot") must route _launch to build_single_shot_lua,
    not build_weldflex_lua — the two produce very different programs and a
    misrouted kind would silently run the wrong one."""
    import job_manager as jm

    calls = []
    real_build = jm.build_single_shot_lua

    def spy(x, y, *args, **kwargs):
        calls.append((x, y, kwargs["run_mode"]))
        return real_build(x, y, *args, **kwargs)

    monkeypatch.setattr(jm, "build_single_shot_lua", spy)

    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("__single_shot__", "Single Shot", [{"x": 12, "y": 34}], cycles=1,
             arm_mode="live", di_check=False, gate_mode="none", kind="single_shot")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    assert calls == [(12, 34, RunMode("live", di_check=False))]
    mgr.shutdown()


def test_single_shot_load_without_a_target_point_fails_at_launch(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("__single_shot__", "Single Shot", [], cycles=1, arm_mode="dry",
             gate_mode="none", kind="single_shot")
    mgr.start()
    wait_state(mgr, JobState.ERROR.value)
    assert "target point" in (mgr.snapshot().error or "")
    mgr.shutdown()


def test_load_passes_the_run_mode_and_recipe_settings_to_the_builder(tmp_path, monkeypatch):
    import job_manager as jm

    monkeypatch.setenv("WELDFLEX_DSC_CALIBRATED", "1")
    real_build = jm.build_weldflex_lua
    seen = []

    def spy(studs, cycles, **kwargs):
        seen.append((kwargs["run_mode"], kwargs.get("speed"),
                     kwargs.get("dsc_enabled"), kwargs.get("stud_reload_ms")))
        return real_build(studs, cycles, **kwargs)

    monkeypatch.setattr(jm, "build_weldflex_lua", spy)

    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load(
        "p1", "Bracket", [{"x": 1, "y": 2}], cycles=1,
        arm_mode="dry", di_check=False, gate_mode="none", speed=42,
        dsc_enabled=True, stud_reload_ms=600,
    )
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    assert seen == [(RunMode("dry", di_check=False), 42, True, 600)]
    mgr.shutdown()


def test_load_passes_the_origin_corner_to_the_builder_and_the_load_event(tmp_path, monkeypatch):
    import job_manager as jm

    real_build = jm.build_weldflex_lua
    seen = []

    def spy(studs, cycles, **kwargs):
        seen.append((studs, kwargs.get("origin_corner")))
        return real_build(studs, cycles, **kwargs)

    monkeypatch.setattr(jm, "build_weldflex_lua", spy)

    mgr = make_manager(tmp_path, FakeRobot())
    mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=1,
             arm_mode="dry", gate_mode="none", origin_corner="back_left")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    # The builder gets the part's own numbers; resolving them is its job.
    assert seen == [([{"x": 1, "y": 2}], "back_left")]
    events = [
        json.loads(line)
        for line in (tmp_path / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    load_event = next(event for event in events if event["event"] == "load")
    assert load_event["detail"]["origin_corner"] == "back_left"
    mgr.shutdown()


@pytest.mark.parametrize("corner, studs, error", [
    ("top_right", [{"x": 1, "y": 2}], "Unknown origin corner"),
    ("front_right", [{"x": 1, "y": 2}, {"x": 900, "y": 2}], "Stud 2"),
])
def test_load_refuses_a_bad_corner_or_a_stud_that_would_flip_across_the_bed(
    tmp_path, monkeypatch, corner, studs, error
):
    """At load, not when Run is pressed: the builder only runs at start()."""
    monkeypatch.delenv("WELDFLEX_BED_X_MM", raising=False)
    mgr = make_manager(tmp_path, FakeRobot())
    with pytest.raises(JobError, match=error):
        mgr.load("p1", "Bracket", studs, cycles=1, arm_mode="dry",
                 gate_mode="none", origin_corner=corner)
    mgr.shutdown()


def test_run_mode_is_on_the_snapshot_the_history_and_the_load_event(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    snap = mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=3,
                    arm_mode="live", di_check=False, gate_mode="none")
    assert (snap.arm_mode, snap.di_check) == ("live", False)

    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    mgr.stop()

    record = json.loads((tmp_path / "run_history.jsonl").read_text(encoding="utf-8"))
    assert record["kind"] == "part"
    assert record["arm_mode"] == "live"
    assert record["di_check"] is False
    for retired in ("welder_profile", "liberty_commissioning", "weld_trigger_do", "weld_trigger_pulse_ms"):
        assert retired not in record
    events = [
        json.loads(line)
        for line in (tmp_path / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    load_event = next(event for event in events if event["event"] == "load")
    assert load_event["detail"]["arm_mode"] == "live"
    assert load_event["detail"]["di_check"] is False
    mgr.shutdown()


def test_load_has_no_default_arm_mode(tmp_path):
    """Live or Dry is picked for every run. A default of "live" is how the old
    faceplate page loaded live jobs nobody had chosen."""
    mgr = make_manager(tmp_path)
    with pytest.raises(TypeError):
        mgr.load("p1", "Bracket", [], cycles=1, gate_mode="none")
    with pytest.raises(JobError, match="live or dry"):
        mgr.load("p1", "Bracket", [], cycles=1, arm_mode="armed", gate_mode="none")
    assert mgr.snapshot().state == JobState.IDLE.value


def test_load_rejects_an_unknown_kind_or_a_non_boolean_di_check(tmp_path):
    mgr = make_manager(tmp_path)
    with pytest.raises(JobError, match="kind"):
        mgr.load("p1", "Bracket", [], cycles=1, arm_mode="dry", kind="liberty_endurance")
    with pytest.raises(JobError, match="DI check"):
        mgr.load("p1", "Bracket", [], cycles=1, arm_mode="dry", di_check="0")
    assert mgr.snapshot().state == JobState.IDLE.value


def test_launch_uses_the_controller_assigned_force_sensor_number(tmp_path, monkeypatch):
    import job_manager as jm

    real_build = jm.build_weldflex_lua
    seen = []

    def spy(studs, cycles, **kwargs):
        seen.append(kwargs["ft_sensor_num"])
        return real_build(studs, cycles, **kwargs)

    monkeypatch.setattr(jm, "build_weldflex_lua", spy)

    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=1, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    assert seen == [7]
    assert "ft_config" in robot.calls
    mgr.shutdown()


ILLEGAL = [
    ("start", JobState.IDLE.value),
    ("pause", JobState.IDLE.value),
    ("resume", JobState.IDLE.value),
    ("continue_", JobState.IDLE.value),
    ("stop", JobState.IDLE.value),
    ("pause", JobState.QUEUED.value),
    ("resume", JobState.QUEUED.value),
    ("continue_", JobState.QUEUED.value),
    ("stop", JobState.QUEUED.value),
]


@pytest.mark.parametrize("command,state", ILLEGAL)
def test_illegal_transitions_are_rejected_not_silently_applied(tmp_path, command, state):
    # With no active job, pause/resume/stop act on the controller's own program
    # instead — so these are only illegal while the controller has none running.
    robot = FakeRobot()
    robot.feed(BODY, program_state=0)
    mgr = make_manager(tmp_path, robot)
    if state == JobState.QUEUED.value:
        mgr.load("p1", "Bracket", [], cycles=1, arm_mode="dry", gate_mode="none")
    with pytest.raises(JobError):
        getattr(mgr, command)()


def test_pause_resume_stop_round_trip(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=5, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    assert mgr.pause().state == JobState.PAUSED.value
    with pytest.raises(JobError):
        mgr.pause()
    assert mgr.resume().state == JobState.RUNNING.value
    assert mgr.stop().state == JobState.STOPPED.value
    assert "stop_program" in robot.calls
    robot.feed(BODY, program_state=0)           # the controller reports the stop
    # Terminal: no further commands, but clear() returns to idle.
    with pytest.raises(JobError):
        mgr.pause()
    assert mgr.clear().state == JobState.IDLE.value


def test_clear_hands_the_cell_back_to_manual_mode(tmp_path):
    """A run leaves the controller in auto; clearing is where the operator gets it back."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=1, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    mgr.stop()

    snap = mgr.clear()
    assert snap.state == JobState.IDLE.value
    assert "set_manual_mode" in robot.calls
    assert snap.error is None


def test_a_failed_manual_handoff_still_clears_but_says_so(tmp_path):
    robot = FakeRobot(fail={"set_manual_mode"})
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=1, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    mgr.stop()

    snap = mgr.clear()
    assert snap.state == JobState.IDLE.value   # the job is gone either way
    assert "stayed in auto mode" in snap.error
    assert mgr.snapshot().state == JobState.IDLE.value

    events = [json.loads(l) for l in
              (tmp_path / "run_events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    clear_event = next(e for e in events if e["event"] == "clear")
    assert "set_manual_mode failed" in clear_event["detail"]["error"]


def test_failed_command_lands_on_the_session_error_not_an_exception(tmp_path):
    robot = FakeRobot(fail={"pause_program"})
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=2, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    snap = mgr.pause()
    assert snap.state == JobState.RUNNING.value   # the pause did not take
    assert "pause_program failed" in snap.error   # and the operator is told why
    mgr.shutdown()


def test_start_failure_ends_the_job_with_a_visible_reason(tmp_path):
    robot = FakeRobot(fail={"run_program"})
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=1, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.ERROR.value)
    assert "run_program failed" in mgr.snapshot().error
    # error is re-startable, unlike the other terminal states
    mgr.start()
    wait_state(mgr, JobState.ERROR.value)


def test_load_is_refused_while_a_job_is_active(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.load("p1", "A", [], cycles=2, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    with pytest.raises(JobError, match="stop it before loading another"):
        mgr.load("p2", "B", [], cycles=1, arm_mode="dry", gate_mode="none")
    mgr.stop()
    mgr.load("p2", "B", [], cycles=1, arm_mode="dry", gate_mode="none")   # allowed once terminal


# ---------------- cycle counting end to end ----------------


def run_one_cycle(robot, mgr, expect):
    """Drive the line feed through one loop iteration and wait for it to land."""
    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)
    robot.feed(PAST_MARKER)
    assert wait_for(lambda: mgr.snapshot().cycles_done == expect, 3.0), (
        f"expected {expect} cycles, got {mgr.snapshot().cycles_done}"
    )


def test_cycles_advance_with_nothing_polling(tmp_path):
    """The headline fix: progress is driven by the monitor thread, not the browser."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=2, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    run_one_cycle(robot, mgr, expect=1)
    run_one_cycle(robot, mgr, expect=2)

    robot.feed(PAST_MARKER, program_state=0)
    wait_state(mgr, JobState.COMPLETED.value)
    snap = mgr.snapshot()
    assert snap.cycles_done == 2
    assert len(snap.cycle_times) == 2
    assert snap.ended_at and snap.error is None


def test_lost_link_mid_run_is_interrupted_not_running(tmp_path, monkeypatch):
    import job_manager as jm

    monkeypatch.setattr(jm, "LINK_LOST_GRACE_S", 0.5)
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=5, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    robot.snap.state = "faulted"
    wait_state(mgr, JobState.INTERRUPTED.value)
    assert "Lost connection" in mgr.snapshot().error


def test_brief_link_fault_does_not_interrupt_the_run(tmp_path, monkeypatch):
    """The 2026-09-23 Pi regression: XML-RPC reconnected ~1 s after ProgramRun and
    the run was written off on the first faulted tick, because the grace timer read
    a `since_ts` that UniversalRobotState does not have. A blip shorter than the
    grace must leave the run going, and recovering must reset the timer."""
    import job_manager as jm

    monkeypatch.setattr(jm, "LINK_LOST_GRACE_S", 1.0)
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=5, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    for _ in range(2):  # two blips, together longer than the grace
        robot.snap.state = "faulted"
        time.sleep(0.7)
        robot.snap.state = "connected"
        time.sleep(MONITOR_INTERVAL_S * 3)

    assert mgr.snapshot().state == JobState.RUNNING.value
    mgr.shutdown()


def gate_one_cycle(robot, mgr, expect):
    """Run a cycle to the boundary and wait for the gate to hold there.

    `program_state=3` is the program pausing *itself* on the `Pause()` the
    builder emits at the gate line — that, not a host-issued ProgramPause, is
    what holds the robot.
    """
    robot.feed(BODY)
    # The re-arm needs a tick to land on this sample. The monitor is not a
    # metronome under load, so leave more than the nominal interval.
    time.sleep(MONITOR_INTERVAL_S * 4)
    robot.feed(PAST_MARKER, program_state=3)
    wait_state(mgr, JobState.GATED.value)
    assert mgr.snapshot().cycles_done == expect


def test_gate_pause_re_arms_on_every_cycle(tmp_path):
    """The 2026-07-28 regression: it gated after cycle 1 and then never again.

    A run that gates once and then sprints through the rest of its cycles is the
    dangerous failure here — nobody is swapping parts — so this drives the full
    gate/continue/gate/continue round trip rather than stopping at the first hold.
    """
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=3, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    gate_one_cycle(robot, mgr, expect=1)
    # The program held itself, so the manager sent nothing to get there.
    assert "pause_program" not in robot.calls
    assert mgr.continue_().state == JobState.RUNNING.value

    gate_one_cycle(robot, mgr, expect=2)           # the one that used to never come
    assert mgr.continue_().state == JobState.RUNNING.value

    # Last cycle: the target is met, so it finishes instead of gating again.
    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)
    robot.feed(PAST_MARKER)
    wait_for(lambda: mgr.snapshot().cycles_done == 3, 3.0)
    robot.feed(PAST_MARKER, program_state=0)
    wait_state(mgr, JobState.COMPLETED.value)

    snap = mgr.snapshot()
    assert snap.cycles_done == 3
    assert snap.error is None


def test_newdofile_aliased_line_does_not_bank_or_gate_early(tmp_path):
    """2026-08-06 live bug, first caught on weld_faceplate.lua.

    GetCurrentLine reports weld.lua's *own* line numbers (up to ~500) for the
    whole time it runs under NewDofile. Without a ceiling those numbers alias
    past cycle_marker_line (~88 here) and the tracker banks + gates seconds
    into the very first weld — the pause never lands where it should, and the
    robot just runs straight through looking like a normal completed cycle.
    """
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=2, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)
    # A plausible mid-weld.lua sample -- well past PAST_MARKER numerically,
    # but it is weld.lua's line, not the caller's.
    robot.feed(350)
    time.sleep(MONITOR_INTERVAL_S * 2)

    assert mgr.snapshot().cycles_done == 0
    assert mgr.snapshot().state == JobState.RUNNING.value

    # Only once control genuinely returns to the caller and reaches the real
    # marker should it bank and gate.
    robot.feed(PAST_MARKER, program_state=3)
    wait_state(mgr, JobState.GATED.value)
    assert mgr.snapshot().cycles_done == 1


def test_the_gate_waits_for_the_programs_own_pause(tmp_path):
    """2026-08-06: the gate moved into the Lua, and this is what that means.

    The manager banks the cycle at the marker — the *start* of the boundary
    dwell — but the program's `Pause()` is the line after the dwell, so paused
    does not get reported for BOUNDARY_MS yet. Until it does, the job must stay
    RUNNING and the manager must not go firing a ProgramPause of its own.
    """
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=3, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)
    robot.feed(PAST_MARKER)                     # still running: inside the dwell
    time.sleep(MONITOR_INTERVAL_S * 3)
    assert mgr.snapshot().cycles_done == 1
    assert mgr.snapshot().state == JobState.RUNNING.value

    robot.feed(PAST_MARKER + 1, program_state=3)   # Pause() ran
    wait_state(mgr, JobState.GATED.value)
    assert "pause_program" not in robot.calls
    mgr.shutdown()


def test_a_gate_that_cannot_hold_stops_the_job(tmp_path, monkeypatch):
    """Fail closed. A gate that cannot pause must not let the program run on.

    This is the backstop path: the program never reports paused, so the manager
    falls back to a host-issued ProgramPause — and when *that* fails too, the
    run ends rather than carrying on with nobody swapping parts. The dwell is
    shortened so the test does not sit through the real one waiting for a
    `Pause()` that is never coming.
    """
    monkeypatch.setenv("WELDFLEX_BOUNDARY_PAUSE_MS", "50")
    robot = FakeRobot(fail={"pause_program"})
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=3, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)
    robot.feed(PAST_MARKER)

    wait_state(mgr, JobState.ERROR.value, timeout=8.0)
    assert "Could not hold at the cycle boundary" in mgr.snapshot().error
    assert robot.calls.count("pause_program") == 2      # tried once, retried once
    assert "stop_program" in robot.calls                # then put the robot down
    mgr.shutdown()


def test_program_ending_early_is_stopped_with_a_partial_count(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=10, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    robot.feed(BODY)
    robot.feed(PAST_MARKER)
    wait_for(lambda: mgr.snapshot().cycles_done == 1, 2.0)
    robot.feed(PAST_MARKER, program_state=0)
    wait_state(mgr, JobState.STOPPED.value)
    assert "1 of 10" in mgr.snapshot().error


# ---------------- persistence ----------------


def test_history_and_events_are_written_as_jsonl(tmp_path):
    finished = []
    mgr = make_manager(tmp_path, on_finish=finished.append)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=2, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    mgr.stop()

    history = (tmp_path / "run_history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(history) == 1
    record = json.loads(history[0])
    assert record["part_id"] == "p1"
    assert record["part_name"] == "Bracket"
    assert record["status"] == "stopped"
    assert record["cycles_target"] == 2
    assert record["started_at"] and record["ended_at"]
    assert finished == [record]

    events = [json.loads(l) for l in
              (tmp_path / "run_events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [e["event"] for e in events][:2] == ["load", "start"]
    assert all(e["run_id"] == record["run_id"] for e in events)


def test_a_truncated_history_line_is_skipped_not_fatal(tmp_path):
    mgr = make_manager(tmp_path)
    path = tmp_path / "run_history.jsonl"
    good = {"run_id": "a", "part_name": "A", "cycles_target": 3, "cycles_done": 3,
            "started_at": "2026-07-27T08:00:00", "status": "completed"}
    # A power cut mid-write costs one line, not the file.
    path.write_text(json.dumps(good) + "\n" + '{"run_id": "b", "cycles_', encoding="utf-8")
    assert [r["run_id"] for r in mgr.history()] == ["a"]


def test_today_stats_only_counts_today(tmp_path):
    from datetime import datetime

    mgr = make_manager(tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    rows = [
        {"started_at": f"{today}T08:00:00", "cycles_target": 10, "cycles_done": 9},
        {"started_at": f"{today}T09:00:00", "cycles_target": 5, "cycles_done": 5},
        {"started_at": "1999-01-01T09:00:00", "cycles_target": 99, "cycles_done": 99},
    ]
    (tmp_path / "run_history.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    assert mgr.today_stats() == (15, 14)


def test_history_is_newest_first(tmp_path):
    mgr = make_manager(tmp_path)
    rows = [{"run_id": str(i), "started_at": f"2026-07-2{i}T08:00:00"} for i in range(1, 4)]
    (tmp_path / "run_history.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    assert [r["run_id"] for r in mgr.history()] == ["3", "2", "1"]


def test_shutdown_records_an_in_flight_job_as_interrupted(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.load("p1", "Bracket", [], cycles=9, arm_mode="dry", gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    mgr.shutdown()
    assert mgr.snapshot().state == JobState.INTERRUPTED.value
    record = json.loads((tmp_path / "run_history.jsonl").read_text(encoding="utf-8").strip())
    assert record["status"] == "interrupted"


# ---------------- snapshot ----------------


def test_snapshot_is_immutable_and_json_ready(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=4, arm_mode="dry", gate_mode="none")
    snap = mgr.snapshot()
    with pytest.raises(Exception):
        snap.state = "running"          # frozen dataclass
    d = snap.to_dict()
    json.dumps(d)                        # renderable and serialisable
    assert d["progress_pct"] == 0.0
    assert d["cycles_target"] == 4
    assert d["stud_count"] == 1


def test_state_sets_are_disjoint():
    assert not ACTIVE_STATES & TERMINAL_STATES
    assert JobState.IDLE.value not in ACTIVE_STATES | TERMINAL_STATES


def test_direct_controller_paused_state_gates_job(tmp_path):
    """When the controller program executes Pause(0) at the cycle boundary,
    program_state becomes 3 ('paused'). JobManager must transition directly
    to GATED and bank the cycle without depending on line-number polling."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 10, "y": 20}], cycles=2, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    # Controller hits Pause(0) at end of cycle 1 (program_state_raw=3 -> 'paused').
    # The dwell was never sampled, so this reading is the cycle's only count.
    robot.feed(line=PAST_MARKER + 1, program_state=3)
    wait_state(mgr, JobState.GATED.value)
    snap = mgr.snapshot()
    assert snap.cycles_done == 1
    assert snap.state == JobState.GATED.value

    # Operator presses Continue
    mgr.continue_()
    wait_state(mgr, JobState.RUNNING.value)

    mgr.shutdown()


# ---------------- operator pause/resume vs. the program's own gate ----------------


def test_resume_does_not_read_the_stale_pause_as_the_gate(tmp_path):
    """After Resume the cache still says "paused" for a heartbeat (0.5 s on the Pi,
    which has no 8083 feed). That reading used to bank a phantom cycle and put up
    the swap-the-part prompt; on a 1-cycle run it then completed the job while the
    robot was still moving."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=1, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)

    assert mgr.pause().state == JobState.PAUSED.value
    robot.feed(BODY, program_state=3)           # the controller confirms the pause
    time.sleep(MONITOR_INTERVAL_S * 2)
    assert mgr.resume().state == JobState.RUNNING.value
    time.sleep(MONITOR_INTERVAL_S * 4)          # cache still reads paused

    snap = mgr.snapshot()
    assert snap.state == JobState.RUNNING.value
    assert snap.cycles_done == 0
    mgr.shutdown()


def test_operator_pause_mid_body_is_never_the_gate(tmp_path, monkeypatch):
    """A pause away from the boundary is not the program's gate, whoever sent it —
    even once the resume settle window has long passed."""
    import job_manager as jm

    monkeypatch.setattr(jm, "COMMAND_SETTLE_S", 0.1)
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=2, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    robot.feed(BODY, program_state=3)
    time.sleep(MONITOR_INTERVAL_S * 4)
    snap = mgr.snapshot()
    # Followed as a plain pause (see the adoption tests), never as the gate.
    assert snap.state != JobState.GATED.value
    assert snap.cycles_done == 0
    mgr.shutdown()


def test_pause_landing_before_the_command_returns_is_not_the_gate(tmp_path):
    """The session is still RUNNING while ProgramPause is on the wire. If the
    controller reports paused before the call returns, that is the operator's
    pause, not the gate — even at the boundary."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=2, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)

    def slow_pause():
        robot.calls.append("pause_program")
        robot.feed(BODY, program_state=3)
        time.sleep(MONITOR_INTERVAL_S * 4)      # monitor ticks while the call is out

    robot.pause_program = slow_pause
    assert mgr.pause().state == JobState.PAUSED.value
    assert mgr.snapshot().cycles_done == 0
    mgr.shutdown()


def test_a_gated_cycle_is_counted_once(tmp_path):
    """The dwell banks the cycle; the program's Pause() a few seconds later must
    not bank it again. Both used to count, so every gated cycle counted twice."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=3, arm_mode="dry", gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)
    robot.feed(PAST_MARKER)                     # the dwell: banks cycle 1
    time.sleep(MONITOR_INTERVAL_S * 3)
    robot.feed(PAST_MARKER + 1, program_state=3)   # Pause() at the gate
    wait_state(mgr, JobState.GATED.value)

    snap = mgr.snapshot()
    assert snap.cycles_done == 1
    assert len(snap.cycle_times) == 1
    mgr.shutdown()


# ---------------- the job follows the controller, like the vendor web app ----------------


@pytest.fixture
def fast_adopt(monkeypatch):
    import job_manager as jm

    monkeypatch.setattr(jm, "EXTERNAL_ADOPT_S", 0.3)
    monkeypatch.setattr(jm, "COMMAND_SETTLE_S", 0.5)


def running_job(tmp_path, robot, gate_mode="none", cycles=3):
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=cycles, arm_mode="dry",
             gate_mode=gate_mode)
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    robot.feed(BODY)
    time.sleep(MONITOR_INTERVAL_S * 2)
    return mgr


def test_a_pause_from_the_pendant_is_followed(tmp_path, fast_adopt):
    robot = FakeRobot()
    mgr = running_job(tmp_path, robot)
    robot.feed(BODY, program_state=3)
    wait_state(mgr, JobState.PAUSED.value)
    assert "pause_program" not in robot.calls   # we sent nothing; we followed
    assert mgr.snapshot().cycles_done == 0

    # ...and so is the resume, and our own Resume button still works after.
    robot.feed(BODY + 1, program_state=2)
    wait_state(mgr, JobState.RUNNING.value)
    assert "resume_program" not in robot.calls
    mgr.shutdown()


def test_a_single_stale_reading_is_not_adopted(tmp_path, monkeypatch):
    """One "paused" reading shorter than EXTERNAL_ADOPT_S must not flip the job."""
    import job_manager as jm

    monkeypatch.setattr(jm, "EXTERNAL_ADOPT_S", 1.5)
    robot = FakeRobot()
    mgr = running_job(tmp_path, robot)
    robot.feed(BODY, program_state=3)
    time.sleep(MONITOR_INTERVAL_S * 2)
    robot.feed(BODY + 1, program_state=2)
    time.sleep(MONITOR_INTERVAL_S * 6)
    assert mgr.snapshot().state == JobState.RUNNING.value
    mgr.shutdown()


def test_releasing_the_gate_from_the_pendant_is_followed(tmp_path, fast_adopt):
    robot = FakeRobot()
    mgr = running_job(tmp_path, robot, gate_mode="pause")
    robot.feed(PAST_MARKER + 1, program_state=3)
    wait_state(mgr, JobState.GATED.value)
    robot.feed(BODY, program_state=2)
    wait_state(mgr, JobState.RUNNING.value)
    assert mgr.snapshot().cycles_done == 1
    mgr.shutdown()


def test_our_own_pause_is_not_undone_by_the_stale_running_reading(tmp_path, fast_adopt):
    """After Pause the cache still says running. That must not read as a resume
    from the pendant and flip the job straight back."""
    robot = FakeRobot()
    mgr = running_job(tmp_path, robot)
    assert mgr.pause().state == JobState.PAUSED.value
    time.sleep(0.45)                            # past EXTERNAL_ADOPT_S, inside the settle
    assert mgr.snapshot().state == JobState.PAUSED.value
    mgr.shutdown()


def test_a_stop_from_the_pendant_while_paused_ends_the_job(tmp_path, fast_adopt):
    robot = FakeRobot()
    mgr = running_job(tmp_path, robot)
    assert mgr.pause().state == JobState.PAUSED.value
    robot.feed(BODY, program_state=3)
    time.sleep(0.6)
    robot.feed(BODY, program_state=0)
    wait_state(mgr, JobState.STOPPED.value)
    mgr.shutdown()


def test_with_no_job_the_buttons_drive_the_controller_program(tmp_path):
    """Any program, WeldFlex's or not: pause/resume/stop act on the controller."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)

    robot.feed(BODY, program_state=2)
    assert mgr.pause().state == JobState.IDLE.value
    assert robot.calls[-1] == "pause_program"
    robot.feed(BODY, program_state=3)
    with pytest.raises(JobError):
        mgr.pause()
    mgr.resume()
    assert robot.calls[-1] == "resume_program"
    mgr.stop()
    assert robot.calls[-1] == "stop_program"
    robot.feed(BODY, program_state=0)
    with pytest.raises(JobError):
        mgr.stop()


def test_stop_reaches_a_program_a_finished_job_left_running(tmp_path, monkeypatch):
    """"Lost connection" ends the job but not the robot program. Stop must still
    be able to reach it."""
    import job_manager as jm

    monkeypatch.setattr(jm, "LINK_LOST_GRACE_S", 0.3)
    robot = FakeRobot()
    mgr = running_job(tmp_path, robot)
    robot.snap.state = "faulted"
    wait_state(mgr, JobState.INTERRUPTED.value)
    robot.snap.state = "connected"
    mgr.stop()
    assert robot.calls[-1] == "stop_program"


def test_events_for_run_reads_the_rotated_file_too(tmp_path):
    """The events log rotates to `.1` at EVENTS_MAX_BYTES. A run's trail must
    survive that, including a run that straddled the rotation."""
    mgr = make_manager(tmp_path)
    rotated = tmp_path / "run_events.jsonl.1"
    live = tmp_path / "run_events.jsonl"
    rotated.write_text(
        json.dumps({"ts": "t1", "run_id": "old", "event": "load", "detail": {}}) + "\n"
        + json.dumps({"ts": "t2", "run_id": "span", "event": "load", "detail": {}}) + "\n",
        encoding="utf-8",
    )
    live.write_text(
        json.dumps({"ts": "t3", "run_id": "span", "event": "completed", "detail": {}}) + "\n"
        + "not json\n",
        encoding="utf-8",
    )

    assert [e["event"] for e in mgr.events_for_run("old")] == ["load"]
    assert [e["ts"] for e in mgr.events_for_run("span")] == ["t2", "t3"]
    assert mgr.events_for_run("missing") == []
