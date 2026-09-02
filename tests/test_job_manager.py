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
from lua_builder import build_weldflex_lua

STATE_MAP = {-1: "offline", 0: "stopped", 1: "stopped", 2: "running", 3: "paused"}

# Line values are taken from a real build of programs/WeldFlex.lua, which is what
# the manager itself measures at start(). BODY is a line inside the loop body; the
# loop head is never used, because the controller never reports it — assuming it
# did is how the counter came to latch after cycle 1 and the gate stopped re-arming.
_BUILT = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, gate_mode="none")
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

    def set_running_hint(self, running):
        self.running_hint = running

    def upload_program(self, path, replace=False):
        self._maybe_fail("upload_program")
        return "WeldFlex.lua"

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
    snap = mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=3, gate_mode="none")
    assert snap.state == JobState.QUEUED.value
    assert snap.part_name == "Bracket"
    assert snap.cycles_target == 3

    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    assert "upload_program" in robot.calls and "run_program" in robot.calls
    assert robot.running_hint is True
    mgr.shutdown()


def test_faceplate_kind_builds_with_the_faceplate_lua_builder(tmp_path, monkeypatch):
    """load(..., kind="faceplate") must route _launch to build_weld_faceplate_lua,
    not build_weldflex_lua — the two produce very different programs and a
    misrouted kind would silently run the wrong one."""
    import job_manager as jm

    calls = []
    real_build = jm.build_weld_faceplate_lua

    def spy(x, y, *args, **kwargs):
        calls.append((x, y))
        return real_build(x, y, *args, **kwargs)

    monkeypatch.setattr(jm, "build_weld_faceplate_lua", spy)

    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("__faceplate__", "Faceplate", [{"x": 12, "y": 34}], cycles=1,
             gate_mode="pause", kind="faceplate")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    assert calls == [(12, 34)]
    mgr.shutdown()


def test_faceplate_load_without_a_target_point_fails_at_launch(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("__faceplate__", "Faceplate", [], cycles=1, gate_mode="pause", kind="faceplate")
    mgr.start()
    wait_state(mgr, JobState.ERROR.value)
    assert "target point" in (mgr.snapshot().error or "")
    mgr.shutdown()


def test_load_accepts_arm_mode_and_passes_it_to_generated_program(tmp_path, monkeypatch):
    import job_manager as jm

    real_build = jm.build_weldflex_lua
    seen = []

    def spy(studs, cycles, gate_mode="pause", arm_mode="live", **kwargs):
        seen.append((arm_mode, kwargs.get("speed")))
        return real_build(studs, cycles, gate_mode=gate_mode, arm_mode=arm_mode, **kwargs)

    monkeypatch.setattr(jm, "build_weldflex_lua", spy)

    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load(
        "p1", "Bracket", [{"x": 1, "y": 2}], cycles=1,
        gate_mode="none", arm_mode="dry", speed=42,
    )
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    assert seen == [("dry", 42)]
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
    mgr = make_manager(tmp_path)
    if state == JobState.QUEUED.value:
        mgr.load("p1", "Bracket", [], cycles=1, gate_mode="none")
    with pytest.raises(JobError):
        getattr(mgr, command)()


def test_pause_resume_stop_round_trip(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=5, gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    assert mgr.pause().state == JobState.PAUSED.value
    with pytest.raises(JobError):
        mgr.pause()
    assert mgr.resume().state == JobState.RUNNING.value
    assert mgr.stop().state == JobState.STOPPED.value
    assert "stop_program" in robot.calls
    # Terminal: no further commands, but clear() returns to idle.
    with pytest.raises(JobError):
        mgr.pause()
    assert mgr.clear().state == JobState.IDLE.value


def test_clear_hands_the_cell_back_to_manual_mode(tmp_path):
    """A run leaves the controller in auto; clearing is where the operator gets it back."""
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=1, gate_mode="none")
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
    mgr.load("p1", "Bracket", [], cycles=1, gate_mode="none")
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
    mgr.load("p1", "Bracket", [], cycles=2, gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    snap = mgr.pause()
    assert snap.state == JobState.RUNNING.value   # the pause did not take
    assert "pause_program failed" in snap.error   # and the operator is told why
    mgr.shutdown()


def test_start_failure_ends_the_job_with_a_visible_reason(tmp_path):
    robot = FakeRobot(fail={"run_program"})
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=1, gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.ERROR.value)
    assert "run_program failed" in mgr.snapshot().error
    # error is re-startable, unlike the other terminal states
    mgr.start()
    wait_state(mgr, JobState.ERROR.value)


def test_load_is_refused_while_a_job_is_active(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.load("p1", "A", [], cycles=2, gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    with pytest.raises(JobError, match="stop it before loading another"):
        mgr.load("p2", "B", [], cycles=1, gate_mode="none")
    mgr.stop()
    mgr.load("p2", "B", [], cycles=1, gate_mode="none")   # allowed once terminal


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
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=2, gate_mode="none")
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


def test_lost_link_mid_run_is_interrupted_not_running(tmp_path):
    robot = FakeRobot()
    mgr = make_manager(tmp_path, robot)
    mgr.load("p1", "Bracket", [], cycles=5, gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    robot.snap.state = "faulted"
    wait_state(mgr, JobState.INTERRUPTED.value)
    assert "Lost connection" in mgr.snapshot().error


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
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=3, gate_mode="pause")
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
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=2, gate_mode="pause")
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
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=3, gate_mode="pause")
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
    mgr.load("p1", "Bracket", [{"x": 1, "y": 1}], cycles=3, gate_mode="pause")
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
    mgr.load("p1", "Bracket", [], cycles=10, gate_mode="none")
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
    mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=2, gate_mode="none")
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
    mgr.load("p1", "Bracket", [], cycles=9, gate_mode="none")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)
    mgr.shutdown()
    assert mgr.snapshot().state == JobState.INTERRUPTED.value
    record = json.loads((tmp_path / "run_history.jsonl").read_text(encoding="utf-8").strip())
    assert record["status"] == "interrupted"


# ---------------- snapshot ----------------


def test_snapshot_is_immutable_and_json_ready(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.load("p1", "Bracket", [{"x": 1, "y": 2}], cycles=4, gate_mode="none")
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
    mgr.load("p1", "Bracket", [{"x": 10, "y": 20}], cycles=2, gate_mode="pause")
    mgr.start()
    wait_state(mgr, JobState.RUNNING.value)

    # Controller hits Pause(0) at end of cycle 1 (program_state_raw=3 -> 'paused')
    robot.feed(line=75, program_state=3)
    wait_state(mgr, JobState.GATED.value)
    snap = mgr.snapshot()
    assert snap.cycles_done == 1
    assert snap.state == JobState.GATED.value

    # Operator presses Continue
    mgr.continue_()
    wait_state(mgr, JobState.RUNNING.value)

    mgr.shutdown()

