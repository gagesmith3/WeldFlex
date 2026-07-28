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
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from lua_builder import GATE_MODES, PROGRAM_NAME, build_weldflex_lua

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

    Two independent signals bank a cycle, and they cannot double-count each other:

    * **the boundary dwell** — a sample at or past `cycle_marker_line`. The normal
      path: the marker is a `WaitMs` long enough that a 250 ms poll cannot step
      over it.
    * **the loop wrap** — a jump back to at-or-before `loop_start_line` after
      having been past it. Within one cycle the reported line only advances, so a
      backwards jump to the loop head can only be the `for` wrapping. This fires
      even if the tail of the cycle was never sampled at all.

    Repeated identical samples are ignored via the link's `line_edge_seq`, which
    only advances when the reported line actually changes.
    """

    def __init__(self, loop_start_line: int, cycle_marker_line: int, cycles_target: int = 0) -> None:
        self.loop_start_line = int(loop_start_line)
        self.cycle_marker_line = int(cycle_marker_line)
        self.cycles_target = int(cycles_target)
        self.cycles_done = 0
        self._counted_this_cycle = False
        self._max_line = 0
        self._last_edge_seq: int | None = None

    def observe(self, line: Any, edge_seq: int | None = None) -> bool:
        """Feed one snapshot. Returns True when a cycle was just banked."""
        if not isinstance(line, int) or isinstance(line, bool):
            return False
        if edge_seq is not None:
            if edge_seq == self._last_edge_seq:
                return False
            self._last_edge_seq = edge_seq

        wrapped = line <= self.loop_start_line and self._max_line > self.loop_start_line

        if line >= self.cycle_marker_line:
            self._max_line = max(self._max_line, line)
            if self._counted_this_cycle:
                return False
            return self._bank()

        if wrapped:
            banked = False if self._counted_this_cycle else self._bank()
            self._counted_this_cycle = False   # a new cycle starts here
            self._max_line = line
            return banked

        self._max_line = max(self._max_line, line)
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
        """Dismiss a finished job so the panel returns to idle."""
        with self._lock:
            state = self._state_locked()
            if state in ACTIVE_STATES:
                raise JobError(f"Cannot clear while {state}")
            run_id = self._session.run_id if self._session else None
            self._session = None
            snap = self._snapshot_locked()
        if run_id:
            self._event(run_id, "clear", {})
        return snap

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

            built = build_weldflex_lua(studs, cycles, gate_mode)

            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, built.program_name)
            try:
                Path(tmp_path).write_text(built.text, encoding="utf-8")
                uploaded = self._robot.upload_program(tmp_path, replace=True)
            finally:
                try:
                    os.remove(tmp_path)
                    os.rmdir(tmp_dir)
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

            log.info("job running run_id=%s program=%s cycles=%d loop_start=%d marker=%d gate=%d",
                     run_id, uploaded, cycles, built.loop_start_line,
                     built.cycle_marker_line, built.gate_line)
            self._event(run_id, "running", {
                "program": uploaded,
                "loop_start_line": built.loop_start_line,
                "cycle_marker_line": built.cycle_marker_line,
                "gate_line": built.gate_line,
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
        # The link is gone. Liberty never noticed this and kept reporting "running"
        # against a dead robot.
        if getattr(snap, "state", None) == "faulted":
            return ("finish", JobState.INTERRUPTED.value, "Lost connection to the robot")

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
        """
        error = None
        try:
            self._robot.pause_program()
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            log.warning("gate pause failed run_id=%s: %s", run_id, exc)
        with self._lock:
            sess = self._session
            if sess is None or sess.run_id != run_id:
                return
            if error:
                sess.error = error
                sess.gate_pending = False
            elif sess.state == JobState.RUNNING.value:
                sess.state = JobState.GATED.value
        self._event(run_id, "gated", {"error": error})

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
