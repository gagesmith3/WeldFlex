"""Owns the connection to the FAIRINO controller.

Everything about *being connected* lives here — establishing the RPC session, keeping
it alive, noticing when it dies, recovering, and reporting what is going on.
`robot_service.WeldFlexRobotService` sits on top and owns everything about *what to
ask the robot to do*.

Three ideas carry the design:

**One thread touches the SDK.** All SDK calls, including the heartbeat probe, run on a
single `_SdkWorker` thread. Nothing else ever holds an SDK object while calling it, so
there is no socket interleaving and no lock ordering to get wrong.

**The supervisor owns connection I/O.** Constructing `Robot.RPC()` can block for
seconds and tearing one down can block for longer, so both happen only on the
supervisor thread. Request threads never build or destroy a client; they read a cached
snapshot or queue work. `_state_lock` is held only across reference swaps, never across
I/O.

**Clients are generation-tagged.** Every client carries an epoch. A failure can only
invalidate the generation it observed, so a status poll timing out at 5s cannot tear
down the client that a 30s upload is still using.
"""

from __future__ import annotations

import concurrent.futures
import io
import ipaddress
import os
import queue
import random
import re
import sys
import threading
import time
import xmlrpc.client
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from robot_feed import FeedSnapshot, StatusFeed

# The FAIRINO SDK prints Chinese debug text to stdout/stderr. On Windows the
# default console codec (cp1252) can't encode it, which raises UnicodeEncodeError
# and gets mistaken for a connection failure. Force UTF-8 stdio regardless of how
# the process was launched.
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _bootstrap_sdk() -> None:
    """Add the local FAIRINO SDK to sys.path so it can be imported."""
    workspace_root = Path(__file__).resolve().parents[1]
    sdk_root = workspace_root / "fairino-python-sdk-main"

    platform_dir = "windows" if sys.platform == "win32" else "linux"
    fallback_dir = "linux" if sys.platform == "win32" else "windows"

    candidates = [
        os.getenv("WELDFLEX_FAIRINO_PATH"),
        str(sdk_root / platform_dir),
        str(sdk_root / fallback_dir),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        fairino_pkg = path / "fairino"
        if fairino_pkg.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
            return


_bootstrap_sdk()

try:
    from fairino import Robot  # type: ignore[import]
except ImportError as exc:
    raise ImportError(
        "Cannot import FAIRINO SDK. Set WELDFLEX_FAIRINO_PATH to the SDK platform folder."
    ) from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_port(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 65535 else default


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if minimum <= value <= maximum else default


XMLRPC_PORT = 20003
CNDE_PORT = _env_port("WELDFLEX_CNDE_PORT", 20005)
CNDE_PERIOD_MS = _env_int("WELDFLEX_CNDE_PERIOD_MS", 20, 8, 1000)
CNDE_FORCE_FRESH_S = _env_float("WELDFLEX_CNDE_FORCE_FRESH_S", 0.5)

# Socket timeout for the XML-RPC channel. The SDK never sets one: RPC.__init__ probes
# under socket.setdefaulttimeout(1) then restores it to None (Robot.py:2294-2297), so
# without this every XML-RPC call can block indefinitely.
XMLRPC_SOCKET_TIMEOUT_S = _env_float("WELDFLEX_XMLRPC_TIMEOUT_S", 12.0)

HEARTBEAT_IDLE_S = _env_float("WELDFLEX_HEARTBEAT_IDLE_S", 1.0)
HEARTBEAT_FAST_S = _env_float("WELDFLEX_HEARTBEAT_FAST_S", 0.5)
PROBE_TIMEOUT_S = _env_float("WELDFLEX_PROBE_TIMEOUT_S", 12.0)

# Consecutive probe failures before the link is declared faulted rather than degraded.
FAIL_THRESHOLD = int(_env_float("WELDFLEX_FAIL_THRESHOLD", 30))
# How long a snapshot may go unrefreshed during a long command before it is called stale.
BUSY_STALE_S = _env_float("WELDFLEX_BUSY_STALE_S", 45.0)
# How long a single SDK call may run before its worker is presumed poisoned.
STUCK_AFTER_S = _env_float("WELDFLEX_STUCK_AFTER_S", 120.0)

BACKOFF_START_S = 0.5
BACKOFF_MAX_S = _env_float("WELDFLEX_BACKOFF_MAX_S", 10.0)

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,251}[A-Za-z0-9])?$")


class SdkCommError(RuntimeError):
    """The SDK returned a communication error code (-4/-3/-2) rather than data."""


class RobotUnreachable(RuntimeError):
    """XML-RPC transport failure.

    Deliberately NOT an OSError subclass. Most SDK methods wrap their RPC call in
    `while flag: try: ... except socket.error: flag = True` (e.g. GetCurrentLine,
    Robot.py:7050-7065). Since Python 3.10 socket.timeout IS TimeoutError, itself an
    OSError — so a plain socket timeout gets swallowed by those handlers and turns an
    indefinite block into an indefinite busy-spin. Raising a non-OSError escapes them.
    """


