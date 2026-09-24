from __future__ import annotations

import hashlib
import logging
import os
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import command_port
import frame_8083 as f8
from robot_link import (
    ConnSnapshot,
    ConnState,
    ForceSnapshot,
    RobotLink,
    TELEMETRY_RPC_TIMEOUT_S,
    has_conn_error,
    is_conn_code,
)

logger = logging.getLogger(__name__)

SDK_TIMEOUT_S = 5.0

# The controller's raw file-transfer socket, used by every Lua upload. Separate
# from XML-RPC (20003), so commands can work while uploads fail.
FILE_PORT = 20010
# The SDK uses 20 s. Connect and reply each wait this long, and both have to
# fit inside the 30 s upload call timeout, or the caller gives up while the
# worker is still mid-transfer. The controller normally replies in under 1 s.
FILE_SOCKET_TIMEOUT_S = 10.0
# The controller's :20010 listener can refuse the connect that immediately
# follows an accepted FileUpload (Pi, 2026-09-23: ECONNREFUSED every time).
# Windows silently retries a refused connect for about a second; Linux fails
# on the first refusal. Retry a refusal for this long, on every platform.
FILE_CONNECT_RETRY_S = 3.0
FILE_CONNECT_RETRY_INTERVAL_S = 0.1
UPLOAD_CALL_TIMEOUT_S = 30.0
# SDK framing: "/f/b" + 10-digit total size + 32-char md5 ... file ... "/b/f".
# The size field counts the whole frame, header and trailer included.
_FILE_HEAD_LEN = 4 + 10 + 32
_FILE_TAIL = b"/b/f"

# Mode(): the controller's operating mode, not a program state. Auto is what a
# Lua program runs under; manual is the one with the green indicator, and is
# where the cell should sit whenever WeldFlex is not driving it.
MODE_AUTO = 0
MODE_MANUAL = 1

STATE_MAP = {
    -1: "offline",
    0: "stopped",
    1: "stopped",
    2: "running",
    3: "paused",
    4: "drag",  # drag-teach active; undocumented in the SDK's own docstring
}

# StartJOG ref: 0-joint, 2-base coord, 4-tool coord, 8-workpiece coord.
# StopJOG ref is always start-ref + 1 (e.g. base-jog stop is ref 3, not 2).
JOG_START_REF = {
    ("cartesian", "base"): 2,
    ("cartesian", "tool"): 4,
    ("cartesian", "workpiece"): 8,
}
JOG_AXIS_NB = {"x": 1, "y": 2, "z": 3, "rx": 4, "ry": 5, "rz": 6}
JOG_DIRECTION = {"negative": 0, "positive": 1}

JOG_MOTION_TIMEOUT_S = 5.0
JOG_MOTION_POLL_S = 0.02
JOG_MOTION_SETTLE_S = 0.05
# Ceiling on a host-driven DO pulse. The hold blocks the link's single worker
# for its whole duration, so this is what stops a bad caller from parking the
# robot's only command channel — and a wired output — indefinitely.
DO_PULSE_MAX_S = 2.0
WELD_TELEMETRY_FRESH_S = 1.0
FORCE_FRESH_S = 0.5
WELD_TELEMETRY_CALL_TIMEOUT_S = 3.0
JOB_TELEMETRY_STUD_DI = 1
JOB_TELEMETRY_READY_DI = 0
JOB_TELEMETRY_SLOTS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
JOB_TELEMETRY_INTERVAL_S = 0.25

_WELD_PHASES = {
    10: "entered",
    20: "search: approaching",
    21: "search: contact",
    30: "press: force control on",
    31: "press: driving in",
    32: "press: holding",
    33: "press: held",
    40: "weld",
    50: "retracting",
    60: "done",
}

_WELD_FAULT_BASE = 90
_WELD_FAULT_SITES = {
    1: "FT_FindSurface refused the approach",
    4: "DI1 (stud on work) not active",
    5: "FT_LinInsertion refused the press",
    9: "FT_Control unavailable or refused",
    10: "DI1 dropped before the weld pulse",
    11: "DI0 (caps at charge) never came up",
}

_WELD_GUARD_CODES = {
    0: "released",
    1: "custom thresholds",
    2: "custom + level",
    3: "level only",
    4: "not needed at this force",
    9: "not applied",
}
_WELD_GUARD_APPLIED_CODES = frozenset({1, 2, 3})


@dataclass(frozen=True)
class UniversalRobotState:
    """Single, authoritative source of truth for all robot state and telemetry across WeldFlex."""

    # Connection & Link Health
    connected: bool = False
    state: str = "disconnected"
    busy: bool = False
    busy_label: str | None = None
    mode: str = "unknown"
    program_state_raw: int | None = None
    program_state: str = "unknown"
    current_line: int | None = None

    # The two channels fail independently, so they are reported independently.
    # `connected` tracks XML-RPC, which is what carries commands; the 8083 push
    # can be alive while it is not. Anything gating a command must read
    # `commands_available`, never `feed_streaming`.
    feed_streaming: bool = False
    commands_available: bool = False
    telemetry_source: str = "rpc"

    # Safety & Diagnostics
    fault_main: int | None = None
    fault_sub: int | None = None
    fault_source: str = "none"
    has_fault: bool = False
    # False when neither the feed nor XML-RPC could answer: a blank code from a
    # dead source is "no reading", never "no fault".
    fault_known: bool = False
    # The feed's coarse class (Appendix 1, 0-12) — the only fault *description*
    # the controller publishes; main/sub have no vendor text. None off-feed.
    fault_class: int | None = None
    fault_label: str | None = None
    # 1/0 from a fresh frame, None when there is no frame to ask. ResetAllError
    # cannot clear an engaged E-stop, so reset is refused while this is True.
    emergency_stop: bool | None = None
    probe_error: str | None = None
    run_error: str | None = None

    # Mechanical & Sensor State
    tcp_pose: tuple[float, float, float, float, float, float] | None = None
    tcp_z: float | None = None
    fz_lbf: float | None = None
    force_fresh: bool = False

    # Hardware IO Interlocks
    stud_on_work: int | None = None  # 1 = SEATED, 0 = OPEN, None = unknown
    weld_ready: int | None = None    # 1 = READY, 0 = CHARGING, None = unknown
    di_live: bool = False

    # Controller System Variables (s_var_1 .. s_var_10)
    weld_phase_code: int | None = None
    weld_phase_label: str | None = None
    last_ft_return: int | None = None
    contact_z: float | None = None
    press_travel_mm: float | None = None
    press_hold_travel_mm: float | None = None
    weld_jolt_travel_mm: float | None = None
    collision_guard_code: int | None = None
    collision_guard_label: str | None = None
    collision_guard_applied: bool = False
    target_press_lbf: float | None = None

    sampled_ts: float = 0.0
    generation: int | None = None

    def age_s(self) -> float | None:
        if not self.sampled_ts:
            return None
        return max(0.0, time.time() - self.sampled_ts)

    def is_fresh(self, max_age_s: float = WELD_TELEMETRY_FRESH_S) -> bool:
        age = self.age_s()
        return self.connected and age is not None and age <= max_age_s


