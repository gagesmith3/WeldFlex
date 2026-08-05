"""Owns the TCP status feed on controller port 8083.

The controller pushes a status frame every 100 ms (settable 8-100 ms on the
pendant, *system settings -> maintenance mode*). One frame carries program
state, current line, both fault-code pairs, DI and DO bitmaps, force/torque,
TCP pose, e-stop and robot mode — see `frame_8083.py` for the layout and
`docs/collabrative_robot_8083_port_status.md` for the vendor spec.

Why this exists as its own module and its own thread:

**It is deliberately independent of the XML-RPC link.** `robot_link.py` runs
every SDK call through one worker thread, so a read that stalls on its socket
timeout starves everything else queued behind it — which is why telemetry got
worse exactly when a program was running. This feed shares nothing with that
worker: separate socket, separate thread, separate reconnect. An XML-RPC
reconnect must not blank telemetry, and a dead feed must not tear down a
working command channel.

**It has its own generation counter, not the link's.** Tying feed frames to the
RPC client's generation would discard perfectly good telemetry every time the
command channel reconnected, for no reason — the two connections fail
independently.

**It refuses to publish a frame it cannot vouch for.** The vendor manual never
states byte order, so the first frame of every connection is checked with
`looks_sane()`. If little-endian fails and big-endian passes, the connection
latches big-endian and says so. If neither passes, the feed reports
`LAYOUT_ERROR` and publishes nothing at all rather than serving plausible
garbage to a page an operator reads.
"""

from __future__ import annotations

import logging
import os
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

import frame_8083 as f8

log = logging.getLogger(__name__)


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


STATUS_PORT = _env_port("WELDFLEX_STATUS_PORT", 8083)

# How long a frame stays usable. The controller's default period is 100 ms and
# its slowest configurable period is 100 ms, so 3 s is ~30 missed frames — long
# enough to ride out a scheduling hiccup, short enough that an operator is not
# reading second-old force data believing it is live.
FEED_STALE_S = _env_float("WELDFLEX_FEED_STALE_S", 3.0)

CONNECT_TIMEOUT_S = _env_float("WELDFLEX_FEED_CONNECT_TIMEOUT_S", 5.0)

# Bounded so the loop wakes to notice stop/retarget even when the controller has
# gone quiet without dropping the socket.
RECV_TIMEOUT_S = _env_float("WELDFLEX_FEED_RECV_TIMEOUT_S", 1.0)
RECV_SIZE = 8192

BACKOFF_START_S = 0.5
BACKOFF_MAX_S = _env_float("WELDFLEX_FEED_BACKOFF_MAX_S", 10.0)

_EMPTY: Mapping[str, Any] = MappingProxyType({})


class FeedState(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    STREAMING = "streaming"
    ERROR = "error"                # socket failed; retrying on backoff
    LAYOUT_ERROR = "layout_error"  # frames arrive but do not decode sanely


@dataclass(frozen=True)
class FeedSnapshot:
    """Latest decoded frame. Built fresh and reference-swapped, never mutated.

    Mirrors `ConnSnapshot`/`ForceSnapshot` in `robot_link.py`: readers take the
    reference under a lock and are then free of it.
    """

    fields: Mapping[str, Any] = _EMPTY
    received_monotonic: float | None = None
    received_ts: float | None = None
    generation: int = 0
    data_len: int | None = None
    little_endian: bool = True

    def age_s(self) -> float | None:
        if self.received_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self.received_monotonic)

    def is_fresh(self, max_age_s: float = FEED_STALE_S) -> bool:
        age = self.age_s()
        return bool(self.fields) and age is not None and age <= max_age_s

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)

    # --- the signals WeldFlex actually reads -----------------------------
    # Each returns None when the field was not in the frame, so "this firmware
    # did not send it" never reads as a zero.

    @property
    def program_state(self) -> int | None:
        return self.fields.get("program_state")

    @property
    def current_line(self) -> int | None:
        return self.fields.get("prog_cur_line")

    @property
    def program_name(self) -> str | None:
        return self.fields.get("program_name")

    @property
    def fault_main(self) -> int | None:
        return self.fields.get("main_errcode")

    @property
    def fault_sub(self) -> int | None:
        return self.fields.get("sub_errcode")

    @property
    def robot_mode(self) -> int | None:
        return self.fields.get("robot_mode")

    @property
    def emergency_stop(self) -> int | None:
        return self.fields.get("emergency_stop")

    @property
    def ft_values(self) -> tuple[float, ...] | None:
        values = self.fields.get("ft_data")
        return tuple(values) if values else None

    @property
    def fz(self) -> float | None:
        """Native sensor value — negative under compression. Callers flip it."""
        values = self.fields.get("ft_data")
        return values[2] if values else None

    @property
    def tcp_z(self) -> float | None:
        pose = self.fields.get("tl_cur_pos")
        return pose[2] if pose else None

    @property
    def ft_active(self) -> bool | None:
        value = self.fields.get("ft_act_status")
        return None if value is None else bool(value)

    def di(self, index: int) -> int | None:
        """Control-box digital input level, or None if not in this frame.

        Observe-only. This is a display value; it does not establish a safety
        interlock and must not be used as one.
        """
        return self._bit("cl_dgt_input_l", "cl_dgt_input_h", index)

    def do(self, index: int) -> int | None:
        """Control-box digital output level, or None if not in this frame."""
        return self._bit("cl_dgt_output_l", "cl_dgt_output_h", index)

    def _bit(self, low_name: str, high_name: str, index: int) -> int | None:
        if not 0 <= index <= 15:
            raise ValueError(f"digital channel out of range: {index}")
        name = low_name if index < 8 else high_name
        packed = self.fields.get(name)
        if packed is None:
            return None
        return (packed >> (index % 8)) & 1


