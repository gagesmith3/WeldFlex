"""The current job: what it is, how far along it is, and how it ended.

Layering: `robot_link` is *being connected*, `robot_service` is the *verb layer*,
and this is the third concern — *what job are we running*. It calls into
`robot_service` and never touches `robot_link` or the SDK directly. `app.py`
routes are thin adapters over `JobManager`.

Cycle progress is driven by a monitor thread, not by browser polling, so a job
keeps advancing with the kiosk tab closed. The thread reads the robot service's
cached, 8083-first observation snapshot — it issues no robot I/O of its own.

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
    ARM_MODES,
    GATE_MODES,
    PROGRAM_NAME,
    WELD_PATH,
    WELD_PROGRAM_NAME,
    RunMode,
    build_single_shot_lua,
    build_weldflex_lua,
    strip_lua_comments,
)
import fault_codes
from base_keepout import check_studs as check_base_keepout
from part_origin import (
    DEFAULT_CORNER,
    ZERO_REF,
    CornerRef,
    parse_corner,
    read_corner_ref,
    resolve_studs,
)

log = logging.getLogger("weldflex.job")

# "part" builds WeldFlex.lua from a recipe's stud list; "single_shot" builds
# single_shot.lua for the Admin page's one-stud tool. Everything after the build
# is shared.
JOB_KINDS = ("part", "single_shot")

MONITOR_INTERVAL_S = 0.25
# A program can still read "stopped" for a moment after ProgramRun returns, so
# "stopped" only means end-of-program once "running" has actually been observed.
# If it never is, the job failed to launch and should say so rather than hang.
STARTUP_TIMEOUT_S = 20.0
# Belt-and-braces finish once the target count is reached, in case the controller
# never reports the stopped edge. Same idea as the old Liberty fallback.
COMPLETION_FALLBACK_S = 4.0
# Slack on top of the boundary dwell before `_gate` gives up waiting for the
# program's own `Pause()` and sends a ProgramPause itself. The cycle banks at the
# *start* of the dwell and `Pause()` runs at the end of it, so the wait has to
# cover the whole dwell first — this is only the margin for the poll interval and
# the controller's own reporting lag.
GATE_PAUSE_GRACE_S = 1.5
# How long the link may read "faulted" before a run is written off as lost. The
# link drops and reconnects XML-RPC at program start and during force operations,
# and with no 8083 feed to cover the gap each of those blips reads "faulted".
LINK_LOST_GRACE_S = 10.0
# After our own Pause/Resume/Continue the cached program state keeps reporting
# the old state for a heartbeat or two (0.5 s apart with no 8083 feed). Until the
# expected state is seen, or this long has passed, a contradicting reading is
# taken as stale: it is neither the gate nor a pause/resume from elsewhere.
COMMAND_SETTLE_S = 2.0
# The job follows the controller, like the vendor web app: a pause, resume or
# stop made from the pendant or web app is adopted once the controller has
# reported it steadily for this long. One reading is not enough on the Pi —
# a stale "paused" is exactly what banked phantom cycles before.
EXTERNAL_ADOPT_S = 1.0

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

    **`program_max_line` guards against `NewDofile` line aliasing.** Per stud,
    `WeldFlex.lua`/`single_shot.lua` call `NewDofile("/fruser/weld.lua", 1, 1)`,
    and `GetCurrentLine` reports *weld.lua's own* line numbers for the whole time
    that sub-file is executing (weld.lua is ~500 lines; the caller program is
    ~100-115). Those numbers are almost always >= `cycle_marker_line`, so without
    a ceiling the tracker banks a cycle within the first poll of the very first
    weld — seconds before the real boundary — and `cycles_done` races past
    `cycles_target` before the actual dwell/gate is ever reached, silently
    skipping the pause. `program_max_line` (the caller program's own line count)
    filters out any sample beyond it, since only a `NewDofile`'d sub-file can
    report a line number past the end of the caller's own text. This was a
    documented-but-unfixed blocker (see `.claude/skills/weldflex-app/references/
    state-and-session.md`) that shipped anyway with the weld.lua hookup
    (2026-08-03) and surfaced live on `weld_faceplate.lua` (2026-08-06).
    """

    def __init__(
        self,
        loop_start_line: int,
        cycle_marker_line: int,
        cycles_target: int = 0,
        program_max_line: int | None = None,
    ) -> None:
        self.loop_start_line = int(loop_start_line)
        self.cycle_marker_line = int(cycle_marker_line)
        self.cycles_target = int(cycles_target)
        self.program_max_line = int(program_max_line) if program_max_line is not None else None
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

        if self.program_max_line is not None and line > self.program_max_line:
            # Can only be an aliased sample from inside a NewDofile'd sub-file
            # (weld.lua) — the caller program has no line past its own length.
            return False

        if line >= self.cycle_marker_line:
            if self._counted_this_cycle:
                return False
            return self._bank()

        # Below the boundary dwell but inside the loop.
        if self._counted_this_cycle and line >= self.loop_start_line:
            self._counted_this_cycle = False
        return False

    def bank_if_uncounted(self) -> bool:
        """Bank the cycle the program just paused at the gate of, unless the
        boundary dwell already banked it. The dwell is sampled first on any
        healthy run, so an unconditional bank here counted every gated cycle
        twice."""
        if self._counted_this_cycle:
            return False
        return self._bank()

    def at_or_past_marker(self, line: Any) -> bool:
        """Whether `line` is the boundary dwell or the gate after it, in the
        caller program rather than an aliased `NewDofile` sub-file line."""
        if not isinstance(line, int) or isinstance(line, bool):
            return False
        if self.program_max_line is not None and line > self.program_max_line:
            return False
        return line >= self.cycle_marker_line

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
    arm_mode: str = "dry"
    di_check: bool = True
    stud_count: int = 0
    cycles_target: int = 0
    cycles_done: int = 0
    pressure_setting: str | None = None
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
    kind: str = "part"  # one of JOB_KINDS
    part_id: str | None = None
    part_name: str | None = None
    studs: list = field(default_factory=list)
    program: str = PROGRAM_NAME
    gate_mode: str = "pause"
    arm_mode: str = "dry"
    di_check: bool = True
    cycles_target: int = 0
    safe_z: float = 60.0
    retract_z: float = 10.0
    part_z: float = 0.0
    pressure_setting: str = "high"
    stud_type: str = "M4"
    substrate: str = "Mild Steel"
    speed: float | int | None = None
    dsc_enabled: bool = False
    stud_reload_ms: int | None = None
    origin_corner: str = DEFAULT_CORNER
    # The corner's taught point relative to zerozero, read once at load so the
    # build uses the same numbers the load checked.
    corner_ref: CornerRef = ZERO_REF
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
    boundary_ms: int = 0
    seen_running: bool = False
    launched_ts: float | None = None
    completed_since: float | None = None
    gate_pending: bool = False
    gate_since: float | None = None
    link_lost_since: float | None = None
    # A "paused" reading is only the cycle gate when nobody else explains it: not
    # while the operator's ProgramPause is in flight, and not while the cached
    # program state still shows the pause that Resume/Continue just released.
    pause_in_flight: bool = False
    expect_program: str | None = None
    expect_until: float | None = None
    # How long the controller has reported its current program state.
    observed_program: str | None = None
    observed_since: float | None = None

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
        *,
        arm_mode: str,
        di_check: bool = True,
        kind: str = "part",
        gate_mode: str = "pause",
        safe_z: float = 60.0,
        retract_z: float = 10.0,
        part_z: float = 0.0,
        pressure_setting: str = "high",
        stud_type: str = "M4",
        substrate: str = "Mild Steel",
        speed: float | int | None = None,
        dsc_enabled: bool = False,
        stud_reload_ms: int | None = None,
        origin_corner: str = DEFAULT_CORNER,
    ) -> JobSnapshot:
        """Queue a part (or a single shot) for running.

        What the caller passes, grouped by where it comes from:

        * **This run** — `cycles` and `arm_mode` ("live" or "dry"). `arm_mode`
          has no default: the operator picks it every run, and a caller that
          forgets gets a TypeError rather than a guess.
        * **The recipe** — `di_check` (False skips the DI0/DI1 checks, live
          runs included), plus the geometry and press settings from `safe_z`
          on down. `origin_corner` is the bed corner the studs are measured
          from; a part's studs are resolved against it here as well as at
          build time, so a stud that would flip across the bed is refused at
          load rather than when Run is pressed. Single shots ignore it.
        * **The entry point** — `kind` picks the builder (`JOB_KINDS`), and
          `gate_mode` what happens between cycles. "single_shot" jobs pass a
          one-point `studs` list (`[{"x":..., "y":...}]`).

        Rejected while a job is still active. A terminal job is replaced rather
        than blocking the load — `clear()` is for dismissing the result
        deliberately, not a precondition for the next job.
        """
        if kind not in JOB_KINDS:
            raise JobError(f"Unknown job kind {kind!r}")
        if gate_mode not in GATE_MODES:
            raise JobError(f"Unknown gate mode {gate_mode!r}")
        if arm_mode not in ARM_MODES:
            raise JobError(f"Unknown arm mode {arm_mode!r}; every run must be live or dry")
        if not isinstance(di_check, bool):
            raise JobError(f"DI check must be true or false, got {di_check!r}")
        try:
            origin_corner = parse_corner(origin_corner, strict=True)
            corner_ref = ZERO_REF
            if kind == "part":
                # Front-left reads nothing; any other corner reads its taught point.
                corner_ref = read_corner_ref(origin_corner,
                                             lambda name: self._robot.teach_point_pose(name))
                check_base_keepout(resolve_studs(studs, origin_corner, corner_ref))
        except ValueError as exc:
            raise JobError(str(exc)) from None
        cycles = max(1, int(cycles))
        with self._lock:
            state = self._state_locked()
            if state in ACTIVE_STATES:
                raise JobError(f"A job is already {state} — stop it before loading another")
            run_id = str(uuid.uuid4())
            self._session = _Session(
                run_id=run_id,
                state=JobState.QUEUED.value,
                kind=kind,
                part_id=part_id,
                part_name=part_name,
                studs=list(studs),
                gate_mode=gate_mode,
                arm_mode=arm_mode,
                di_check=di_check,
                cycles_target=cycles,
                safe_z=float(safe_z),
                retract_z=float(retract_z),
                part_z=float(part_z),
                pressure_setting=str(pressure_setting),
                stud_type=str(stud_type),
                substrate=str(substrate),
                speed=speed,
                dsc_enabled=bool(dsc_enabled),
                stud_reload_ms=stud_reload_ms,
                origin_corner=origin_corner,
                corner_ref=corner_ref,
            )
            snap = self._snapshot_locked()
        log.info("job loaded run_id=%s kind=%s part=%r cycles=%d gate=%s arm=%s di_check=%s "
                 "origin=%s corner_ref=(%.1f, %.1f) studs=%d",
                 run_id, kind, part_name, cycles, gate_mode, arm_mode, di_check,
                 origin_corner, corner_ref.x_mm, corner_ref.y_mm, len(studs))
        self._event(run_id, "load", {"part_id": part_id, "part_name": part_name,
                                     "kind": kind, "cycles": cycles, "gate_mode": gate_mode,
                                     "arm_mode": arm_mode, "di_check": di_check,
                                     "origin_corner": origin_corner,
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
        if not self._has_active_job():
            return self._program_command("pause", "running", self._robot.pause_program)
        return self._command(
            "pause", (JobState.RUNNING.value,), JobState.PAUSED.value, self._robot.pause_program
        )

    def resume(self) -> JobSnapshot:
        if not self._has_active_job():
            return self._program_command("resume", "paused", self._robot.resume_program)
        return self._command(
            "resume", (JobState.PAUSED.value,), JobState.RUNNING.value, self._robot.resume_program
        )

    def continue_(self) -> JobSnapshot:
        """Release the inter-cycle gate once the operator has swapped the part."""
        return self._command(
            "continue", (JobState.GATED.value,), JobState.RUNNING.value, self._robot.resume_program
        )

    def stop(self) -> JobSnapshot:
        if not self._has_active_job():
            return self._program_command(
                "stop", ("running", "paused"), self._robot.stop_program
            )
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

    def _has_active_job(self) -> bool:
        with self._lock:
            return self._session is not None and self._state_locked() in ACTIVE_STATES

    def _program_command(
        self, name: str, from_program: str | tuple[str, ...], call: Callable[[], None]
    ) -> JobSnapshot:
        """Pause/resume/stop whatever program the controller is running, with no
        WeldFlex job behind it — one started from the pendant or web app, or one
        a finished job left running (a run written off as "Lost connection" does
        not stop the robot). Checked against the controller's own program state,
        so the button cannot fire at a program that is not there.
        """
        allowed = (from_program,) if isinstance(from_program, str) else from_program
        program_state = self._program_state(self._robot.get_universal_state())
        if program_state not in allowed:
            raise JobError(f"Cannot {name}: the controller program is {program_state}")
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - shown to the operator as the panel note
            log.warning("controller %s failed: %s", name, exc)
            raise JobError(f"{name.capitalize()} failed: {exc}") from exc
        log.info("controller program %s (no active job)", name)
        return self.snapshot()

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
            # The session is still RUNNING while ProgramPause is on the wire, and
            # the monitor would otherwise read the pause landing as the gate.
            if to_state == JobState.PAUSED.value:
                sess.pause_in_flight = True
        try:
            call()
            error = None
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            log.warning("%s failed run_id=%s: %s", name, run_id, exc)
        with self._lock:
            sess = self._session
            if sess is not None and sess.run_id == run_id:
                sess.pause_in_flight = False
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
                    sess.expect_program = "paused" if to_state == JobState.PAUSED.value else "running"
                    sess.expect_until = time.time() + COMMAND_SETTLE_S
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
            arm_mode=sess.arm_mode,
            di_check=sess.di_check,
            stud_count=len(sess.studs),
            cycles_target=sess.cycles_target,
            cycles_done=sess.cycles_done,
            pressure_setting=sess.pressure_setting,
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
                kind = sess.kind
                studs = list(sess.studs)
                cycles = sess.cycles_target
                gate_mode = sess.gate_mode
                run_mode = RunMode(sess.arm_mode, di_check=sess.di_check)
                safe_z = sess.safe_z
                retract_z = sess.retract_z
                part_z = sess.part_z
                pressure_setting = sess.pressure_setting
                stud_type = sess.stud_type
                substrate = sess.substrate
                speed = sess.speed
                dsc_enabled = sess.dsc_enabled
                stud_reload_ms = sess.stud_reload_ms
                origin_corner = sess.origin_corner
                corner_ref = sess.corner_ref

            ft_config = self._robot.ft_config()
            if ft_config.get("company") != 24 or ft_config.get("device") != 0:
                raise JobError(
                    "Force sensor configuration is not XJC device 24/0; initialize the force sensor before running"
                )
            ft_sensor_num = ft_config.get("number")
            if not isinstance(ft_sensor_num, int) or not 1 <= ft_sensor_num <= 255:
                raise JobError(
                    f"Force sensor reported an invalid controller number: {ft_sensor_num!r}"
                )

            if kind == "single_shot":
                if not studs:
                    raise JobError("Single shot has no target point set")
                built = build_single_shot_lua(
                    studs[0]["x"],
                    studs[0]["y"],
                    cycles,
                    run_mode=run_mode,
                    gate_mode=gate_mode,
                    safe_z=safe_z,
                    part_z=part_z,
                    pressure_setting=pressure_setting,
                    ft_sensor_num=ft_sensor_num,
                    stud_type=stud_type,
                    substrate=substrate,
                    speed=speed,
                )
            else:
                built = build_weldflex_lua(
                    studs,
                    cycles,
                    run_mode=run_mode,
                    gate_mode=gate_mode,
                    safe_z=safe_z,
                    retract_z=retract_z,
                    part_z=part_z,
                    pressure_setting=pressure_setting,
                    ft_sensor_num=ft_sensor_num,
                    stud_type=stud_type,
                    substrate=substrate,
                    speed=speed,
                    dsc_enabled=dsc_enabled,
                    stud_reload_ms=stud_reload_ms,
                    origin_corner=origin_corner,
                    corner_ref=corner_ref,
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

            self._robot.start_job_telemetry()
            self._robot.run_program(uploaded)

            with self._lock:
                sess = self._session
                if sess is None or sess.run_id != run_id:
                    return
                sess.program = uploaded
                sess.loop_start_line = built.loop_start_line
                sess.cycle_marker_line = built.cycle_marker_line
                sess.gate_line = built.gate_line
                sess.boundary_ms = built.boundary_ms
                sess.tracker = CycleTracker(
                    built.loop_start_line, built.cycle_marker_line, cycles,
                    program_max_line=built.program_line_count,
                )
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
            # Force operations make the controller temporarily stop answering
            # XML-RPC, but the 8083 status feed continues reporting program
            # state, current line, and faults. Commands still use XML-RPC; this
            # monitor only observes through the service's consolidated cache.
            snap = self._robot.get_universal_state()
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
        # The session times the outage itself: UniversalRobotState carries no
        # `since_ts`, and reading one off it made every single faulted tick an
        # instant interrupt (live on the Pi, 2026-09-23, 1.8 s into a run).
        if getattr(snap, "state", None) == "faulted":
            now = time.time()
            if sess.link_lost_since is None:
                sess.link_lost_since = now
            if now - sess.link_lost_since >= LINK_LOST_GRACE_S:
                return ("finish", JobState.INTERRUPTED.value, "Lost connection to the robot")
            return None
        sess.link_lost_since = None

        if program_state == "running":
            sess.seen_running = True

        now = time.time()
        if program_state != sess.observed_program:
            sess.observed_program = program_state
            sess.observed_since = now
        steady = now - (sess.observed_since or now) >= EXTERNAL_ADOPT_S
        if sess.expect_program is not None and (
            program_state == sess.expect_program or now >= (sess.expect_until or 0.0)
        ):
            sess.expect_program = None
        settling = sess.expect_program is not None

        if sess.state == JobState.RUNNING.value and sess.tracker is not None:
            # Direct controller state gating: when the controller hits Pause(0) at the
            # cycle gate, program_state becomes "paused". Transition to GATED directly.
            if self._paused_at_gate_locked(sess, snap, program_state):
                if sess.tracker.bank_if_uncounted():
                    # The dwell went unsampled, so this is the cycle's only count.
                    now = time.time()
                    if sess.cycle_start_ts:
                        sess.cycle_times.append(round(now - sess.cycle_start_ts, 2))
                    sess.cycle_start_ts = now
                    events.append(("cycle", {
                        "cycles_done": sess.cycles_done,
                        "cycles_target": sess.cycles_target,
                        "cycle_s": sess.cycle_times[-1] if sess.cycle_times else None,
                    }))
                sess.state = JobState.GATED.value
                sess.gate_pending = False
                log.info("job gated at cycle %d/%d run_id=%s",
                         sess.cycles_done, sess.cycles_target, sess.run_id)
                events.append(("gated", {"error": None, "held_by": "program"}))
            else:
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
                        sess.gate_since = time.time()

        if getattr(snap, "fault_main", None):
            text = fault_codes.describe(snap.fault_main, snap.fault_sub)
            sess.error = f"Controller fault {snap.fault_main}/{snap.fault_sub}: {text.description}"

        gate_action = self._gate_pending_locked(sess, program_state, events)
        if gate_action is not None:
            return gate_action

        if steady and not settling and not sess.pause_in_flight:
            self._adopt_external_locked(sess, program_state, now, events)

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

        # The program ended before the target count was reached. From RUNNING
        # that is immediate, as it always was; from a hold it has to be steady,
        # since nothing here stops a held program except someone else's Stop.
        if sess.seen_running and program_state == "stopped" and (
            sess.state == JobState.RUNNING.value
            or (sess.state in (JobState.PAUSED.value, JobState.GATED.value)
                and steady and not settling)
        ):
            return ("finish", JobState.STOPPED.value,
                    f"Program ended after {done} of {target} cycles")

        if (
            not sess.seen_running
            and sess.launched_ts
            and time.time() - sess.launched_ts > STARTUP_TIMEOUT_S
        ):
            return ("finish", JobState.ERROR.value, "Program never reported running")

        return None

    def _adopt_external_locked(
        self, sess: _Session, program_state: str, now: float, events: list
    ) -> None:
        """Follow a pause or resume made somewhere other than this app.

        The controller is the source of truth, as it is for the vendor web app:
        paused from the pendant reads PAUSED here, resumed from the pendant
        reads RUNNING. A pause *at the boundary* never reaches this — the gate
        check has already made it GATED.
        """
        if sess.state == JobState.RUNNING.value and program_state == "paused":
            sess.state = JobState.PAUSED.value
            sess.gate_pending = False
            log.info("job paused from the controller run_id=%s", sess.run_id)
            events.append(("pause_external", {"error": None}))
        elif (
            sess.state in (JobState.PAUSED.value, JobState.GATED.value)
            and program_state == "running"
        ):
            was = sess.state
            sess.state = JobState.RUNNING.value
            sess.gate_pending = False
            sess.cycle_start_ts = now
            log.info("job resumed from the controller run_id=%s (was %s)", sess.run_id, was)
            events.append(("resume_external", {"error": None, "from": was}))

    def _paused_at_gate_locked(self, sess: _Session, snap: Any, program_state: str) -> bool:
        """Whether a "paused" reading is the program holding at its own gate.

        It used to be any "paused" while RUNNING, which also matched the
        operator's own Pause landing before `_command` could record it, and the
        stale "paused" the cache still holds for a heartbeat after Resume — each
        of which banked a phantom cycle and put up the swap-the-part prompt.
        """
        if program_state != "paused" or sess.gate_mode != "pause" or sess.pause_in_flight:
            return False
        if sess.expect_program == "running":
            return False  # the cache still shows the pause Resume/Continue released
        # Mid-body is never the gate; that is a pause from somewhere else.
        return sess.gate_pending or sess.tracker.at_or_past_marker(
            getattr(snap, "current_line", None)
        )

    def _gate_pending_locked(
        self, sess: _Session, program_state: str, events: list
    ) -> tuple | None:
        """Resolve an armed-but-not-yet-held cycle gate. `pause` mode only.

        The hold is **the program's own**: `lua_builder` emits the controller's
        `Pause()` instruction at the gate line, so the job is gated the moment
        the controller reports `paused`. Nothing is sent to the robot for that.

        The cycle banks at the marker — the *start* of the boundary dwell — and
        `Pause()` is the line after it, so the whole dwell has to elapse before
        the program can possibly report paused. Only once that window plus a
        margin is gone is the program treated as not having held, and `_gate`
        sends a ProgramPause as a backstop.

        That backstop used to be the entire gate, and it was not good enough: it
        could only be sent *after* the marker was seen and had to make the round
        trip before the dwell ran out. On 2026-08-06 it failed on hardware
        exactly the way that design invites — the faceplate run welded,
        retracted, opened DO1 and drove straight back down into the next cycle
        without ever holding. A gate that depends on host timing to stop a
        moving welder is not a gate.
        """
        if not sess.gate_pending or sess.state != JobState.RUNNING.value:
            return None

        if program_state == "paused" and not sess.pause_in_flight:
            sess.state = JobState.GATED.value
            events.append(("gated", {"error": None, "held_by": "program"}))
            return None

        window = (max(0, sess.boundary_ms) / 1000.0) + GATE_PAUSE_GRACE_S
        if sess.gate_since is not None and time.time() - sess.gate_since >= window:
            return _GATE
        return None

    def _gate(self, run_id: str) -> None:
        """Backstop for a controller whose in-program `Pause()` did not hold.

        Fails closed. A gate that cannot hold is worse than no gate at all: the
        program would run its remaining cycles with nobody swapping parts. One
        retry covers a transient RPC hiccup; past that the run ends.
        """
        log.warning("the program's own Pause() never took run_id=%s — sending ProgramPause",
                    run_id)
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

        self._enter_gated(run_id, held_by="host")

    def _enter_gated(self, run_id: str, held_by: str) -> None:
        """Move a still-running session to GATED. `held_by` records which
        mechanism actually stopped the robot, so the audit trail says whether the
        in-program `Pause()` worked or the ProgramPause backstop covered for it."""
        with self._lock:
            sess = self._session
            if sess is None or sess.run_id != run_id:
                return
            if sess.state == JobState.RUNNING.value:
                sess.state = JobState.GATED.value
                # Our own ProgramPause: the cache's "running" is stale, not a
                # resume from the pendant.
                sess.expect_program = "paused"
                sess.expect_until = time.time() + COMMAND_SETTLE_S
        self._event(run_id, "gated", {"error": None, "held_by": held_by})

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
                "kind": sess.kind,
                "gate_mode": sess.gate_mode,
                "arm_mode": sess.arm_mode,
                "di_check": sess.di_check,
                "stud_count": len(sess.studs),
                "cycles_target": sess.cycles_target,
                "cycles_done": sess.cycles_done,
                "started_at": sess.started_at,
                "ended_at": sess.ended_at,
                "status": status,
                "error": sess.error,
                "cycle_times": list(sess.cycle_times),
                "safe_z": sess.safe_z,
                "retract_z": sess.retract_z,
                "part_z": sess.part_z,
                "pressure_setting": sess.pressure_setting,
                "stud_type": sess.stud_type,
                "substrate": sess.substrate,
            }
            snap = self._snapshot_locked()

        self._robot.stop_job_telemetry()
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
        """Every logged event for run_id, oldest first.

        Reads the rotated `.1` file before the live one: `_append_line` rotates at
        EVENTS_MAX_BYTES, so an older run's trail — or the start of a run that
        straddled the rotation — lives only in `.1`.
        """
        if not run_id:
            return []
        lines: list[str] = []
        with self._file_lock:
            for path in (Path(str(self._events_path) + ".1"), self._events_path):
                try:
                    with open(path, encoding="utf-8") as f:
                        lines.extend(f.readlines())
                except OSError:
                    continue
        out = []
        for line in lines:
            line = line.strip()
            if not line or run_id not in line:
                continue
            try:
                item = json.loads(line)
                if item.get("run_id") == run_id:
                    out.append(item)
            except json.JSONDecodeError:
                continue
        return out