@dataclass(frozen=True)
class WeldTelemetrySnapshot:
    """Latest detailed weld-test sample, owned by the service rather than a browser poll."""

    active: bool = False
    sampled_ts: float | None = None
    generation: int | None = None
    error: str | None = None
    ft_err: int | None = None
    fz: float | None = None
    stud_di: int | None = None
    stud_on_work: int | None = None
    ready_di: int | None = None
    weld_ready: int | None = None
    sysvars: tuple[tuple[int, float | None], ...] = ()
    tcp_z: float | None = None
    program_state_raw: int | None = None
    line: int | None = None
    fault_main: int | None = None
    fault_sub: int | None = None

    def age_s(self) -> float | None:
        if self.sampled_ts is None:
            return None
        return max(0.0, time.time() - self.sampled_ts)

    def is_fresh(self, max_age_s: float = WELD_TELEMETRY_FRESH_S) -> bool:
        age = self.age_s()
        # Keep the last complete sample visible through one failed attempt. A
        # transient timeout must not erase useful live diagnostics before the
        # cache has actually gone stale.
        return age is not None and age <= max_age_s

    def sysvar(self, slot: int) -> float | None:
        if not self.is_fresh():
            return None
        for saved_slot, value in self.sysvars:
            if saved_slot == slot:
                return value
        return None


@dataclass(frozen=True)
class FileTransferResult:
    """Outcome of one raw file transfer, naming the step that failed.

    stage: "ok", "rpc" (FileUpload refused), "connect", "send" or "reply".
    """

    stage: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.stage == "ok"


def transfer_file(
    rpc: Any,
    host: str,
    path: Path,
    file_type: int = 0,
    port: int = FILE_PORT,
    timeout: float = FILE_SOCKET_TIMEOUT_S,
    connect_retry_s: float = FILE_CONNECT_RETRY_S,
) -> FileTransferResult:
    """Send a file to the controller the way the SDK's `__FileUpLoad` does, but
    say which step failed.

    The SDK returns the same bare -1 for a refused FileUpload RPC, no connect
    to :20010, a short send, and a reply that isn't "SUCCESS", then discards
    the reason. That left a Pi-only upload failure (2026-09-23) undiagnosable.
    The wire format here is the SDK's, byte for byte; the differences are that
    `sendall` replaces a single unchecked `send`, a refused connect is retried
    (see FILE_CONNECT_RETRY_S), the socket is always closed, and socket errors
    are returned rather than raised. That last one matters:
    `RobotLink.call` treats a raised OSError as a dead XML-RPC link and tears
    the session down, and a failure on this separate port proves nothing
    about that link.

    `rpc` is the SDK's XML-RPC proxy (`Robot.robot`). An XML-RPC failure on the
    FileUpload call itself is left to raise, since that one *is* link evidence.
    """
    data = path.read_bytes()
    total = len(data) + _FILE_HEAD_LEN + len(_FILE_TAIL)
    md5 = hashlib.md5(data).hexdigest()

    rtn = rpc.FileUpload(file_type, path.name)
    if rtn != 0:
        return FileTransferResult("rpc", f"the controller refused the FileUpload request (code {rtn})")

    sent = 0
    started = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            break
        except ConnectionRefusedError as exc:
            elapsed = time.monotonic() - started
            if elapsed >= connect_retry_s:
                return FileTransferResult(
                    "connect",
                    f"{host}:{port} refused all {attempts} connects over {elapsed:.1f}s: {exc!r}",
                )
            time.sleep(FILE_CONNECT_RETRY_INTERVAL_S)
        except OSError as exc:
            return FileTransferResult("connect", f"could not connect to {host}:{port}: {exc!r}")
    if attempts > 1:
        logger.info(
            "File port %s:%s accepted on attempt %d after %.2fs",
            host, port, attempts, time.monotonic() - started,
        )
    try:
        try:
            for chunk in (f"/f/b{total:10d}{md5}".encode("utf-8"), data, _FILE_TAIL):
                sock.sendall(chunk)
                sent += len(chunk)
        except OSError as exc:
            return FileTransferResult(
                "send", f"the transfer to {host}:{port} broke after {sent} of {total} bytes: {exc!r}"
            )
        # The SDK waits 0.5 s before its single recv; keep that so a reply the
        # controller sends in pieces arrives whole.
        time.sleep(0.5)
        try:
            reply = sock.recv(1024)
        except socket.timeout:
            return FileTransferResult("reply", f"sent all {total} bytes; no reply within {timeout:.0f}s")
        except OSError as exc:
            return FileTransferResult("reply", f"sent all {total} bytes; reading the reply failed: {exc!r}")
    finally:
        sock.close()

    if reply[:7] == b"SUCCESS":
        return FileTransferResult("ok")
    if not reply:
        return FileTransferResult("reply", f"sent all {total} bytes; the controller closed the socket without replying")
    return FileTransferResult("reply", f"sent all {total} bytes; the controller replied {reply[:200]!r} instead of SUCCESS")