@dataclass
class FeedStats:
    state: str = FeedState.STOPPED.value
    ip: str | None = None
    port: int = STATUS_PORT
    generation: int = 0
    connects: int = 0
    last_error: str | None = None
    last_error_ts: float | None = None
    connected_since: float | None = None
    frames: int = 0
    data_len: int | None = None
    little_endian: bool = True
    frame_rate: float | None = None
    reader: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["expected_data_len"] = f8.EXPECTED_DATA_LEN
        return out


class StatusFeed:
    """Reads port 8083 on a dedicated thread and publishes the latest frame."""

    def __init__(
        self,
        port: int = STATUS_PORT,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        recv_timeout_s: float = RECV_TIMEOUT_S,
    ) -> None:
        self._port = port
        self._connect_timeout = connect_timeout_s
        self._recv_timeout = recv_timeout_s

        self._lock = threading.Lock()
        self._snapshot = FeedSnapshot()
        self._ip: str | None = None
        self._gen = 0
        self._state = FeedState.STOPPED
        self._last_error: str | None = None
        self._last_error_ts: float | None = None
        self._connects = 0
        self._connected_since: float | None = None
        self._frames = 0
        self._data_len: int | None = None
        self._little_endian = True
        self._reader_stats: dict[str, int] = {}
        self._layout_logged = False

        # Frame-rate estimate over a sliding window, for the diagnostics panel.
        self._rate_window_start: float | None = None
        self._rate_window_frames = 0
        self._frame_rate: float | None = None

        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self, ip: str) -> None:
        """Begin (or retarget to) streaming from `ip`. Idempotent, non-blocking."""
        to_start: threading.Thread | None = None
        with self._lock:
            changed = ip != self._ip
            self._ip = ip
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._run, name="robot-feed", daemon=True
                )
                to_start = self._thread
        if changed:
            # Drop the old socket so a blocked recv notices the new target
            # without waiting out its timeout.
            self._drop_socket()
        if to_start is not None:
            to_start.start()
        self._wake.set()

    def retarget(self, ip: str) -> None:
        self.start(ip)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop streaming and close the socket. Idempotent."""
        with self._lock:
            thread = self._thread
            self._thread = None
        self._stop.set()
        self._wake.set()
        self._drop_socket()
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._state = FeedState.STOPPED
            self._connected_since = None
            self._snapshot = FeedSnapshot()

    # ---------------------------------------------------------------- reads

    def snapshot(self) -> FeedSnapshot:
        with self._lock:
            return self._snapshot

    def stats(self) -> FeedStats:
        with self._lock:
            return FeedStats(
                state=self._state.value,
                ip=self._ip,
                port=self._port,
                generation=self._gen,
                connects=self._connects,
                last_error=self._last_error,
                last_error_ts=self._last_error_ts,
                connected_since=self._connected_since,
                frames=self._frames,
                data_len=self._data_len,
                little_endian=self._little_endian,
                frame_rate=self._frame_rate,
                reader=dict(self._reader_stats),
            )

    def is_streaming(self) -> bool:
        with self._lock:
            return self._state is FeedState.STREAMING

    # ----------------------------------------------------------------- loop

    def _run(self) -> None:
        backoff = BACKOFF_START_S
        while not self._stop.is_set():
            with self._lock:
                ip = self._ip
            if not ip:
                self._wake.wait(0.5)
                self._wake.clear()
                continue

            try:
                streamed = self._session(ip)
            except Exception as exc:  # noqa: BLE001 - a feed must never die
                self._note_error(f"feed error: {exc}")
                streamed = False

            if self._stop.is_set():
                break
            # A session that actually delivered frames earned a fresh backoff;
            # a connection that failed immediately should keep stepping back.
            backoff = BACKOFF_START_S if streamed else min(backoff * 2, BACKOFF_MAX_S)
            delay = max(0.1, backoff + backoff * random.uniform(-0.2, 0.2))
            self._wake.wait(delay)
            self._wake.clear()

        self._drop_socket()

    def _session(self, ip: str) -> bool:
        """One connect-and-read cycle. Returns True if any frame was published."""
        self._set_state(FeedState.CONNECTING)
        try:
            sock = socket.create_connection((ip, self._port), timeout=self._connect_timeout)
        except OSError as exc:
            self._note_error(f"connect to {ip}:{self._port} failed: {exc}")
            return False

        sock.settimeout(self._recv_timeout)
        with self._lock:
            self._sock = sock
            self._gen += 1
            self._connects += 1
            generation = self._gen
            self._connected_since = time.time()
            self._rate_window_start = time.monotonic()
            self._rate_window_frames = 0

        reader = f8.FrameReader()
        endian_checked = False
        little_endian = True
        published = False

        try:
            while not self._stop.is_set():
                with self._lock:
                    if self._ip != ip or self._sock is not sock:
                        return published  # retargeted or dropped underneath us
                try:
                    chunk = sock.recv(RECV_SIZE)
                except socket.timeout:
                    continue
                except OSError as exc:
                    self._note_error(f"feed read failed: {exc}")
                    return published

                if not chunk:
                    self._note_error("controller closed the status feed")
                    return published

                for parsed in reader.feed(chunk):
                    if not endian_checked:
                        resolved = self._resolve_endianness(parsed)
                        endian_checked = True
                        if resolved is None:
                            return published
                        little_endian = resolved
                    elif parsed.get("_little_endian") != little_endian:
                        # The framing layer changed its mind mid-stream, which a
                        # real controller never does. Re-prove rather than trust.
                        parsed = self._reparse(parsed, little_endian)
                        if parsed is None:
                            continue
                    self._publish(parsed, generation, little_endian, reader)
                    published = True
        finally:
            with self._lock:
                if self._sock is sock:
                    self._sock = None
            self._close(sock)
        return published

    def _resolve_endianness(self, parsed: dict[str, Any]) -> bool | None:
        """Prove the layout on the first frame of a connection.

        `FrameReader` has already worked out the byte order of the *framing*
        (LEN and CHECKSUM) by finding which reading makes them agree. This
        second check confirms the same order also makes sense of the *payload*,
        and covers the pathological case where it does not.

        Returns True for little-endian, False for big-endian, or None when the
        frame decodes sanely neither way — in which case the caller abandons the
        session rather than publishing values it cannot vouch for.
        """
        framing = bool(parsed.get("_little_endian", True))
        if f8.looks_sane(parsed):
            if not framing:
                log.warning(
                    "port-8083 frames decode big-endian, not little — the vendor "
                    "manual does not state byte order; treating this connection "
                    "as big-endian"
                )
            with self._lock:
                self._little_endian = framing
            return framing

        raw = parsed.get("_raw")
        if raw is not None and f8.looks_sane(f8.parse(raw, little_endian=not framing)):
            log.warning(
                "port-8083 framing and payload disagree on byte order; using %s "
                "for the payload",
                "little-endian" if not framing else "big-endian",
            )
            with self._lock:
                self._little_endian = not framing
            return not framing

        self._set_state(FeedState.LAYOUT_ERROR)
        self._note_error(
            f"frame did not decode sanely in either byte order "
            f"(data_len={parsed.get('_data_len')}, expected {f8.EXPECTED_DATA_LEN})"
        )
        if not self._layout_logged:
            self._layout_logged = True
            log.error(
                "port-8083 layout check failed: data_len=%s expected=%s. "
                "Refusing to publish. Check the controller firmware revision "
                "against docs/collabrative_robot_8083_port_status.md.",
                parsed.get("_data_len"),
                f8.EXPECTED_DATA_LEN,
            )
        return None

    @staticmethod
    def _reparse(parsed: dict[str, Any], little_endian: bool) -> dict[str, Any] | None:
        raw = parsed.get("_raw")
        if raw is None:
            return None
        out = f8.parse(raw, little_endian=little_endian)
        out["_cnt"] = parsed.get("_cnt")
        out["_data_len"] = parsed.get("_data_len")
        out["_little_endian"] = little_endian
        return out

    def _publish(
        self,
        parsed: dict[str, Any],
        generation: int,
        little_endian: bool,
        reader: f8.FrameReader,
    ) -> None:
        fields = {k: v for k, v in parsed.items() if not k.startswith("_")}
        now = time.monotonic()
        with self._lock:
            if self._gen != generation:
                return  # a newer session already owns the feed
            self._snapshot = FeedSnapshot(
                fields=MappingProxyType(fields),
                received_monotonic=now,
                received_ts=time.time(),
                generation=generation,
                data_len=parsed.get("_data_len"),
                little_endian=little_endian,
            )
            self._state = FeedState.STREAMING
            self._frames += 1
            self._data_len = parsed.get("_data_len")
            self._reader_stats = reader.stats.as_dict()

            self._rate_window_frames += 1
            if self._rate_window_start is not None:
                elapsed = now - self._rate_window_start
                if elapsed >= 1.0:
                    self._frame_rate = self._rate_window_frames / elapsed
                    self._rate_window_start = now
                    self._rate_window_frames = 0

    # --------------------------------------------------------------- helpers

    def _set_state(self, state: FeedState) -> None:
        with self._lock:
            self._state = state

    def _note_error(self, reason: str) -> None:
        with self._lock:
            self._last_error = reason
            self._last_error_ts = time.time()
            if self._state is not FeedState.LAYOUT_ERROR:
                self._state = FeedState.ERROR
            self._connected_since = None
        log.debug("status feed: %s", reason)

    def _drop_socket(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            self._close(sock)

    @staticmethod
    def _close(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
