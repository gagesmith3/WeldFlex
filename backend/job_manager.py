"""The current job: what it is, how far along it is, and how it ended.

Layering: `robot_link` is *being connected*, `robot_service` is the *verb layer*,
and this is the third concern — *what job are we running*. It calls into
`robot_service` and never touches `robot_link` or the SDK directly. `app.py`
routes are thin adapters over `JobManager`.

Cycle progress is driven by a monitor thread, not by browser polling, so a job
keeps advancing with the kiosk tab closed. The thread reads only the link's
cached snapshot — it issues no robot I/O of its own.

Two rules the concurrency here depends on:

* **No robot I/O and no file I/O under `_lock`.** An SDK call can block for
  seconds and the lock is on the path of every status poll. The monitor computes
  transitions under the lock and returns deferred actions the caller runs after
  releasing it.
* **Every mutation is `run_id`-checked.** A stale thread from a finished job
  cannot touch the session of the next one.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from lua_builder import (
    GATE_MODES,
    PROGRAM_NAME,
    WELD_PATH,
    WELD_PROGRAM_NAME,
    build_weldflex_lua,
    strip_lua_comments,
)

log = logging.getLogger("weldflex.job")

MONITOR_INTERVAL_S = 0.25
# A program can still read "stopped" for a moment after ProgramRun returns, so
# "stopped" only means end-of-program once "running" has actually been observed.
# If it never is, the job failed to launch and should say so rather than hang.
STARTUP_TIMEOUT_S = 20.0
# Belt-and-braces finish once the target count is reached, in case the controller
# never reports the stopped edge. Same idea as the old Liberty fallback.
COMPLETION_FALLBACK_S = 4.0

EVENTS_MAX_BYTES = 512 * 1024
HISTORY_TAIL_LINES = 500


class JobState(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    GATED = "gated"              # holding at a cycle boundary for the part swap
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"
    INTERRUPTED = "interrupted"  # the link died mid-run


ACTIVE_STATES = frozenset({
    JobState.STARTING.value, JobState.RUNNING.value,
    JobState.PAUSED.value, JobState.GATED.value,
})
TERMINAL_STATES = frozenset({
    JobState.COMPLETED.value, JobState.STOPPED.value,
    JobState.ERROR.value, JobState.INTERRUPTED.value,
})


class JobError(RuntimeError):
    """A command was issued from a state that does not allow it."""


def _now_iso() -> str:
    # Local time on purpose: the operator reads these on the shop floor, and the
    # today-stats rollup matches on a local YYYY-MM-DD prefix.
    return datetime.now().isoformat(timespec="seconds")


class CycleTracker:
    """Counts completed cycles from a stream of `GetCurrentLine` samples.

    One signal banks a cycle: **the boundary dwell** — a sample at or past
    `cycle_marker_line`. The marker is a `WaitMs` long enough that the 250 ms poll
    cannot step over it. A second sample below the marker then re-arms the counter
    for the next cycle.

    Two things this deliberately does *not* do, both learned the hard way:

    * It does not wait to see `loop_start_line` to re-arm. That is the `for
      cycleIndex` statement, the single lowest-numbered line in the loop, and it
      executes in microseconds — a 250 ms sampler never lands on it. Keying the
      re-arm on it latched the counter after cycle 1, so `cycles_done` stuck at 1
      and the inter-cycle gate never fired again (2026-07-28 bring-up).
    * It does not treat "the reported line went backwards" as a wrap. The **inner
      stud loop** makes body lines non-monotonic within a single cycle, so a
      backwards jump cannot tell an inner iteration from an outer one.

    The cost of dropping those is that the boundary dwell is now the only thing
    that banks a cycle: miss every sample across the whole dwell and the count
    stalls. That is why the dwell is long, and longer still in `pause` gate mode.

    A part with zero studs has no executable line below the marker at all, so its
    cycles cannot be re-armed. That is a config error the builder already flags.

    Repeated identical samples are ignored via the link's `line_edge_seq`, which
    only advances when the reported line actually changes.
    """

    def __init__(self, loop_start_line: int, cycle_marker_line: int, cycles_target: int = 0) -> None:
        self.loop_start_line = int(loop_start_line)
        self.cycle_marker_line = int(cycle_marker_line)
        self.cycles_target = int(cycles_target)
        self.cycles_done = 0
        self._counted_this_cycle = False
        self._last_edge_seq: int | None = None

    def observe(self, line: Any, edge_seq: int | None = None) -> bool:
        """Feed one snapshot. Returns True when a cycle was just banked."""
        if not isinstance(line, int) or isinstance(line, bool):
            return False
        if edge_seq is not None:
            if edge_seq == self._last_edge_seq:
                return False
            self._last_edge_seq = edge_seq

        if line >= self.cycle_marker_line:
            if self._counted_this_cycle:
                return False
            return self._bank()

        # Below the boundary dwell but inside the loop. The inner stud loop never
        # crosses the marker, so arriving here after the marker was banked can only
        # be the outer `for` wrapping into the next cycle. Level-triggered rather
        # than edge-triggered on purpose: the wrap itself usually happens while the
        # job is GATED, when nothing is feeding this at all.
        if self._counted_this_cycle and line >= self.loop_start_line:
            self._counted_this_cycle = False
        return False

    def _bank(self) -> bool:
        self._counted_this_cycle = True
        if self.cycles_target and self.cycles_done >= self.cycles_target:
            return False
        self.cycles_done += 1
        return True


@dataclass(frozen=True)
class JobSnapshot:
    """Immutable view of the current job — mirrors `ConnSnapshot`.

    Templates render from one of these rather than a shared mutable dict, so the
    lock is never held across `render_template`.
    """

    state: str = JobState.IDLE.value
    run_id: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    program: str = PROGRAM_NAME
    gate_mode: str = "pause"
    stud_count: int = 0
    cycles_target: int = 0
    cycles_done: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    cycle_times: tuple[float, ...] = ()
    current_cycle_s: float | None = None
    elapsed_s: float | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def progress_pct(self) -> float:
        if not self.cycles_target:
            return 0.0
        return min(100.0, self.cycles_done / self.cycles_target * 100.0)

    @property
    def avg_cycle_s(self) -> float | None:
        if not self.cycle_times:
            return None
        return sum(self.cycle_times) / len(self.cycle_times)

    def to_dict(self) -> dict[str, Any]:
        d = {name: getattr(self, name) for name in self.__dataclass_fields__}
        d["cycle_times"] = list(self.cycle_times)
        d["active"] = self.active
        d["terminal"] = self.terminal
        d["progress_pct"] = self.progress_pct
        d["avg_cycle_s"] = self.avg_cycle_s
        return d


@dataclass
class _Session:
    """Mutable working state. Only ever touched under `JobManager._lock`."""

    run_id: str
    state: str = JobState.QUEUED.value
    part_id: str | None = None
    part_name: str | None = None
    studs: list = field(default_factory=list)
    program: str = PROGRAM_NAME
    gate_mode: str = "pause"
    cycles_target: int = 0
    safe_z: float = 10.0
    part_z: float = 0.0
    pressure_setting: str = "high"
    stud_type: str = "M4"
    substrate: str = "Mild Steel"
    started_at: str | None = None
    started_ts: float | None = None
    ended_at: str | None = None
    ended_ts: float | None = None
    error: str | None = None
    cycle_times: list = field(default_factory=list)
    cycle_start_ts: float | None = None
    tracker: CycleTracker | None = None
    loop_start_line: int = 0
    cycle_marker_line: int = 0
    gate_line: int = 0
    seen_running: bool = False
    launched_ts: float | None = None
    completed_since: float | None = None
    gate_pending: bool = False

    @property
    def cycles_done(self) -> int:
        return self.tracker.cycles_done if self.tracker else 0


# Deferred actions a monitor tick can ask for, run by the caller once the lock is
# released. ("gate",) or ("finish", status, error).
_GATE = ("gate",)


class JobManager:
    """Owns the current job. Thread-safe; all reads go through `snapshot()`."""

    def __init__(
        self,
        robot: Any,
        history_path: str | os.PathLike | None = None,
        events_path: str | os.PathLike | None = None,
        on_finish: Callable[[dict], None] | None = None,
        state_map: dict[int | None, str] | None = None,
    ) -> None:
        self._robot = robot
        base = Path(__file__).resolve().parent
        self._history_path = Path(history_path or base / "run_history.jsonl")
        self._events_path = Path(events_path or base / "run_events.jsonl")
        self._on_finish = on_finish
        self._state_map = state_map

        self._lock = threading.Lock()
        self._session: _Session | None = None
        self._monitor: threading.Thread | None = None
        self._stop = threading.Event()
        self._file_lock = threading.Lock()

    # ---------------- commands ----------------

    def load(
        self,
        part_id: str,
        part_name: str,
        studs: Sequence[dict],
        cycles: int,
        gate_mode: str = "pause",
        safe_z: float = 10.0,
        part_z: float = 0.0,
        pressure_setting: str = "high",
        stud_type: str = "M4",
        substrate: str = "Mild Steel",
    ) -> JobSnapshot:
        """Queue a part for running. Rejected while a job is still active.

        A terminal job is replaced rather than blocking the load — `clear()` is for
        dismissing the result deliberately, not a precondition for the next job.
        """
        if gate_mode not in GATE_MODES:
            raise JobError(f"Unknown gate mode {gate_mode!r}")
        cycles = max(1, int(cycles))
        with self._lock:
            state = self._state_locked()
            if state in ACTIVE_STATES:
                raise JobError(f"A job is already {state} — stop it before loading another")
            run_id = str(uuid.uuid4())
            self._session = _Session(
                run_id=run_id,
                state=JobState.QUEUED.value,
                part_id=part_id,
                part_name=part_name,
                studs=list(studs),
                gate_mode=gate_mode,
                cycles_target=cycles,
                safe_z=float(safe_z),
                part_z=float(part_z),
                pressure_setting=str(pressure_setting),
                stud_type=str(stud_type),
                substrate=str(substrate),
            )
            snap = self._snapshot_locked()
        log.info("job loaded run_id=%s part=%r cycles=%d gate=%s studs=%d",
                 run_id, part_name, cycles, gate_mode, len(studs))
        self._event(run_id, "load", {"part_id": part_id, "part_name": part_name,
                                     "cycles": cycles, "gate_mode": gate_mode,
                                     "studs": len(studs)})
        return snap

    def start(self) -> JobSnapshot:
        """Build, upload and run the program. Returns immediately in `starting`.

        The robot work — `Mode(0)`, a 2 s settle, ProgramLoad, ProgramRun — runs on
        our own thread rather than the Flask request thread, so the POST does not
        block the UI for the length of a program load.
        """
        with self._lock:
            sess = self._session
            state = self._state_locked()
            if sess is None or state not in (JobState.QUEUED.value, JobState.ERROR.value):
                raise JobError(f"Cannot start from state {state!r}")
            sess.state = JobState.STARTING.value
            sess.error = None
            sess.ended_at = None
            sess.ended_ts = None
            sess.started_at = _now_iso()
            sess.started_ts = time.time()
            run_id = sess.run_id
            snap = self._snapshot_locked()

        log.info("job starting run_id=%s", run_id)
        self._event(run_id, "start", {})
        threading.Thread(
            target=self._launch, args=(run_id,), daemon=True, name="job-launch"
        ).start()
        return snap

    def pause(self) -> JobSnapshot:
        return self._command(
            "pause", (JobState.RUNNING.value,), JobState.PAUSED.value, self._robot.pause_program
        )

    def resume(self) -> JobSnapshot:
        return self._command(
            "resume", (JobState.PAUSED.value,), JobState.RUNNING.value, self._robot.resume_program
        )

    def continue_(self) -> JobSnapshot:
        """Release the inter-cycle gate once the operator has swapped the part."""
        return self._command(
            "continue", (JobState.GATED.value,), JobState.RUNNING.value, self._robot.resume_program
        )

    def stop(self) -> JobSnapshot:
        with self._lock:
            sess = self._session
            state = self._state_locked()
            if sess is None or state not in ACTIVE_STATES:
                raise JobError(f"Cannot stop from state {state!r}")
            run_id = sess.run_id
        self._stop.set()
        error = None
        try:
            self._robot.stop_program()
        except Exception as exc:  # noqa: BLE001 - surfaced on the session, not swallowed
            error = str(exc)
            log.warning("ProgramStop failed run_id=%s: %s", run_id, exc)
        self._event(run_id, "stop_command", {"error": error})
        return self._finish(run_id, JobState.STOPPED.value, error=error)

    def clear(self) -> JobSnapshot:
        """Dismiss a finished job so the panel returns to idle, and hand the cell
        back in manual mode.

        Running a job puts the controller into auto (`run_program`'s `Mode(0)`) and
        nothing takes it out again when the program ends. Clearing is the point
        where the operator gets the robot back, so the green manual indicator
        should agree with the now-idle panel.

        A failed handoff does not undo the clear — the job is already gone. The
        reason rides back on the otherwise-idle snapshot for the panel to show.
        """
        with self._lock:
            state = self._state_locked()
            if state in ACTIVE_STATES:
                raise JobError(f"Cannot clear while {state}")
            run_id = self._session.run_id if self._session else None
            self._session = None
            snap = self._snapshot_locked()

        # Outside the lock: an SDK call can block for seconds.
        error = None
        try:
            self._robot.set_manual_mode()
        except Exception as exc:  # noqa: BLE001 - shown to the operator, not swallowed
            error = f"Job cleared, but the robot stayed in auto mode: {exc}"
            log.warning("manual-mode handoff failed after clear run_id=%s: %s", run_id, exc)
        if run_id:
            self._event(run_id, "clear", {"error": error})
        return replace(snap, error=error) if error else snap

    def _command(
        self, name: str, from_states: tuple[str, ...], to_state: str, call: Callable[[], None]
    ) -> JobSnapshot:
        """Pattern B: a failed command lands on the session's `error` field.

        The robot call happens outside the lock — it can block for seconds.
        """
        with self._lock:
            sess = self._session
            state = self._state_locked()
            if sess is None or state not in from_states:
                raise JobError(f"Cannot {name} from state {state!r}")
            run_id = sess.run_id
        try:
            call()
            error = None
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            log.warning("%s failed run_id=%s: %s", name, run_id, exc)
        with self._lock:
            sess = self._session
            if sess is not None and sess.run_id == run_id:
                if error:
                    sess.error = error
                else:
                    sess.state = to_state
                    sess.error = None
                    sess.gate_pending = False
                    if to_state == JobState.RUNNING.value:
                        # Restart the cycle clock on resume/continue so a recorded
                        # cycle time is machine time, not machine time plus however
                        # long the operator took to swap the part.
                        sess.cycle_start_ts = time.time()
            snap = self._snapshot_locked()
        self._event(run_id, name, {"error": error})
        return snap

    # ---------------- reads ----------------

    def snapshot(self) -> JobSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def history(self, limit: int = 20) -> list[dict]:
        """Most recent finished runs, newest first."""
        return list(reversed(self._read_history()))[:limit]

    def today_stats(self) -> tuple[int, int]:
        """(cycles attempted, cycles completed) across runs started today."""
        today = datetime.now().strftime("%Y-%m-%d")
        attempted = completed = 0
        for entry in self._read_history():
            if str(entry.get("started_at") or "").startswith(today):
                attempted += int(entry.get("cycles_target", 0) or 0)
                completed += int(entry.get("cycles_done", 0) or 0)
        return attempted, completed

    # ---------------- lifecycle ----------------

    def shutdown(self) -> None:
        """Stop the monitor and record an in-flight job as interrupted.

        Wired to the same atexit/SIGTERM hooks that close the RPC session, so a
        systemd restart does not leave a monitor thread mid-cycle or a run with no
        ending in the history.
        """
        with self._lock:
            sess = self._session
            run_id = sess.run_id if sess and sess.state in ACTIVE_STATES else None
        self._stop.set()
        if run_id:
            log.warning("shutting down with run_id=%s still active", run_id)
            self._finish(run_id, JobState.INTERRUPTED.value, error="Application shut down")
        monitor = self._monitor
        if monitor is not None and monitor.is_alive():
            monitor.join(timeout=2.0)

    # ---------------- internals ----------------

    def _state_locked(self) -> str:
        return self._session.state if self._session else JobState.IDLE.value

    def _snapshot_locked(self) -> JobSnapshot:
        sess = self._session
        if sess is None:
            return JobSnapshot()
        now = time.time()
        current_cycle_s = None
        if sess.state in ACTIVE_STATES and sess.cycle_start_ts:
            current_cycle_s = max(0.0, now - sess.cycle_start_ts)
        elapsed_s = None
        if sess.started_ts:
            end_ts = sess.ended_ts if sess.ended_ts else now
            elapsed_s = max(0.0, end_ts - sess.started_ts)
        return JobSnapshot(
            state=sess.state,
            run_id=sess.run_id,
            part_id=sess.part_id,
            part_name=sess.part_name,
            program=sess.program,
            gate_mode=sess.gate_mode,
            stud_count=len(sess.studs),
            cycles_target=sess.cycles_target,
            cycles_done=sess.cycles_done,
            started_at=sess.started_at,
            ended_at=sess.ended_at,
            error=sess.error,
            cycle_times=tuple(sess.cycle_times),
            current_cycle_s=current_cycle_s,
            elapsed_s=elapsed_s,
        )

    def _program_state(self, snap: Any) -> str:
        if self._state_map is None:
            from robot_service import STATE_MAP  # local: keeps the import graph one-way

            self._state_map = STATE_MAP
        return self._state_map.get(getattr(snap, "program_state_raw", None), "unknown")

    def _launch(self, run_id: str) -> None:
        """Build → upload → run, on our own thread. Any failure ends the job."""
        try:
            with self._lock:
                sess = self._session
                if sess is None or sess.run_id != run_id:
                    return
                studs = list(sess.studs)
                cycles = sess.cycles_target
                gate_mode = sess.gate_mode
                safe_z = sess.safe_z
                part_z = sess.part_z
                pressure_setting = sess.pressure_setting
                stud_type = sess.stud_type
                substrate = sess.substrate

            built = build_weldflex_lua(
                studs,
                cycles,
                gate_mode=gate_mode,
                safe_z=safe_z,
                part_z=part_z,
                pressure_setting=pressure_setting,
                stud_type=stud_type,
                substrate=substrate,
            )

            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, built.program_name)
            weld_tmp_path = os.path.join(tmp_dir, WELD_PROGRAM_NAME)
            try:
                # Upload weld.lua sub-process first so NewDofile executes the latest code.
                weld_text = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
                Path(weld_tmp_path).write_text(weld_text, encoding="utf-8")
                self._robot.upload_program(weld_tmp_path, replace=True)

                Path(tmp_path).write_text(built.text, encoding="utf-8")
                uploaded = self._robot.upload_program(tmp_path, replace=True)
            finally:
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except OSError:
                    pass

            self._robot.run_program(uploaded)

            with self._lock:
                sess = self._session
                if sess is None or sess.run_id != run_id:
                    return
                sess.program = uploaded
                sess.loop_start_line = built.loop_start_line
                sess.cycle_marker_line = built.cycle_marker_line
                sess.gate_line = built.gate_line
                sess.tracker = CycleTracker(built.loop_start_line, built.cycle_marker_line, cycles)
                sess.state = JobState.RUNNING.value
                sess.launched_ts = time.time()
                sess.cycle_start_ts = time.time()

            log.info("job running run_id=%s program=%s cycles=%d loop_start=%d marker=%d "
                     "gate=%d boundary_ms=%d",
                     run_id, uploaded, cycles, built.loop_start_line,
                     built.cycle_marker_line, built.gate_line, built.boundary_ms)
            self._event(run_id, "running", {
                "program": uploaded,
                "loop_start_line": built.loop_start_line,
                "cycle_marker_line": built.cycle_marker_line,
                "gate_line": built.gate_line,
                "boundary_ms": built.boundary_ms,
            })
            self._start_monitor(run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("job failed to start run_id=%s", run_id)
            self._event(run_id, "start_failed", {"error": str(exc)})
            self._finish(run_id, JobState.ERROR.value, error=str(exc))

    def _start_monitor(self, run_id: str) -> None:
        self._stop.set()
        self._stop.clear()
        self._monitor = threading.Thread(
            target=self._monitor_loop, args=(run_id,), daemon=True, name="job-monitor"
        )
        self._monitor.start()

    def _monitor_loop(self, run_id: str) -> None:
        # Probe fast while a program runs so `current_line` stays fresh enough for
        # the boundary dwell to be sampled.
        self._robot.set_running_hint(True)
        try:
            self._monitor_body(run_id)
        except Exception:  # noqa: BLE001
            log.exception("job monitor crashed run_id=%s", run_id)
            self._finish(run_id, JobState.ERROR.value, error="Monitor thread crashed")
        finally:
            self._robot.set_running_hint(False)

    def _monitor_body(self, run_id: str) -> None:
        while not self._stop.is_set():
            snap = self._robot.snapshot()
            program_state = self._program_state(snap)
            events: list[tuple[str, dict]] = []
            with self._lock:
                sess = self._session
                if sess is None or sess.run_id != run_id or sess.state not in ACTIVE_STATES:
                    return
                action = self._tick_locked(sess, snap, program_state, events)

            for name, detail in events:
                self._event(run_id, name, detail)

            if action == _GATE:
                self._gate(run_id)
            elif action is not None and action[0] == "finish":
                self._finish(run_id, action[1], error=action[2])
                return

            self._stop.wait(MONITOR_INTERVAL_S)

    def _tick_locked(
        self, sess: _Session, snap: Any, program_state: str, events: list
    ) -> tuple | None:
        """One monitor tick. Returns a deferred action for the caller to run unlocked."""
        # The link is degraded or faulted. Allow a 10s grace period for transient RPC stalls during motion.
        if getattr(snap, "state", None) == "faulted":
            since = getattr(snap, "since_ts", None)
            fault_duration = (time.time() - since) if since is not None else 999.0
            if fault_duration >= 10.0:
                return ("finish", JobState.INTERRUPTED.value, "Lost connection to the robot")
            return None

        if program_state == "running":
            sess.seen_running = True

        if sess.state == JobState.RUNNING.value and sess.tracker is not None:
            banked = sess.tracker.observe(
                getattr(snap, "current_line", None), getattr(snap, "line_edge_seq", None)
            )
            if banked:
                now = time.time()
                if sess.cycle_start_ts:
                    sess.cycle_times.append(round(now - sess.cycle_start_ts, 2))
                sess.cycle_start_ts = now
                done, target = sess.cycles_done, sess.cycles_target
                log.info("cycle %d/%d run_id=%s", done, target, sess.run_id)
                events.append(("cycle", {
                    "cycles_done": done,
                    "cycles_target": target,
                    "cycle_s": sess.cycle_times[-1] if sess.cycle_times else None,
                }))
                if sess.gate_mode == "pause" and done < target and not sess.gate_pending:
                    sess.gate_pending = True
                    return _GATE

        if getattr(snap, "fault_main", None):
            sess.error = f"Controller fault {snap.fault_main}/{snap.fault_sub}"

        done, target = sess.cycles_done, sess.cycles_target
        if target and done >= target:
            if sess.completed_since is None:
                sess.completed_since = time.time()
            elif program_state == "stopped" or (
                time.time() - sess.completed_since >= COMPLETION_FALLBACK_S
            ):
                return ("finish", JobState.COMPLETED.value, None)
            return None
        sess.completed_since = None

        # The program ended before the target count was reached.
        if sess.seen_running and program_state == "stopped" and sess.state == JobState.RUNNING.value:
            return ("finish", JobState.STOPPED.value,
                    f"Program ended after {done} of {target} cycles")

        if (
            not sess.seen_running
            and sess.launched_ts
            and time.time() - sess.launched_ts > STARTUP_TIMEOUT_S
        ):
            return ("finish", JobState.ERROR.value, "Program never reported running")

        return None

    def _gate(self, run_id: str) -> None:
        """Hold at the cycle boundary so the operator can swap the part.

        `pause` mode only. The pause lands wherever the robot is inside the
        boundary dwell rather than exactly on the gate line — acceptable for
        bring-up, which is why `di` is the production target.

        Fails closed. A gate that cannot hold is worse than no gate at all: the
        program would run its remaining cycles with nobody swapping parts. One
        retry covers a transient RPC hiccup; past that the run ends.
        """
        error = None
        for attempt in (1, 2):
            try:
                self._robot.pause_program()
                error = None
                break
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                log.warning("gate pause attempt %d failed run_id=%s: %s", attempt, run_id, exc)

        if error:
            try:
                self._robot.stop_program()
            except Exception:  # noqa: BLE001 - already heading for ERROR
                log.exception("stop after a failed gate also failed run_id=%s", run_id)
            self._event(run_id, "gate_failed", {"error": error})
            self._finish(run_id, JobState.ERROR.value,
                         error=f"Could not hold at the cycle boundary: {error}")
            return

        with self._lock:
            sess = self._session
            if sess is None or sess.run_id != run_id:
                return
            if sess.state == JobState.RUNNING.value:
                sess.state = JobState.GATED.value
        self._event(run_id, "gated", {"error": None})

    def _finish(self, run_id: str, status: str, error: str | None = None) -> JobSnapshot:
        """Move to a terminal state exactly once, and write the history record."""
        self._stop.set()
        with self._lock:
            sess = self._session
            if sess is None or sess.run_id != run_id or sess.state in TERMINAL_STATES:
                return self._snapshot_locked()
            sess.state = status
            sess.ended_at = _now_iso()
            sess.ended_ts = time.time()
            sess.cycle_start_ts = None
            sess.gate_pending = False
            if error:
                sess.error = error
            record = {
                "run_id": sess.run_id,
                "part_id": sess.part_id,
                "part_name": sess.part_name,
                "program": sess.program,
                "gate_mode": sess.gate_mode,
                "stud_count": len(sess.studs),
                "cycles_target": sess.cycles_target,
                "cycles_done": sess.cycles_done,
                "started_at": sess.started_at,
                "ended_at": sess.ended_at,
                "status": status,
                "error": sess.error,
                "cycle_times": list(sess.cycle_times),
                "safe_z": sess.safe_z,
                "part_z": sess.part_z,
                "pressure_setting": sess.pressure_setting,
                "stud_type": sess.stud_type,
                "substrate": sess.substrate,
            }
            snap = self._snapshot_locked()

        log.info("job %s run_id=%s cycles=%d/%d error=%s",
                 status, run_id, record["cycles_done"], record["cycles_target"], error)
        self._event(run_id, status, {"error": error, "cycles_done": record["cycles_done"]})
        self._append_line(self._history_path, record)
        if self._on_finish is not None:
            try:
                self._on_finish(record)
            except Exception:  # noqa: BLE001
                log.exception("on_finish callback failed run_id=%s", run_id)
        return snap

    # ---------------- persistence ----------------

    def _event(self, run_id: str | None, event: str, detail: dict) -> None:
        self._append_line(
            self._events_path,
            {"ts": _now_iso(), "run_id": run_id, "event": event, "detail": detail},
            cap_bytes=EVENTS_MAX_BYTES,
        )

    def _append_line(self, path: Path, payload: dict, cap_bytes: int | None = None) -> None:
        """JSONL, not a JSON array: append is O(1), there is no read-modify-write of
        the whole file, and a power cut mid-write costs one line instead of the lot."""
        try:
            with self._file_lock:
                if cap_bytes and path.exists() and path.stat().st_size > cap_bytes:
                    os.replace(path, str(path) + ".1")
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")
        except OSError:
            log.exception("failed to write %s", path.name)

    def _read_history(self) -> list[dict]:
        """Oldest first. A malformed line is skipped, not fatal."""
        try:
            with open(self._history_path, encoding="utf-8") as f:
                lines = f.readlines()[-HISTORY_TAIL_LINES:]
        except OSError:
            return []
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def events_for_run(self, run_id: str) -> list[dict]:
        """Fetch all logged events matching a given run_id."""
        if not run_id:
            return []
        out = []
        try:
            with open(self._events_path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if item.get("run_id") == run_id:
                    out.append(item)
            except json.JSONDecodeError:
                continue
        return out