class WeldFlexRobotService:
    """Robot operations. Connection concerns live in RobotLink; this is the verb layer."""

    def __init__(self, robot_ip: str) -> None:
        self._link = RobotLink(robot_ip)
        self._start_time = time.time()
        self._weld_telemetry_lock = threading.Lock()
        self._weld_telemetry = WeldTelemetrySnapshot()
        self._weld_telemetry_epoch = 0
        self._weld_telemetry_stop: threading.Event | None = None
        self._weld_telemetry_thread: threading.Thread | None = None
        self._weld_telemetry_config: tuple[int, int, tuple[int, ...], float] | None = None
        self._last_reset_ts: float | None = None

    # --- connection surface (delegated to the link) ---

    @property
    def robot_ip(self) -> str:
        return self._link.robot_ip

    def start(self) -> None:
        """Begin maintaining the connection. Non-blocking; call once at app startup."""
        self._link.start(connect=True)

    def shutdown(self, timeout: float = 5.0) -> None:
        self.stop_weld_telemetry()
        self._link.shutdown(timeout=timeout)

    def connect(self) -> None:
        self._link.connect()

    def disconnect(self) -> None:
        self._link.disconnect()

    def reconnect(self) -> None:
        self._link.request_reconnect()

    def set_robot_ip(self, ip: str) -> bool:
        """Retarget the live connection. Returns True if the address changed."""
        return self._link.set_ip(ip)

    def snapshot(self) -> ConnSnapshot:
        return self._link.snapshot()

    def force_snapshot(self) -> ForceSnapshot:
        """Latest CNDE force data for any page, without controller I/O."""
        return self._link.force_snapshot()

    def feed_snapshot(self):
        """Latest port-8083 status frame, without controller I/O.

        Observation only, but no longer unused: `get_universal_state()` prefers
        this over the XML-RPC cache for program state, line and fault codes,
        because the controller stops answering XML-RPC for the duration of a
        force operation while these frames keep arriving. `ft_read()` also uses
        its F/T fields as the primary force source; DI remains observation-only
        and stays on the controller-Lua relay for interlocks.
        """
        return self._link.feed_snapshot()

    def feed_stats(self) -> dict[str, Any]:
        """Connection and frame-integrity counters for the diagnostics page."""
        return self._link.feed_stats()

    def get_universal_state(self) -> UniversalRobotState:
        """Consolidated, authoritative single source of truth for all robot state and telemetry."""
        snap = self.snapshot()
        feed = self.feed_snapshot()
        with self._weld_telemetry_lock:
            telemetry = self._weld_telemetry

        feed_streaming = feed.is_fresh()
        connected = snap.connected
        state = snap.state
        busy = snap.busy
        busy_label = snap.busy_label

        # Observation prefers the pushed frame. The XML-RPC poll cannot get an
        # answer while the controller is busy with a force operation — the
        # program keeps running, the poll just stops being able to ask — so the
        # frame is both fresher and available exactly when the poll is not.
        # `PROGRAM_STATES` and `STATE_MAP` agree, so this changes source, not meaning.
        if feed_streaming and feed.program_state is not None:
            prog_raw = feed.program_state
            line = feed.current_line
            fault_main = feed.fault_main or None  # the frame reports 0 for "none"
            fault_sub = feed.fault_sub or None
            fault_source = "8083"
            telemetry_source = "8083"
            fault_class = feed.get("error_code")
            estop = feed.emergency_stop
            emergency_stop = None if estop is None else bool(estop)
        else:
            prog_raw = snap.program_state_raw
            line = snap.current_line
            fault_main = snap.fault_main
            fault_sub = snap.fault_sub
            fault_source = snap.fault_source
            telemetry_source = "rpc"
            fault_class = None
            emergency_stop = None

        program_state = STATE_MAP.get(prog_raw, "unknown") if prog_raw is not None else "unknown"
        has_fault = bool(fault_main) or bool(fault_class)
        # "cache" is the SDK's dead local read, which reports 0 whatever the
        # controller is doing (see fairino-sdk error-handling reference).
        fault_known = fault_source == "8083" or (fault_source == "rpc" and connected)
        fault_label = f8.ERROR_CODES.get(fault_class) if fault_class else None

        # A live feed proves the controller is reachable even when XML-RPC is
        # not. Say so rather than showing "offline" over a robot that is plainly
        # running — but do not say "online" either, because commands still ride
        # XML-RPC and would silently fail.
        if feed_streaming and not connected and state != "disconnected":
            state = "telemetry"

        try:
            ft = self.ft_read()
            fz_lbf = ft["fz"] * -1.0 * 0.2248089431 if ft.get("fz") is not None else None
            force_fresh = bool(ft.get("active"))
        except Exception:
            fz_lbf = None
            force_fresh = False

        telemetry_current = telemetry.is_fresh() and telemetry.generation == snap.generation
        sv_phase = telemetry.sysvar(1) if telemetry_current else None
        sv_ret = telemetry.sysvar(2) if telemetry_current else None
        sv_z0 = telemetry.sysvar(3) if telemetry_current else None
        sv_travel = telemetry.sysvar(4) if telemetry_current else None
        sv_guard = telemetry.sysvar(5) if telemetry_current else None
        sv_stud = telemetry.sysvar(6) if telemetry_current else None
        sv_ready = telemetry.sysvar(7) if telemetry_current else None
        sv_press_lbf = telemetry.sysvar(8) if telemetry_current else None
        sv_hold_travel = telemetry.sysvar(9) if telemetry_current else None
        sv_jolt_travel = telemetry.sysvar(10) if telemetry_current else None

        phase_code = None
        packed_di1 = None
        packed_di0 = None
        if sv_phase is not None:
            raw_val = int(sv_phase)
            phase_code = raw_val % 100
            d1 = (raw_val // 100) % 10
            d0 = (raw_val // 1000) % 10
            packed_di1 = None if d1 == 9 else d1
            packed_di0 = None if d0 == 9 else d0

        phase_label = None
        if phase_code is not None:
            if phase_code >= _WELD_FAULT_BASE:
                site = phase_code - _WELD_FAULT_BASE
                phase_label = f"FAULT: {_WELD_FAULT_SITES.get(site, f'site {site}')}"
            else:
                phase_label = _WELD_PHASES.get(phase_code, f"unknown ({phase_code})")

        guard_code = int(sv_guard) if sv_guard is not None else None
        guard_label = _WELD_GUARD_CODES.get(guard_code, f"unknown ({guard_code})") if guard_code is not None else None
        guard_applied = guard_code in _WELD_GUARD_APPLIED_CODES if guard_code is not None else False

        def _di_level(val: float | None) -> int | None:
            if val is None or val < 0:
                return None
            return 1 if int(val) == 1 else 0

        stud_on_work = _di_level(sv_stud) if sv_stud is not None else packed_di1
        weld_ready = _di_level(sv_ready) if sv_ready is not None else packed_di0
        di_live = program_state == "running"
        fast_feed_fresh = feed.is_fresh(FORCE_FRESH_S)
        if fast_feed_fresh:
            if feed.di(JOB_TELEMETRY_STUD_DI) is not None:
                stud_on_work = feed.di(JOB_TELEMETRY_STUD_DI)
            if feed.di(JOB_TELEMETRY_READY_DI) is not None:
                weld_ready = feed.di(JOB_TELEMETRY_READY_DI)
            di_live = di_live or stud_on_work is not None or weld_ready is not None

        probe_err = None
        if not connected and not feed_streaming:
            probe_err = "Robot offline"
        elif not connected:
            probe_err = "Telemetry only — the robot is reachable but commands cannot be delivered"
        elif not force_fresh and di_live:
            probe_err = "No fresh force data from the status feed or CNDE"

        sampled_ts = telemetry.sampled_ts or snap.last_success_ts or time.time()

        return UniversalRobotState(
            connected=connected,
            state=state,
            busy=busy,
            busy_label=busy_label,
            feed_streaming=feed_streaming,
            commands_available=connected,
            telemetry_source=telemetry_source,
            program_state_raw=prog_raw,
            program_state=program_state,
            current_line=line,
            fault_main=fault_main,
            fault_sub=fault_sub,
            fault_source=fault_source,
            has_fault=has_fault,
            fault_known=fault_known,
            fault_class=fault_class,
            fault_label=fault_label,
            emergency_stop=emergency_stop,
            probe_error=probe_err,
                 tcp_z=(feed.tcp_z if fast_feed_fresh and feed.tcp_z is not None
                         else telemetry.tcp_z if telemetry_current else None),
            fz_lbf=fz_lbf,
            force_fresh=force_fresh,
            stud_on_work=stud_on_work,
            weld_ready=weld_ready,
            di_live=di_live,
            weld_phase_code=phase_code,
            weld_phase_label=phase_label,
            last_ft_return=int(sv_ret) if sv_ret is not None else None,
            contact_z=sv_z0 if (sv_z0 is not None and sv_z0 != 0) else None,
            press_travel_mm=sv_travel if (sv_travel is not None and sv_travel != 0) else None,
            press_hold_travel_mm=sv_hold_travel if (sv_hold_travel is not None and sv_hold_travel != 0) else None,
            weld_jolt_travel_mm=sv_jolt_travel if (sv_jolt_travel is not None and sv_jolt_travel != 0) else None,
            collision_guard_code=guard_code,
            collision_guard_label=guard_label,
            collision_guard_applied=guard_applied,
            target_press_lbf=sv_press_lbf if (sv_press_lbf is not None and sv_press_lbf > 0) else None,
            sampled_ts=sampled_ts,
            generation=snap.generation,
        )

    def set_running_hint(self, running: bool) -> None:
        """Tell the link to probe faster while a program runs, for live line tracking."""
        self._link.set_heartbeat_hint(running)

    def thread_report(self) -> dict[str, Any]:
        return self._link.thread_report()

    # --- detailed weld telemetry ---

    def start_job_telemetry(self) -> None:
        """Start the read-only sampler required to observe a JobManager weld run."""
        self.start_weld_telemetry(
            JOB_TELEMETRY_STUD_DI,
            JOB_TELEMETRY_READY_DI,
            JOB_TELEMETRY_SLOTS,
            interval_s=JOB_TELEMETRY_INTERVAL_S,
        )

    def stop_job_telemetry(self) -> None:
        """Stop the JobManager telemetry sampler while retaining its last sample."""
        self.stop_weld_telemetry()

    def start_weld_telemetry(
        self,
        stud_di: int,
        ready_di: int,
        sysvar_slots: tuple[int, ...],
        interval_s: float,
    ) -> None:
        """Start one detailed sampler for an active weld program.

        Browser polls read ``weld_telemetry_snapshot`` only. The sampler is the
        single producer, and its calls still pass through RobotLink's SDK worker.
        """
        config = (
            int(stud_di),
            int(ready_di),
            tuple(int(slot) for slot in sysvar_slots),
            max(0.05, float(interval_s)),
        )
        with self._weld_telemetry_lock:
            thread = self._weld_telemetry_thread
            if thread is not None and thread.is_alive() and self._weld_telemetry_config == config:
                return

            if self._weld_telemetry_stop is not None:
                self._weld_telemetry_stop.set()
            self._weld_telemetry_epoch += 1
            epoch = self._weld_telemetry_epoch
            stop = threading.Event()
            self._weld_telemetry_stop = stop
            self._weld_telemetry_config = config
            self._weld_telemetry = WeldTelemetrySnapshot(active=True)
            thread = threading.Thread(
                target=self._weld_telemetry_loop,
                args=(epoch, stop, config),
                daemon=True,
                name="weld-telemetry",
            )
            self._weld_telemetry_thread = thread
        thread.start()

    def stop_weld_telemetry(self) -> None:
        """Stop detailed sampling and retain the last reading as visibly stale data."""
        with self._weld_telemetry_lock:
            self._weld_telemetry_epoch += 1
            if self._weld_telemetry_stop is not None:
                self._weld_telemetry_stop.set()
            self._weld_telemetry_stop = None
            self._weld_telemetry_thread = None
            self._weld_telemetry_config = None
            self._weld_telemetry = replace(self._weld_telemetry, active=False)

    def weld_telemetry_snapshot(self) -> WeldTelemetrySnapshot:
        """Return the latest detailed telemetry cache without doing robot I/O."""
        with self._weld_telemetry_lock:
            return self._weld_telemetry

    def _weld_telemetry_loop(
        self,
        epoch: int,
        stop: threading.Event,
        config: tuple[int, int, tuple[int, ...], float],
    ) -> None:
        stud_di, ready_di, sysvar_slots, interval_s = config
        while not stop.is_set():
            before = self.snapshot()
            sampled_ts = time.time()
            try:
                probe = self.weld_probe(stud_di, ready_di, sysvar_slots)
                after = self.snapshot()
                if before.generation != after.generation:
                    raise RuntimeError("Robot connection changed during weld telemetry sample")
                reading = WeldTelemetrySnapshot(
                    active=True,
                    sampled_ts=sampled_ts,
                    generation=after.generation,
                    ft_err=probe["ft_err"],
                    fz=probe["fz"],
                    stud_di=probe["stud_di"],
                    stud_on_work=probe["stud_on_work"],
                    ready_di=probe["ready_di"],
                    weld_ready=probe["weld_ready"],
                    sysvars=tuple(
                        (slot, probe["sysvars"].get(slot)) for slot in sysvar_slots
                    ),
                    tcp_z=probe["tcp_z"],
                    program_state_raw=probe["program_state_raw"],
                    line=probe["line"],
                    fault_main=probe["fault_main"],
                    fault_sub=probe["fault_sub"],
                )
            except Exception as exc:  # noqa: BLE001 - sampling failures are telemetry data
                reading = None
                error = str(exc)

            with self._weld_telemetry_lock:
                if epoch != self._weld_telemetry_epoch:
                    return
                if reading is not None:
                    self._weld_telemetry = reading
                else:
                    self._weld_telemetry = replace(
                        self._weld_telemetry, active=True, error=error
                    )
            stop.wait(interval_s)

    # --- SDK call plumbing ---

    def _call(
        self,
        fn: Callable[[Any], Any],
        timeout: float = SDK_TIMEOUT_S,
        retries: int = 3,
        priority: int = 0,
        coalesce_key: str | None = None,
        rpc_timeout: float | None = None,
    ) -> Any:
        """Run an SDK call on the link's worker thread with a hard timeout."""
        return self._link.call(
            fn,
            timeout=timeout,
            retries=retries,
            label=getattr(fn, "__name__", "call"),
            priority=priority,
            coalesce_key=coalesce_key,
            rpc_timeout=rpc_timeout,
        )

    # Kept as staticmethods for the documented wrapper pattern; the implementations
    # live in robot_link so the link's own dispatch path shares them.
    _is_conn_code = staticmethod(is_conn_code)
    _has_conn_error = staticmethod(has_conn_error)

    @staticmethod
    def _unpack(response: Any) -> tuple[int, Any]:
        """Split an SDK response into (error_code, value)."""
        if isinstance(response, (tuple, list)):
            if len(response) >= 2:
                return int(response[0]), response[1]
            if len(response) == 1:
                return int(response[0]), None
        if isinstance(response, (int, float)):
            return int(response), None
        return -1, response

    def pause_program(self) -> None:
        self._program_control("ProgramPause", lambda r: r.ProgramPause(), command_port.CMD_PAUSE)

    def resume_program(self) -> None:
        self._program_control("ProgramResume", lambda r: r.ProgramResume(), command_port.CMD_RESUME)

    def stop_program(self) -> None:
        self._program_control("ProgramStop", lambda r: r.ProgramStop(), command_port.CMD_STOP)

    def _program_control(self, label: str, fn: Callable[[Any], Any], cmd_id: int) -> None:
        """Pause/resume/stop, over XML-RPC while it answers and port 8080 when not.

        The controller stops answering XML-RPC for the whole of a force
        operation, which left these buttons dead through the search, press and
        hold (see `command_port`). XML-RPC stays first because it is the path
        with a track record; 8080 is only the way through that outage. A
        *refusal* from an answering controller is final — only a transport
        failure falls through, so a real "can't pause now" is never overridden.
        """
        rpc_error = None
        if self.snapshot().connected:
            try:
                err = self._call(fn, retries=1)
            except Exception as exc:  # noqa: BLE001 - transport failure: try 8080
                rpc_error = str(exc)
            else:
                err_code, _ = self._unpack(err)
                if err_code != 0:
                    raise RuntimeError(f"{label} failed (code {err_code})")
                return
        else:
            rpc_error = "XML-RPC not answering"

        logger.warning("%s over XML-RPC unavailable (%s) — sending on the command port",
                    label, rpc_error)
        try:
            command_port.send_command(self.robot_ip, cmd_id)
        except command_port.CommandPortError as exc:
            raise RuntimeError(f"{label} failed: {rpc_error}; command port: {exc}") from exc

    def upload_program(self, local_path: str, replace: bool = False) -> str:
        """Upload a Lua file to the robot. Returns the program name as stored on the robot."""
        path = Path(local_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Program file not found: {path}")
        if replace:
            # Ignore delete errors; file may not already exist.
            self._call(lambda r: r.LuaDelete(path.name))
        self._lua_upload(path)
        return path.name

    def _lua_upload(self, path: Path) -> None:
        """The SDK's `LuaUpload`, in two steps that each report their own failure.

        Step 1 is the raw transfer on :20010 (`transfer_file`), which fails
        before the controller parses anything. Step 2 is `LuaUpLoadUpdate`, the
        controller's post-upload check. That check EXECUTES top-level Lua (see
        the fairino-sdk skill), and its errorStr is the controller's own reason.
        """
        result = self._call(
            lambda r: transfer_file(r.robot, r.ip_address, path),
            timeout=UPLOAD_CALL_TIMEOUT_S,
        )
        if not result.ok:
            logger.warning("Lua upload of %s failed at %s: %s", path.name, result.stage, result.detail)
            raise RuntimeError(f"Lua upload of {path.name} failed at the {result.stage} step: {result.detail}")
        resp = self._call(lambda r: r.robot.LuaUpLoadUpdate(path.name), timeout=UPLOAD_CALL_TIMEOUT_S)
        err_code, detail = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(
                f"Lua upload of {path.name} failed (code {err_code}): the transfer completed; "
                f"the controller refused the file at the post-upload check: {detail}"
            )

    def upload_studs_data(self, studs: list, filename: str = "studs_data_wf.lua") -> None:
        """Generate a studs data Lua file from a list of {x, y} dicts and upload it to the robot.

        LuaUpload fails if the file already exists, so we delete first (ignoring
        errors if it wasn't there) then upload the freshly generated file.
        """
        lines = ["-- Auto-generated by WeldFlex.", f"-- {len(studs)} stud(s).", "", "studs = {"]
        for s in studs:
            lines.append(f"    {{x={s['x']}, y={s['y']}}},")
        lines.append("}")
        lua_content = "\n".join(lines) + "\n"

        # Delete the old copy — ignore errors (file may not exist yet)
        self._call(lambda r: r.LuaDelete(filename))

        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, filename)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(lua_content)
            self._lua_upload(Path(tmp_path))
        finally:
            try:
                os.remove(tmp_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def joint_overspeed_protect(self, strategy: int = 3, speed_percent: int = 50) -> None:
        """Arm the controller's joint-overspeed handling for subsequent motion.

        Raw RPC: the SDK only issues JointOverSpeedProtectStart as a private
        bracket around its own MoveL (Robot.py:3088), but the motion that needs
        it here — weld.lua's FT_FindSurface descent — executes inside a
        controller-side program, so it is armed session-wide instead. strategy:
        0 off, 1 standard, 2 error-stop on overspeed, 3 adaptive slowdown.
        speed_percent is the vendor's "allowed slow-down threshold" [0-100]
        (their default is 10); 50 gives the adaptive strategy real headroom.

        Deliberately never paired with JointOverSpeedProtectEnd: nothing
        host-side observes the program end reliably, and armed is the safer
        resting state while the FT_FindSurface axis-2 overspeed fault
        (2026-07-28) is under investigation. Whether this API governs
        program-executed motion at all on this firmware is unverified — a
        nonzero return here is how we find out it does not exist.
        """
        resp = self._call(
            lambda r: r.robot.JointOverSpeedProtectStart(int(strategy), int(speed_percent))
        )
        err_code, _ = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"JointOverSpeedProtectStart failed (code {err_code})")

    def set_manual_mode(self) -> None:
        """Hand the cell back to the operator in manual mode (the green indicator).

        `run_program` puts the controller into auto and nothing takes it out again
        when the program ends, so this is the way back.
        """
        err = self._call(lambda r: r.Mode(MODE_MANUAL))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"Mode(manual) failed (code {err_code})")

    def run_program(self, program_name: str) -> None:
        """Run a Lua program already stored on the robot under /fruser/."""
        self._call(lambda r: r.Mode(MODE_AUTO))
        time.sleep(0.5)
        load_resp = self._call(lambda r: r.ProgramLoad(f"/fruser/{program_name}"))
        load_err, _ = self._unpack(load_resp)
        if load_err != 0:
            raise RuntimeError(f"ProgramLoad failed (code {load_err}): {program_name}")
        run_resp = self._call(lambda r: r.ProgramRun())
        run_err, _ = self._unpack(run_resp)
        if run_err != 0:
            raise RuntimeError(f"ProgramRun failed (code {run_err}): {program_name}")

    def upload_and_run(self, local_path: str) -> None:
        """Upload a Lua file then immediately run it."""
        program_name = self.upload_program(local_path)
        self.run_program(program_name)

    def jog_step(self, mode: str, frame: str, axis: str, direction: str, step: float, vel: float) -> None:
        """Jog one bounded step (StartJOG's max_dis) and block until the robot reports motion done."""
        ref = JOG_START_REF.get((mode, frame))
        nb = JOG_AXIS_NB.get(axis)
        dir_ = JOG_DIRECTION.get(direction)
        if ref is None or nb is None or dir_ is None:
            raise ValueError(f"Invalid jog request: mode={mode} frame={frame} axis={axis} direction={direction}")

        err = self._call(lambda r: r.StartJOG(ref, nb, dir_, float(step), float(vel)))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"StartJOG failed (code {err_code})")

        time.sleep(JOG_MOTION_SETTLE_S)
        deadline = time.time() + JOG_MOTION_TIMEOUT_S
        while time.time() < deadline:
            done_resp = self._call(lambda r: r.GetRobotMotionDone(), retries=1)
            _, done = self._unpack(done_resp)
            if done:
                return
            time.sleep(JOG_MOTION_POLL_S)

    def jog_stop(self) -> None:
        """Immediate jog stop — no ref needed, halts whatever mode is active."""
        err = self._call(lambda r: r.ImmStopJOG())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ImmStopJOG failed (code {err_code})")

    def jog_pose(self) -> list:
        """Current TCP pose [x, y, z, rx, ry, rz] for the jog position readout."""
        resp = self._call(
            lambda r: r.GetActualTCPPose(1), retries=1, priority=2, coalesce_key="jog-pose"
        )
        err_code, pose = self._unpack(resp)
        if err_code != 0 or pose is None:
            raise RuntimeError(f"GetActualTCPPose failed (code {err_code})")
        return [float(v) for v in pose]

    def tcp_enable_drag(self) -> None:
        """Enter drag teach mode so the operator can physically position the robot."""
        err = self._call(lambda r: r.DragTeachSwitch(1))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"DragTeachSwitch(1) failed (code {err_code})")

    def tcp_record_point(self, point_num: int) -> None:
        """Exit drag mode and record current pose as TCP reference point N (1-4)."""
        def _seq(r):
            r.DragTeachSwitch(0)
            return r.SetTcp4RefPoint(point_num)
        err = self._call(_seq)
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"SetTcp4RefPoint({point_num}) failed (code {err_code})")

    def tcp_compute_and_apply(self, tool_id: int = 1) -> list:
        """Compute TCP from 4 recorded points and save to tool slot via SetToolCoord."""
        resp = self._call(lambda r: r.ComputeTcp4(), timeout=10.0)
        err_code, tcp_pose = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"ComputeTcp4 failed (code {err_code})")
        apply_resp = self._call(lambda r: r.SetToolCoord(tool_id, tcp_pose, 0, 0, 0, 0))
        apply_code, _ = self._unpack(apply_resp)
        if apply_code != 0:
            raise RuntimeError(f"SetToolCoord failed (code {apply_code})")
        return list(tcp_pose)

    def set_anticollision(self, mode: int = 0, level: list | None = None, config: int = 0) -> None:
        """Set joint collision detection level on controller (mode 0, level 10 = Collision OFF)."""
        if level is None:
            level = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        err = self._call(lambda r: r.SetAnticollision(mode, level, config))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            logger.warning(f"SetAnticollision failed (code {err_code})")

    def ft_setup(self) -> None:
        """Configure and activate the F/T sensor without changing its tare or payload.
        Takes ~6 s due to required waits between commands."""
        def require_success(command: str, response: Any) -> None:
            err_code, _ = self._unpack(response)
            if err_code != 0:
                raise RuntimeError(f"{command} failed (code {err_code})")

        def _sequence(r):
            require_success("FT_SetConfig(24, 0)", r.FT_SetConfig(24, 0))
            time.sleep(1)
            # Report in the tool frame (0=tool, 1=base). Asserted rather than
            # inherited: weld contact force acts along the torch approach axis,
            # which stays on one tool-frame axis as the robot reorients.
            require_success("FT_SetRCS(0)", r.FT_SetRCS(0))
            time.sleep(1)
            require_success("FT_Activate(0)", r.FT_Activate(0))
            time.sleep(2)
            require_success("FT_Activate(1)", r.FT_Activate(1))

        self._call(_sequence, timeout=30.0)

    def ft_config(self) -> dict:
        """The controller's own force-sensor configuration, including its number.

        Read-only, idle-safe, and the answer to weld.lua's FTC_SENSOR_NUM — which
        is currently the guessed value 1. FT_Control takes a `sensor_num`, and a
        wrong one does not fail loudly: the regulator starts, regulates on
        nothing, and the insertion drives in under position control until
        something stops it. That is indistinguishable from the collision-threshold
        story the STAGE 2 investigation has been chasing.

        `FT_GetConfig` returns [number, company, device, softversion, bus]
        (Robot.py:7439-7443). `number` is the sensor number the WebApp assigned;
        `company` should read 24 (XJC) and `device` 0 on this cell, matching what
        ft_setup writes — if they do not, this is reporting a different sensor
        than the one WeldFlex configured and the number is not usable.

        Call it while nothing is running. It is a plain XML-RPC read, but the FT
        interfaces return code 14 for the whole time a controller-side force task
        owns the sensor.
        """
        resp = self._call(lambda r: r.FT_GetConfig())
        err_code, values = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"FT_GetConfig failed (code {err_code})")
        if not isinstance(values, (list, tuple)) or len(values) < 3:
            raise RuntimeError(f"FT_GetConfig returned an unexpected shape: {values!r}")
        return {
            "number": int(values[0]),
            "company": int(values[1]),
            "device": int(values[2]),
            # Documented as unused and defaulted to 0; carried through rather than
            # dropped so an unexpected value is visible instead of silently lost.
            "softversion": int(values[3]) if len(values) > 3 else None,
            "bus": int(values[4]) if len(values) > 4 else None,
        }

    def ft_compensation(self) -> dict:
        """Read the controller's configured payload below the F/T sensor."""
        def read_compensation(r):
            return r.GetForceSensorPayload(), r.GetForceSensorPayloadCog()

        payload_response, cog_response = self._call(read_compensation)
        payload_code, payload_kg = self._unpack(payload_response)
        if payload_code != 0:
            raise RuntimeError(f"GetForceSensorPayload failed (code {payload_code})")
        if not isinstance(cog_response, (list, tuple)) or len(cog_response) < 4:
            raise RuntimeError(
                f"GetForceSensorPayloadCog returned an unexpected shape: {cog_response!r}"
            )
        cog_code = int(cog_response[0])
        if cog_code != 0:
            raise RuntimeError(f"GetForceSensorPayloadCog failed (code {cog_code})")
        try:
            cog_mm = tuple(float(value) for value in cog_response[1:4])
            return {"payload_kg": float(payload_kg), "cog_mm": cog_mm}
        except (TypeError, ValueError):
            raise RuntimeError(
                f"GetForceSensorPayload returned invalid data: {payload_kg!r}, {cog_response[1:4]!r}"
            ) from None

    def ft_deactivate(self) -> None:
        err = self._call(lambda r: r.FT_Activate(0))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_Activate(0) failed (code {err_code})")

    def ft_zero(self) -> None:
        err = self._call(lambda r: r.FT_SetZero(1))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_SetZero failed (code {err_code})")

    def ft_read(self) -> dict:
        """Read fresh force from the push caches without issuing robot commands."""
        feed = self.feed_snapshot()
        feed_values = feed.ft_values if feed.is_fresh(FORCE_FRESH_S) else None
        if feed_values is not None and len(feed_values) >= 6:
            fx, fy, fz, mx, my, mz = feed_values[:6]
            return {
                "fx": float(fx), "fy": float(fy), "fz": float(fz),
                "mx": float(mx), "my": float(my), "mz": float(mz),
                "active": feed.ft_active is True,
                "source": "8083",
                "age_s": feed.age_s(),
            }

        cached = self.force_snapshot()
        if cached.is_fresh(FORCE_FRESH_S):
            fx, fy, fz, mx, my, mz = cached.values
            return {
                "fx": fx, "fy": fy, "fz": fz,
                "mx": mx, "my": my, "mz": mz,
                "active": True,
                "source": cached.source,
                "age_s": cached.age_s(),
            }

        return {
            "fx": None, "fy": None, "fz": None,
            "mx": None, "my": None, "mz": None,
            "active": False,
            "source": "none",
            "age_s": None,
        }

    def pulse_do(self, channel: int, duration_s: float) -> None:
        """Drive a control-box DO high for `duration_s`, then back low.

        Both writes and the hold happen inside one worker dispatch. The link has
        a single worker thread, so nothing can be interleaved between the two
        writes and leave the line latched high, and the `finally` drops it even
        if the hold is interrupted. `retries=1` rather than `_call`'s default 3
        because this actuates real hardware — a silent retry of a half-failed
        pulse would advance the stud feeder a second time.
        """
        ch = int(channel)
        hold = max(0.0, min(float(duration_s), DO_PULSE_MAX_S))

        def set_do_pulse(r):
            on = r.SetDO(ch, 1)
            try:
                time.sleep(hold)
            finally:
                off = r.SetDO(ch, 0)
            return on, off

        on_resp, off_resp = self._call(
            set_do_pulse, timeout=hold + SDK_TIMEOUT_S, retries=1
        )
        on_err, _ = self._unpack(on_resp)
        off_err, _ = self._unpack(off_resp)
        if on_err != 0 or off_err != 0:
            raise RuntimeError(
                f"SetDO({ch}) failed (high code {on_err}, low code {off_err})"
            )

    def weld_probe(
        self,
        stud_di: int = 1,
        ready_di: int = 0,
        sysvar_slots: tuple[int, ...] = (1, 2),
    ) -> dict:
        """Sample Lua variables with bounded, individually scheduled RPC reads.

        Force, pose and input display values come from fresh push data. A failed
        RPC aborts the sample; callers retain their previous sample until stale.
        """
        feed = self.feed_snapshot()
        feed_fresh = feed.is_fresh(FORCE_FRESH_S)
        stud_level = feed.di(stud_di) if feed_fresh else None
        ready_level = feed.di(ready_di) if feed_fresh else None
        deadline = time.monotonic() + WELD_TELEMETRY_CALL_TIMEOUT_S
        svar_resps = []
        for slot in sysvar_slots:
            level = {6: stud_level, 7: ready_level}.get(slot)
            if level is not None:
                svar_resps.append((0, level))
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Weld telemetry sample budget exhausted")
            response = self._call(
                lambda robot, slot=slot: robot.robot.GetSysVarValue(int(slot)),
                retries=1,
                timeout=min(remaining, TELEMETRY_RPC_TIMEOUT_S + 0.25),
                rpc_timeout=min(remaining, TELEMETRY_RPC_TIMEOUT_S),
                priority=2,
                coalesce_key=f"weld-sysvar:{slot}",
            )
            err_code, _ = self._unpack(response)
            if err_code != 0:
                raise RuntimeError(f"GetSysVarValue({slot}) failed (code {err_code})")
            svar_resps.append(response)
        force = self.ft_read()

        def _number(resp):
            """Value out of an (err, value) response, or None on any failure."""
            if isinstance(resp, (list, tuple)) and len(resp) >= 2 and int(resp[0]) == 0:
                try:
                    return float(resp[1])
                except (TypeError, ValueError):
                    return None
            return None

        # Keyed by slot so a caller that asks for different slots still gets a
        # dict it can index by the number weld.lua wrote to.
        sysvars = {
            int(slot): _number(resp)
            for slot, resp in zip(sysvar_slots, svar_resps)
        }

        return {
            "ft_err": 0 if force["fz"] is not None else None,
            "fz": force["fz"],
            "stud_di": int(stud_di),
            "stud_on_work": stud_level,
            "ready_di": int(ready_di),
            "weld_ready": ready_level,
            "sysvars": sysvars,
            "tcp_z": feed.tcp_z if feed_fresh else None,
            # Program state, line, and faults are the core heartbeat's job.
            # Duplicating them here made one detail read much longer without
            # improving what the page can render.
            "program_state_raw": None,
            "line": None,
            "fault_main": None,
            "fault_sub": None,
        }

    def status(self) -> dict[str, Any]:
        """Connection + program state from the link's cached snapshot. Does no robot I/O.

        Polled once a second by every open page, so it must stay free: the robot is
        contacted once per heartbeat by the supervisor regardless of how many browsers
        are watching.
        """
        snap = self._link.snapshot()
        return {
            "connected": snap.connected,
            "program_state": STATE_MAP.get(snap.program_state_raw, "unknown"),
            "program_state_raw": snap.program_state_raw,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Everything status() reports plus link internals. Also a pure cache read."""
        snap = self._link.snapshot()
        live = snap.state in (ConnState.CONNECTED.value, ConnState.DEGRADED.value)
        err = 0 if live else -1

        return {
            "connected": snap.connected,
            "program_state": STATE_MAP.get(snap.program_state_raw, "unknown"),
            "program_state_error": err,
            "program_state_source": snap.program_state_source,
            "current_line": snap.current_line,
            "current_line_error": err,
            "fault_codes": self._fault_codes(snap),
            # link internals
            "state": snap.state,
            "ip": snap.ip,
            "last_error": snap.last_error,
            "last_success_age_s": snap.age_s(),
            "since_s": snap.since_s(),
            "attempts": snap.attempts,
            "consecutive_failures": snap.consecutive_failures,
            "generation": snap.generation,
            "worker_restarts": snap.worker_restarts,
            "probe_latency_ms": snap.probe_latency_ms,
            "retry_in_s": snap.retry_in_s,
            "busy": snap.busy,
            "busy_label": snap.busy_label,
            "threads": self._link.thread_report(),
        }

    @staticmethod
    def _fault_codes(snap: ConnSnapshot) -> dict[str, Any]:
        """Controller fault codes as of the last heartbeat.

        Collected by the probe rather than fetched here, so the diagnostics page costs
        no robot traffic. `source` says where the codes came from and must be read
        alongside them:

          "rpc"   — asked the controller directly (robot_link._read_fault_codes).
          "cache" — robot_state_pkg, which only the CNDE stream fills.
          "none"  — neither channel is available, so a blank code is NOT evidence
                    of no fault. CNDE does not connect on the FR-16, so this was
                    the only outcome until the raw read was added.
        """
        return {
            "main_code": snap.fault_main,
            "sub_code": snap.fault_sub,
            "safety_code": None,
            "safety_error": 0,
            "robot_error_error": 0 if snap.fault_source == "cache" else -1,
            "source": snap.fault_source,
        }

    def reset_errors(self) -> None:
        """Clear the controller's resettable faults (ResetAllError).

        Refused up front in the two cases where the call cannot work, so the
        operator gets a reason instead of a bare code: no XML-RPC link (the
        `telemetry` window included — the verb would never arrive), and an
        engaged E-stop, which only a physical release clears. A 0 return means
        the controller accepted the request, not that the fault is gone; the
        header chip shows whether it actually cleared on the next frame.
        """
        ustate = self.get_universal_state()
        if not ustate.commands_available:
            raise RuntimeError("Commands are unavailable — the robot is not accepting XML-RPC right now")
        if ustate.emergency_stop:
            raise RuntimeError("E-stop is engaged — release it before resetting")
        err = self._call(lambda r: r.ResetAllError())
        err_code, _ = self._unpack(err)
        self._last_reset_ts = time.time()
        if err_code != 0:
            raise RuntimeError(f"ResetAllError failed (code {err_code})")

    def last_reset_age_s(self) -> float | None:
        """Seconds since reset_errors() last reached the controller, or None."""
        if self._last_reset_ts is None:
            return None
        return max(0.0, time.time() - self._last_reset_ts)

    def uptime(self) -> str:
        elapsed = int(time.time() - self._start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