class _TimeoutTransport(xmlrpc.client.Transport):
    """xmlrpc.client transport with a real socket timeout, reported as RobotUnreachable."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        # Set the timeout explicitly rather than relying on the global default, which
        # the SDK constructor mutates and restores while we may be mid-call.
        conn.timeout = self._timeout
        if getattr(conn, "sock", None) is not None:
            conn.sock.settimeout(self._timeout)
        return conn

    def request(self, host, handler, request_body, verbose=False):
        try:
            return super().request(host, handler, request_body, verbose)
        except OSError as exc:
            # Drop the poisoned keep-alive connection so the next call redials.
            self.close()
            raise RobotUnreachable(f"XML-RPC to {host} failed: {exc}") from exc


def make_proxy(robot_ip: str, timeout: float = XMLRPC_SOCKET_TIMEOUT_S) -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(
        f"http://{robot_ip}:{XMLRPC_PORT}",
        transport=_TimeoutTransport(timeout),
        allow_none=True,
    )


def harden_client(client: Any, robot_ip: str) -> None:
    """Make an SDK RPC instance safe to use: bounded XML-RPC, no SDK-owned reconnect.

    Rebinding `client.robot` is safe — it is assigned only in RPC.__init__
    (Robot.py:2242/2297/2308) and nulled in CloseRPC, so our proxy lives as long as the
    client does.

    SetReConnectParam(False, ...) disables the SDK's own reconnect machinery, which
    otherwise spawns an untracked thread from FRCNDEClient's recv loop on every
    disconnect (Robot.py:1851-1858) and can flip the process-global RPC.is_connect
    underneath a healthy link. It also guarantees `reconnect_flag` stays False, so no
    SDK method parks on `while self.reconnect_flag: sleep(0.1)` and CloseRPC's identical
    gate cannot hang. Reconnect becomes solely this module's responsibility.
    """
    client.robot = make_proxy(robot_ip)
    try:
        client.SetReConnectParam(False, 0, 1000)
    except Exception:
        pass


def is_conn_code(val: Any) -> bool:
    """True only for the integer SDK comm-error codes -4/-3/-2.

    The int check matters: `-2.0 in (-4, -3, -2)` is True by numeric equality, so
    without it a legitimate TCP pose component of -2.0 from jog_pose(), or an fz reading
    of -3.0 from ft_read(), would be mistaken for a dropped connection.
    """
    return isinstance(val, int) and not isinstance(val, bool) and val in (-4, -3, -2)


def has_conn_error(val: Any) -> bool:
    """Recursively check for an SDK communication-error code anywhere in a result."""
    if is_conn_code(val):
        return True
    if isinstance(val, (list, tuple)):
        if len(val) > 0 and is_conn_code(val[0]):
            return True
        for item in val:
            if has_conn_error(item):
                return True
    return False


def is_link_failure(exc: BaseException) -> bool:
    """Does this exception mean the *connection* is bad, as opposed to the call?

    Only evidence about the transport should tear a client down. A call that raises
    because a response had an unexpected shape, or because an argument was wrong, says
    nothing about the link — and dropping the connection over it would put every other
    page into a reconnect wait for no reason.
    """
    if isinstance(exc, (RobotUnreachable, SdkCommError, OSError)):
        return True
    if isinstance(exc, xmlrpc.client.ProtocolError):
        return True
    # xmlrpc.client.Fault is the server reporting an application error over a working
    # connection — explicitly not a link failure.
    return False


def validate_host(host: str) -> str:
    """Return a cleaned IP or hostname, or raise ValueError."""
    host = (host or "").strip()
    if not host:
        raise ValueError("Robot address cannot be empty")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if _HOSTNAME_RE.match(host):
        return host
    raise ValueError(f"Not a valid IP address or hostname: {host!r}")


class ConnState(str, Enum):
    DISCONNECTED = "disconnected"  # operator-requested; no reconnect attempts
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"          # client alive but probes failing
    FAULTED = "faulted"            # no usable client; retrying on backoff


@dataclass(frozen=True)
class ConnSnapshot:
    """Immutable view of the link. Built fresh and reference-swapped, never mutated."""

    state: str = ConnState.DISCONNECTED.value
    ip: str = ""
    connected: bool = False
    since_ts: float | None = None
    last_success_ts: float | None = None
    last_error: str | None = None
    last_error_ts: float | None = None
    attempts: int = 0
    consecutive_failures: int = 0
    generation: int = 0
    worker_restarts: int = 0
    busy: bool = False
    busy_label: str | None = None
    busy_for_s: float | None = None
    probe_latency_ms: float | None = None
    retry_in_s: float | None = None
    program_state_raw: int | None = None
    program_state_source: str = "none"  # "rpc" | "cache" | "none"
    current_line: int | None = None
    line_edge_seq: int = 0
    fault_main: int | None = None
    fault_sub: int | None = None
    # "cache" once read from robot_state_pkg, "none" if never populated. The FR-16 never
    # connects CNDE, which is the only writer of that struct, so these stay 0 there —
    # absence of a code is not evidence of no fault.
    fault_source: str = "none"

    def age_s(self) -> float | None:
        """Seconds since the last successful robot round trip."""
        if self.last_success_ts is None:
            return None
        return max(0.0, time.time() - self.last_success_ts)

    def since_s(self) -> float | None:
        if self.since_ts is None:
            return None
        return max(0.0, time.time() - self.since_ts)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["age_s"] = self.age_s()
        d["since_s"] = self.since_s()
        return d


@dataclass(frozen=True)
class ForceSnapshot:
    """Latest complete CNDE force frame, independent of browser polling."""

    values: tuple[float, float, float, float, float, float] | None = None
    received_monotonic: float | None = None
    generation: int | None = None
    source: str = "none"

    def age_s(self) -> float | None:
        if self.received_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self.received_monotonic)

    def is_fresh(self, max_age_s: float = CNDE_FORCE_FRESH_S) -> bool:
        age = self.age_s()
        return self.values is not None and age is not None and age <= max_age_s


@dataclass(frozen=True)
class _ClientHandle:
    gen: int
    rpc: Any
    ip: str
    created_ts: float = field(default_factory=time.time)


class _SdkWorker:
    """One daemon thread draining a work queue.

    Disposable by design. If a call wedges inside the SDK the thread cannot be killed,
    so the watchdog abandons the whole worker and builds another. It is a daemon thread
    rather than a ThreadPoolExecutor worker precisely so an abandoned one cannot block
    interpreter exit — ThreadPoolExecutor joins its non-daemon workers at shutdown, which
    would leave the process unable to exit and therefore unable to be restarted by
    systemd.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._submit_lock = threading.Lock()
        self._sequence = 0
        self._coalesced: dict[str, concurrent.futures.Future] = {}
        self._active: tuple[str, float] | None = None
        self._retired = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(
        self,
        fn: Callable[[], Any],
        label: str = "",
        priority: int = 0,
        coalesce_key: str | None = None,
    ) -> concurrent.futures.Future:
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._submit_lock:
            if self._retired:
                future.set_exception(RuntimeError(f"SDK worker {self.name} is retired"))
                return future
            if coalesce_key is not None:
                existing = self._coalesced.get(coalesce_key)
                if existing is not None and not existing.done():
                    return existing
                self._coalesced[coalesce_key] = future
            self._sequence += 1
            self._queue.put((priority, self._sequence, future, fn, label, coalesce_key))
        return future

    def active(self) -> tuple[str, float] | None:
        """(label, monotonic start) of the in-flight call, or None."""
        return self._active

    def is_busy(self) -> bool:
        return self._active is not None

    def busy_for(self) -> float | None:
        act = self._active
        return None if act is None else time.monotonic() - act[1]

    def retire(self) -> None:
        """Stop accepting work and exit once the current call returns (if it ever does)."""
        with self._submit_lock:
            self._retired = True
            self._sequence += 1
            # Commands, core probes, and detailed telemetry use priorities 0, 1, and
            # 2. Retire only after accepting work has drained, matching SimpleQueue's
            # former sentinel behavior.
            self._queue.put((99, self._sequence, None, None, "", None))

    def alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            _, _, future, fn, label, coalesce_key = item
            if future is None:
                return
            if not future.set_running_or_notify_cancel():
                continue
            self._active = (label or "call", time.monotonic())
            try:
                future.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 - must not kill the worker
                future.set_exception(exc)
            finally:
                self._active = None
                if coalesce_key is not None:
                    with self._submit_lock:
                        if self._coalesced.get(coalesce_key) is future:
                            del self._coalesced[coalesce_key]


class RobotLink:
    """Establishes and maintains the controller connection; serves a cached snapshot."""

    def __init__(self, robot_ip: str) -> None:
        self._ip = validate_host(robot_ip)
        self._state_lock = threading.Lock()
        self._handle: _ClientHandle | None = None
        self._gen = 0
        self._worker = _SdkWorker("robot-sdk-0")
        self._worker_seq = 0
        self._worker_restarts = 0

        self._state = ConnState.DISCONNECTED
        self._since_ts: float | None = None
        self._last_success_ts: float | None = None
        self._last_error: str | None = None
        self._last_error_ts: float | None = None
        self._attempts = 0
        self._consecutive_failures = 0
        self._probe_latency_ms: float | None = None
        self._program_state_raw: int | None = None
        self._program_state_source = "none"
        self._current_line: int | None = None
        self._line_edge_seq = 0
        self._fault_main: int | None = None
        self._fault_sub: int | None = None
        self._fault_source = "none"
        self._next_attempt_ts: float | None = None
        self._raw_state_supported: bool | None = None
        self._raw_fault_supported: bool | None = None
        self._force_snapshot = ForceSnapshot()

        # The port-8083 status stream. Deliberately not tied to the XML-RPC
        # client or its generation: it has its own socket and its own thread, so
        # telemetry survives a dead command channel and a command channel
        # survives a dead feed. Started on operator intent rather than on a
        # successful RPC connect, for the same reason.
        self._feed = StatusFeed()

        self._snapshot = ConnSnapshot(ip=self._ip)

        self._enabled = False           # operator intent: should we hold a connection?
        self._pending_ip: str | None = None
        self._teardown_queue: list[tuple[_ClientHandle, bool]] = []
        self._fast_heartbeat = False
        self._backoff = BACKOFF_START_S

        self._wake = threading.Event()
        self._gen_changed = threading.Condition(self._state_lock)
        self._stop = threading.Event()
        self._supervisor: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle

    @property
    def robot_ip(self) -> str:
        with self._state_lock:
            return self._ip

    def start(self, connect: bool = True) -> None:
        """Start the supervisor. Idempotent and non-blocking."""
        with self._state_lock:
            if self._supervisor is not None and self._supervisor.is_alive():
                if connect:
                    self._enabled = True
                    self._wake.set()
                return
            self._enabled = connect
            self._stop.clear()
            self._supervisor = threading.Thread(
                target=self._supervisor_loop, name="robot-supervisor", daemon=True
            )
            ip = self._ip
        if connect:
            self._set_state(ConnState.CONNECTING, "starting")
            self._feed.start(ip)
        self._supervisor.start()
        self._wake.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the supervisor, close the connection, retire the worker. Idempotent."""
        with self._state_lock:
            self._enabled = False
            supervisor = self._supervisor
            self._supervisor = None
        self._stop.set()
        self._wake.set()
        if supervisor is not None and supervisor.is_alive():
            supervisor.join(timeout=timeout)

        self._feed.stop()
        handle = self._take_handle()
        if handle is not None:
            self._teardown(handle, close_rpc=True)
        self._set_state(ConnState.DISCONNECTED, "shut down")
        self._worker.retire()

    def connect(self) -> None:
        """Operator intent: hold a connection. Returns immediately."""
        with self._state_lock:
            self._enabled = True
            self._attempts = 0
            self._backoff = BACKOFF_START_S
            self._next_attempt_ts = None
            ip = self._ip
        self._set_state(ConnState.CONNECTING, "connect requested")
        self._feed.start(ip)
        self.start(connect=True)
        self._wake.set()

    def disconnect(self) -> None:
        """Operator intent: stay disconnected until connect() is called.

        True manual mode — the supervisor makes no further attempts. A technician
        working on the controller will not have the app reconnecting underneath them.
        """
        with self._state_lock:
            self._enabled = False
            handle, self._handle = self._handle, None
        # A technician working on the controller gets a genuinely quiet link:
        # the feed's socket goes too, not just the RPC client.
        self._feed.stop()
        if handle is not None:
            self._queue_teardown(handle, close_rpc=True)
        self._set_state(ConnState.DISCONNECTED, "disconnected by operator")
        self._wake.set()

    def request_reconnect(self) -> None:
        """Drop the current client and build a fresh one."""
        with self._state_lock:
            self._enabled = True
            self._attempts = 0
            self._backoff = BACKOFF_START_S
            self._next_attempt_ts = None
            handle, self._handle = self._handle, None
        if handle is not None:
            self._queue_teardown(handle, close_rpc=True)
        self._set_state(ConnState.CONNECTING, "reconnect requested")
        self.start(connect=True)
        self._wake.set()

    def set_ip(self, ip: str) -> bool:
        """Retarget the connection. Returns True if the address actually changed."""
        cleaned = validate_host(ip)
        with self._state_lock:
            if cleaned == self._ip:
                return False
            self._pending_ip = cleaned
            handle, self._handle = self._handle, None
            self._attempts = 0
            self._backoff = BACKOFF_START_S
            self._next_attempt_ts = None
        if handle is not None:
            self._queue_teardown(handle, close_rpc=True)
        self._feed.retarget(cleaned)
        self._set_state(ConnState.CONNECTING, f"retargeting to {cleaned}")
        self._wake.set()
        return True

    def set_heartbeat_hint(self, fast: bool) -> None:
        """Probe faster while a program is running so current_line stays fresh."""
        with self._state_lock:
            self._fast_heartbeat = bool(fast)
        self._wake.set()

    # ------------------------------------------------------------------ reads

    def snapshot(self) -> ConnSnapshot:
        with self._state_lock:
            return self._snapshot

    def force_snapshot(self) -> ForceSnapshot:
        """Return the latest CNDE frame without performing robot I/O."""
        with self._state_lock:
            snapshot = self._force_snapshot
            if snapshot.generation != self._gen:
                return ForceSnapshot()
            return snapshot

    def feed_snapshot(self) -> FeedSnapshot:
        """Latest port-8083 frame, without performing any robot I/O.

        Not filtered by the RPC client's generation — the feed has its own
        socket and its own generation, and an RPC reconnect says nothing about
        whether the status stream is still good.
        """
        return self._feed.snapshot()

    def feed_stats(self) -> dict[str, Any]:
        return self._feed.stats().as_dict()

    def thread_report(self) -> dict[str, Any]:
        """Live thread census — the only way to see a thread leak in the field."""
        names = sorted(t.name for t in threading.enumerate())
        sdk_prefixes = ("robot-sdk", "robot-supervisor", "robot-feed",
                        "RobotUdpDataRecvThread", "CNDERecvThread")
        return {
            "count": len(names),
            "names": names,
            "sdk": [n for n in names if n.startswith(sdk_prefixes)],
        }

    # ------------------------------------------------------------------ dispatch

    def call(
        self,
        fn: Callable[[Any], Any],
        *,
        timeout: float,
        retries: int = 3,
        label: str = "",
        priority: int = 0,
        coalesce_key: str | None = None,
    ) -> Any:
        """Run an SDK call on the worker thread against the current client.

        Never builds a client — that is the supervisor's job.

        If no client is available, whether to wait for one is decided by `retries`, which
        the callers already use to distinguish the two kinds of work. A command
        (retries>1) waits briefly, so a button pressed during a momentary blip still
        goes through. A polled read (retries=1 — status, jog_pose, ft_read) fails
        immediately, because blocking a 300ms poll for seconds just stacks up requests
        behind a robot that is already known to be away.
        """
        grace = min(timeout, 3.0) if retries > 1 else 0.0
        last_exc: BaseException | None = None

        for attempt in range(max(1, retries)):
            handle = self._await_handle(grace)
            if handle is None:
                snap = self.snapshot()
                detail = snap.last_error or "no connection established"
                raise RuntimeError(f"Robot not connected ({snap.state}): {detail}")

            try:
                key = None if coalesce_key is None else f"{coalesce_key}:{handle.gen}"
                future = self._worker.submit(
                    lambda: fn(handle.rpc),
                    label=label or "call",
                    priority=priority,
                    coalesce_key=key,
                )
                result = future.result(timeout=timeout)
                if has_conn_error(result):
                    raise SdkCommError("SDK reported communication failure")
                self._note_success()
                return result
            except concurrent.futures.TimeoutError as exc:
                # Giving up waiting is not evidence that the link is dead. This call may
                # simply be queued behind a legitimate long one (a 30s upload), and
                # tearing the client down here would kill *that* command's connection
                # mid-flight. Record it and fail this caller only; the supervisor's probe
                # owns "actually dead" and the watchdog owns "actually stuck".
                reason = f"{label or 'call'} timed out after {timeout:.0f}s"
                self._note_error(reason)
                raise RuntimeError(
                    f"Robot did not respond within {int(timeout)}s — connection may be lost."
                ) from exc
            except BaseException as exc:  # noqa: BLE001 - classified below
                if not is_link_failure(exc):
                    # The call failed, the connection did not. Report it to this caller
                    # and leave the link alone — retrying would just fail identically.
                    raise
                last_exc = exc
                is_last_attempt = (attempt == max(1, retries) - 1)
                if is_last_attempt:
                    self._invalidate(handle.gen, f"{label or 'call'}: {exc}")
                    raise
                self._note_error(f"{label or 'call'} attempt {attempt + 1} failed: {exc}")
                # Wait for the supervisor to publish a replacement if invalidated, or retry.
                self._await_new_generation(handle.gen, grace)

        raise last_exc if last_exc is not None else RuntimeError("call failed")

    # ------------------------------------------------------------------ internals

    def _await_handle(self, timeout: float) -> _ClientHandle | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._state_lock:
                if self._handle is not None:
                    return self._handle
                if not self._enabled:
                    return None  # manual disconnect: no point waiting
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._gen_changed.wait(remaining)

    def _await_new_generation(self, old_gen: int, timeout: float) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._state_lock:
            while self._handle is None or self._handle.gen == old_gen:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._enabled:
                    return
                self._gen_changed.wait(remaining)

    def _take_handle(self) -> _ClientHandle | None:
        with self._state_lock:
            handle, self._handle = self._handle, None
            return handle

    def _invalidate(self, gen: int, reason: str) -> None:
        """Mark a generation dead. Never closes anything on the calling thread.

        Teardown blocks for seconds (FRCNDEClient.close alone can spend ~3s contending
        for the recv mutex, plus thread joins), so it is handed to the supervisor.
        """
        with self._state_lock:
            if self._handle is None or self._handle.gen != gen:
                return  # already replaced; a stale failure must not kill a newer client
            handle, self._handle = self._handle, None
            self._last_error = reason
            self._last_error_ts = time.time()
            self._consecutive_failures += 1
            self._queue_teardown_locked(handle, close_rpc=False)
        self._set_state(ConnState.FAULTED, reason)
        self._wake.set()

    def _note_error(self, reason: str) -> None:
        """Record a failure that is not, on its own, grounds for dropping the client."""
        with self._state_lock:
            self._last_error = reason
            self._last_error_ts = time.time()
            self._publish_locked()

    def _note_success(self) -> None:
        with self._state_lock:
            self._last_success_ts = time.time()
            self._consecutive_failures = 0
            self._publish_locked()

    def _queue_teardown(self, handle: _ClientHandle, close_rpc: bool) -> None:
        with self._state_lock:
            self._queue_teardown_locked(handle, close_rpc)
        self._wake.set()

    def _queue_teardown_locked(self, handle: _ClientHandle, close_rpc: bool) -> None:
        self._teardown_queue.append((handle, close_rpc))

    def _set_state(self, state: ConnState, reason: str | None = None) -> None:
        with self._state_lock:
            changed = self._state != state
            if changed:
                self._state = state
                self._since_ts = time.time()
            if reason and state in (ConnState.FAULTED, ConnState.DEGRADED):
                self._last_error = reason
                self._last_error_ts = time.time()
            # RPC.is_connect is a lock-free class attribute on the SDK (Robot.py:2219)
            # with no single owner: a failed constructor on any instance flips it for the
            # whole process, after which every @xmlrpc_timeout method returns a bare -4.
            # We deliberately override it so our state machine is authoritative.
            Robot.RPC.is_connect = state in (ConnState.CONNECTED, ConnState.DEGRADED)
            self._publish_locked()

    def _publish_locked(self) -> None:
        """Rebuild the immutable snapshot. Caller must hold _state_lock."""
        busy_label = None
        busy_for = None
        act = self._worker.active()
        if act is not None:
            busy_label, started = act
            busy_for = time.monotonic() - started

        retry_in = None
        if self._next_attempt_ts is not None:
            retry_in = max(0.0, self._next_attempt_ts - time.time())

        self._snapshot = ConnSnapshot(
            state=self._state.value,
            ip=self._ip,
            connected=self._state == ConnState.CONNECTED,
            since_ts=self._since_ts,
            last_success_ts=self._last_success_ts,
            last_error=self._last_error,
            last_error_ts=self._last_error_ts,
            attempts=self._attempts,
            consecutive_failures=self._consecutive_failures,
            generation=self._gen,
            worker_restarts=self._worker_restarts,
            busy=act is not None,
            busy_label=busy_label,
            busy_for_s=busy_for,
            probe_latency_ms=self._probe_latency_ms,
            retry_in_s=retry_in,
            program_state_raw=self._program_state_raw,
            program_state_source=self._program_state_source,
            current_line=self._current_line,
            line_edge_seq=self._line_edge_seq,
            fault_main=self._fault_main,
            fault_sub=self._fault_sub,
            fault_source=self._fault_source,
        )
        self._gen_changed.notify_all()

    # ------------------------------------------------------------------ supervisor

    def _supervisor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 - the supervisor must never die
                self._set_state(ConnState.FAULTED, f"supervisor error: {exc}")
            self._wake.wait(self._tick_interval())
            self._wake.clear()

    def _tick_interval(self) -> float:
        with self._state_lock:
            if not self._enabled:
                return 1.0
            if self._handle is None:
                return min(self._backoff, BACKOFF_MAX_S)
            return HEARTBEAT_FAST_S if self._fast_heartbeat else HEARTBEAT_IDLE_S

    def _tick(self) -> None:
        self._drain_teardowns()
        self._check_worker_health()

        with self._state_lock:
            enabled = self._enabled
            handle = self._handle
            pending_ip = self._pending_ip
            self._pending_ip = None
            if pending_ip:
                self._ip = pending_ip

        if not enabled:
            return
        if pending_ip:
            self._set_state(ConnState.CONNECTING, f"address set to {pending_ip}")

        if handle is None:
            self._attempt_open()
            return

        # Skip the probe while a command is in flight unless we are in a fast
        # heartbeat mode for a running job. A running job wants current_line and
        # fault updates to stay fresh, so the lightweight direct probe is allowed
        # to run even while the command worker is occupied.
        if self._worker.is_busy() and not self._fast_heartbeat:
            with self._state_lock:
                stale = (
                    self._last_success_ts is not None
                    and time.time() - self._last_success_ts > BUSY_STALE_S
                )
                self._publish_locked()
            if stale:
                self._set_state(ConnState.DEGRADED, "no successful call while busy")
            return

        self._probe(handle)

    def _drain_teardowns(self) -> None:
        while True:
            with self._state_lock:
                if not self._teardown_queue:
                    return
                handle, close_rpc = self._teardown_queue.pop(0)
            self._teardown(handle, close_rpc=close_rpc)

    def _check_worker_health(self) -> None:
        busy_for = self._worker.busy_for()
        if busy_for is None or busy_for < STUCK_AFTER_S:
            return
        act = self._worker.active()
        label = act[0] if act else "unknown"

        handle = self._take_handle()
        old = self._worker
        with self._state_lock:
            self._worker_seq += 1
            self._worker_restarts += 1
            seq = self._worker_seq
        # Abandon rather than join: the thread is stuck inside the SDK and cannot be
        # interrupted. It is a daemon, so leaving it costs a thread and nothing else.
        old.retire()
        self._worker = _SdkWorker(f"robot-sdk-{seq}")
        self._set_state(
            ConnState.FAULTED, f"SDK worker stuck in {label} for {busy_for:.0f}s; worker replaced"
        )
        if handle is not None:
            # Never CloseRPC a client whose worker may still be inside it.
            self._teardown(handle, close_rpc=False)

    def _attempt_open(self) -> None:
        with self._state_lock:
            if self._next_attempt_ts is not None and time.time() < self._next_attempt_ts:
                return
            ip = self._ip
            self._attempts += 1
            attempt = self._attempts
            # Only advertise CONNECTING on a fresh attempt. Once faulted, stay faulted
            # while retrying — otherwise the chip strobes CONNECTING/OFFLINE once per
            # backoff. The retry countdown in the snapshot conveys that work continues.
            announce = self._state != ConnState.FAULTED
        if announce:
            self._set_state(ConnState.CONNECTING, None)

        client = None
        try:
            # Configure CNDE port (default 20005) and realtime state subscription
            # to include key telemetry signals (force, program state, robot state, fault codes, mode)
            Robot.SetRobotRealtimeStateConfig(
                [
                    Robot.RobotState.FtSensorData,
                    Robot.RobotState.ProgramState,
                    Robot.RobotState.RobotState,
                    Robot.RobotState.MainCode,
                    Robot.RobotState.SubCode,
                    Robot.RobotState.RobotMode,
                    Robot.RobotState.EmergencyStop,
                    Robot.RobotState.MotionDone,
                ],
                CNDE_PERIOD_MS,
            )
            # Configure the SDK's class-level port before construction because
            # RPC.__init__ starts its receiver immediately.
            Robot.RPC.ROBOT_CNDE_PORT = CNDE_PORT
            client = Robot.RPC(ip)
            harden_client(client, ip)
            cnde = getattr(client, "_cnde_client", None)
            if cnde is not None:
                cnde.set_state_callback(lambda state: self._on_cnde_state(client, state))
            # Capability is controller-session specific. A reconnect may target
            # newer firmware or recover an RPC method that failed transiently.
            self._raw_state_supported = None
            self._raw_fault_supported = None
            # Robot.RPC() blocks for seconds and cannot be interrupted. If the operator
            # retargeted or disconnected while we were in there, drop this client now
            # rather than spending another probe timeout proving an address nobody wants.
            with self._state_lock:
                superseded = self._pending_ip is not None or not self._enabled
            if superseded:
                self._teardown_raw(client, close_rpc=False)
                return
            reading = self._run_probe(client)
        except Exception as exc:  # noqa: BLE001
            if client is not None:
                self._teardown_raw(client, close_rpc=False)
            self._schedule_retry(f"connect to {ip} failed (attempt {attempt}): {exc}")
            return

        with self._state_lock:
            superseded = self._pending_ip is not None or not self._enabled or self._ip != ip
            if not superseded:
                self._gen += 1
                self._handle = _ClientHandle(gen=self._gen, rpc=client, ip=ip)
                self._attempts = 0
                self._consecutive_failures = 0
                self._backoff = BACKOFF_START_S
                self._next_attempt_ts = None
                self._last_success_ts = time.time()
                self._force_snapshot = ForceSnapshot()
                self._record_probe_locked(reading)
        if superseded:
            self._teardown_raw(client, close_rpc=False)
            return
        self._set_state(ConnState.CONNECTED, None)

    def _schedule_retry(self, reason: str) -> None:
        with self._state_lock:
            delay = min(self._backoff, BACKOFF_MAX_S)
            jitter = delay * random.uniform(-0.2, 0.2)
            self._next_attempt_ts = time.time() + max(0.1, delay + jitter)
            self._backoff = min(self._backoff * 2, BACKOFF_MAX_S)
        self._set_state(ConnState.FAULTED, reason)

    def _probe(self, handle: _ClientHandle) -> None:
        try:
            reading = self._run_probe(handle.rpc)
        except concurrent.futures.TimeoutError:
            # Not necessarily fatal — the watchdog owns the truly-stuck case.
            self._note_probe_failure(handle, f"probe timed out after {PROBE_TIMEOUT_S:.0f}s")
            return
        except Exception as exc:  # noqa: BLE001
            self._note_probe_failure(handle, f"probe failed: {exc}")
            return

        with self._state_lock:
            if self._handle is None or self._handle.gen != handle.gen:
                # The request completed after a reconnect or retarget. Its data
                # belongs to a retired socket and must not refresh the new link.
                return
            self._last_success_ts = time.time()
            self._consecutive_failures = 0
            self._record_probe_locked(reading)
        self._set_state(ConnState.CONNECTED, None)

    def _run_probe(self, client: Any) -> dict[str, Any]:
        """Probe the controller for telemetry.

        The SDK worker is the exclusive owner of the client's XML-RPC proxy.
        Fast heartbeats may queue behind an in-flight command, but they must
        never call the same proxy from the supervisor thread: xmlrpc.client
        cannot safely interleave request/response pairs on one connection.
        """
        future = self._worker.submit(
            lambda: self._probe_body(client), label="probe", priority=1
        )
        return future.result(timeout=PROBE_TIMEOUT_S)

    def _on_cnde_state(self, client: Any, state: Any) -> None:
        """Copy a fully decoded CNDE force frame for every in-process consumer."""
        try:
            values = tuple(float(state.ft_sensor_data[index]) for index in range(6))
        except (AttributeError, IndexError, TypeError, ValueError):
            return

        with self._state_lock:
            handle = self._handle
            if handle is None or handle.rpc is not client:
                return
            self._force_snapshot = ForceSnapshot(
                values=values,
                received_monotonic=time.monotonic(),
                generation=handle.gen,
                source="cnde",
            )

    def _probe_body(self, client: Any) -> dict[str, Any]:
        """One real XML-RPC round trip, plus the state the UI needs.

        Calls go through `client.robot` — the raw hardened proxy — rather than the SDK
        wrappers, for two reasons. The wrappers short-circuit to -4 whenever the global
        RPC.is_connect is False, which would make a stale flag self-latching and leave
        the probe unable to ever observe recovery. And they retry forever on socket
        errors, which is the loop that wedges the worker.

        Everything the diagnostics page shows is gathered here so that page can be
        served from cache instead of adding robot traffic per poll.
        """
        proxy = client.robot
        t0 = time.monotonic()
        state_raw, state_src = self._read_program_state(client)
        latency_ms = (time.monotonic() - t0) * 1000.0

        line = None
        try:
            line = self._second(proxy.GetCurrentLine())
        except Exception:  # noqa: BLE001 - liveness already established above
            pass

        fault_main, fault_sub, fault_src = self._read_fault_codes(client)

        return {
            "latency_ms": latency_ms,
            "state_raw": state_raw,
            "state_src": state_src,
            "line": line,
            "fault_main": fault_main,
            "fault_sub": fault_sub,
            "fault_src": fault_src,
        }

    def _read_program_state(self, client: Any) -> tuple[int | None, str]:
        """Prefer the CNDE real-time stream when active; fall back to RPC query."""
        try:
            cnde = getattr(client, "_cnde_client", None)
            streaming = bool(cnde is not None and getattr(cnde, "_robot_state_run_flag", False))
            pkg = getattr(client, "robot_state_pkg", None)
            if streaming and pkg is not None:
                val = getattr(pkg, "program_state", 0)
                if val != 0:
                    return int(val), "cnde"
        except Exception:  # noqa: BLE001
            pass

        if self._raw_state_supported is not False:
            try:
                resp = client.robot.GetProgramState()
                value = self._second(resp)
                if value is not None:
                    self._raw_state_supported = True
                    return int(value), "rpc"
            except xmlrpc.client.Fault:
                # Method genuinely absent on this controller — stop asking.
                self._raw_state_supported = False
            except RobotUnreachable:
                raise
            except Exception:  # noqa: BLE001
                pass

        try:
            pkg = getattr(client, "robot_state_pkg", None)
            if pkg is not None:
                return int(pkg.robot_state), "cache"
        except Exception:  # noqa: BLE001
            pass
        return None, "none"

    def _read_fault_codes(self, client: Any) -> tuple[int | None, int | None, str]:
        """Prefer the CNDE real-time stream when active; fall back to RPC query."""
        try:
            cnde = getattr(client, "_cnde_client", None)
            streaming = bool(cnde is not None and getattr(cnde, "_robot_state_run_flag", False))
            pkg = getattr(client, "robot_state_pkg", None)
            if streaming and pkg is not None:
                main = getattr(pkg, "main_code", 0)
                sub = getattr(pkg, "sub_code", 0)
                return int(main) or None, int(sub) or None, "cnde"
        except Exception:  # noqa: BLE001
            pass

        if self._raw_fault_supported is not False:
            try:
                resp = client.robot.GetRobotErrorCode()
                if isinstance(resp, (list, tuple)) and len(resp) >= 3 and int(resp[0]) == 0:
                    self._raw_fault_supported = True
                    # 0 means "no fault" for both codes; normalise to None so every
                    # consumer's `if fault_main:` reads the same as it always did.
                    return int(resp[1]) or None, int(resp[2]) or None, "rpc"
            except xmlrpc.client.Fault:
                # Method genuinely absent on this controller — stop asking.
                self._raw_fault_supported = False
            except RobotUnreachable:
                raise
            except Exception:  # noqa: BLE001
                pass

        try:
            cnde = getattr(client, "_cnde_client", None)
            streaming = bool(cnde is not None and getattr(cnde, "_robot_state_run_flag", False))
            pkg = getattr(client, "robot_state_pkg", None)
            if streaming and pkg is not None:
                return int(pkg.main_code) or None, int(pkg.sub_code) or None, "cache"
        except Exception:  # noqa: BLE001
            pass
        return None, None, "none"

    @staticmethod
    def _second(resp: Any) -> Any:
        """Pull the value out of the SDK's (err, value) convention."""
        if isinstance(resp, (list, tuple)):
            if len(resp) >= 2:
                return resp[1]
            return None
        return resp

    def _record_probe_locked(self, reading: dict[str, Any]) -> None:
        self._probe_latency_ms = reading["latency_ms"]
        self._program_state_raw = reading["state_raw"]
        self._program_state_source = reading["state_src"]
        line = reading["line"]
        # A monotonic edge counter, so a consumer tracking program progress sees every
        # change even if it samples less often than the heartbeat.
        if line is not None and line != self._current_line:
            self._line_edge_seq += 1
        self._current_line = line
        self._fault_main = reading["fault_main"]
        self._fault_sub = reading["fault_sub"]
        self._fault_source = reading["fault_src"]

    def _note_probe_failure(self, handle: _ClientHandle, reason: str) -> None:
        with self._state_lock:
            if self._handle is None or self._handle.gen != handle.gen:
                return
            self._consecutive_failures += 1
            failures = self._consecutive_failures
            self._last_error = reason
            self._last_error_ts = time.time()

        if failures >= FAIL_THRESHOLD:
            self._invalidate(handle.gen, reason)
        else:
            self._set_state(ConnState.DEGRADED, reason)

    def _teardown(self, handle: _ClientHandle, *, close_rpc: bool) -> None:
        self._teardown_raw(handle.rpc, close_rpc=close_rpc)

    def _teardown_raw(self, client: Any, *, close_rpc: bool) -> None:
        """Close a client. Supervisor thread only — this can block for seconds."""
        if client is None:
            return
        if close_rpc:
            # Safe now that SetReConnectParam(False, ...) keeps reconnect_flag False:
            # CloseRPC's `while self.reconnect_flag` gate (Robot.py:13413) cannot hang.
            try:
                client.CloseRPC()
            except Exception:  # noqa: BLE001
                pass
        # Always close the sockets afterwards: CloseRPC sends the CNDE stop frame and
        # drops the XML-RPC proxy but never touches _udp_client, whose recv thread would
        # otherwise outlive the connection.
        try:
            if getattr(client, "_udp_client", None):
                client._udp_client.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(client, "_cnde_client", None):
                client._cnde_client.close()
        except Exception:  # noqa: BLE001
            pass
