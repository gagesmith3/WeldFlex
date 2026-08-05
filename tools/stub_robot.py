"""Fake FR-16 controller: XML-RPC on port 20003 plus the status feed on 8083.

Lets the connection layer be exercised — timeouts, watchdog, reconnect, backoff —
without a robot on the bench, and reproduces the one failure mode real hardware can't
be made to produce on demand: a socket that accepts the connection and then never
answers.

    python tools/stub_robot.py                  # serve on 127.0.0.1:20003 + :8083
    WELDFLEX_ROBOT_IP=127.0.0.1 python backend/app.py

Control it while running (from another shell):

    python tools/stub_robot.py --hang           # next call blocks for 600s
    python tools/stub_robot.py --hang-forever   # every call blocks
    python tools/stub_robot.py --normal         # back to healthy
    python tools/stub_robot.py --state 2        # report program state 2 (running)

Drive the job manager's cycle detector, which cannot be exercised without a
`GetCurrentLine` feed that actually wraps a loop:

    python tools/stub_robot.py --cycle 31:40:3  # loop_start:marker:cycles
    python tools/stub_robot.py --lines 31,35,40 # replay an explicit sequence
    python tools/stub_robot.py --lines ''       # back to the free-running counter

Each GetCurrentLine consumes one step; the last value repeats once the script is
exhausted, the way a stopped program holds its final line. The line numbers to
use are logged by the manager at launch ("loop_start=.. marker=..").

Only the methods the app actually calls over XML-RPC are implemented. Anything else
returns [0] so an unexpected call fails loudly in the app rather than here.

## The port-8083 status feed

Pushes a status frame every 100 ms to every connected client, mirroring the same
`program_state` / `current_line` / fault codes the XML-RPC side reports so the two
can be compared on the diagnostics page. Each knob below exists because the
decoder has to survive that exact case on real hardware:

    --feed-off / --feed-on          stop and restart the stream (tests staleness)
    --feed-di 3                     set the DI 7-0 bitmask (bit0=DI0 ready, bit1=DI1 stud)
    --feed-fz -88.75                set the reported Fz in newtons, native sign
    --feed-endian big               emit big-endian frames (the manual never states which)
    --feed-len 300                  truncate DATA, as older firmware would
    --feed-corrupt 3                corrupt the next 3 frames' checksums
    --feed-garbage 17               inject 17 junk bytes before the next frame
    --feed-cycles 800               advance the DO4-7 cycle counter every 800 ms

`--feed-cycles` is what drives the DO-counter cycle tracker: it walks bits 4-7 of
the DO word 0->1->..->15->0, exactly as the generated Lua does at its cycle
boundary. Set 0 to stop.
"""

from __future__ import annotations

import argparse
import random
import socket
import socketserver
import struct
import sys
import threading
import time
from pathlib import Path
from xmlrpc.client import ServerProxy
from xmlrpc.server import SimpleXMLRPCServer

# The backend modules import each other flat, the same way conftest.py sets up.
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import frame_8083 as f8  # noqa: E402

HOST = "127.0.0.1"
PORT = 20003
STATUS_PORT = 8083
FEED_PERIOD_S = 0.1
HANG_SECONDS = 600.0

# Bits 4-7 of the control-box DO word, matching the generated Lua's counter.
CYCLE_DO_SHIFT = 4
CYCLE_DO_MASK = 0x0F


class StubController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hang_next = False
        self.hang_always = False
        self.program_state = 1
        self.current_line = 0
        self.calls = 0
        self.line_script: list[int] = []
        self.line_step = 0

        # --- port-8083 status feed state ---
        self.feed_on = True
        self.feed_di = 0
        self.feed_do = 0
        self.feed_fz = 0.0
        self.feed_little_endian = True
        self.feed_data_len: int | None = None   # None = full 650-byte layout
        self.feed_corrupt = 0                   # frames left to corrupt
        self.feed_garbage = 0                   # junk bytes to inject next frame
        self.feed_cycle_ms = 0                  # 0 = counter held
        self.feed_cycle_count = 0
        self.feed_frames = 0
        self._feed_next_cycle_ts = 0.0

    # --- test control (not part of the real controller's API) ---

    def _ctl_hang(self, forever: bool = False) -> str:
        with self._lock:
            if forever:
                self.hang_always = True
            else:
                self.hang_next = True
        return "hang armed (forever)" if forever else "hang armed (one call)"

    def _ctl_normal(self) -> str:
        with self._lock:
            self.hang_next = self.hang_always = False
        return "normal"

    def _ctl_state(self, state: int) -> str:
        with self._lock:
            self.program_state = int(state)
        return f"program_state={state}"

    def _ctl_lines(self, lines: list) -> str:
        with self._lock:
            self.line_script = [int(v) for v in lines]
            self.line_step = 0
        return f"line script: {len(self.line_script)} step(s)"

    def _ctl_cycle(self, loop_start: int, marker: int, cycles: int) -> str:
        """Build the line feed a real `for cycleIndex = 1, cycleCount` loop produces.

        Each cycle walks the body twice (a two-stud part, so body lines are
        non-monotonic *within* a cycle, as they really are), dwells on the boundary
        marker — which the manager polls several times — then wraps. The dwell
        repeats are what prove the detector does not double-count a held line.

        `loop_start` itself is deliberately never emitted. That is the `for
        cycleIndex` statement, and a 250 ms sampler does not land on it; the real
        controller has never once reported it. A stub that emitted it made a
        detector keyed on the loop head look correct while hardware gated after
        cycle 1 and then ran free (2026-07-28). Keep this feed honest.
        """
        loop_start, marker, cycles = int(loop_start), int(marker), int(cycles)
        body = list(range(loop_start + 1, marker))
        script: list[int] = []
        for _ in range(cycles):
            script += body + body + [marker] * 4 + [marker + 1, marker + 2]
        return self._ctl_lines(script)

    def _ctl_stats(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "hang_next": self.hang_next,
                    "hang_always": self.hang_always, "program_state": self.program_state,
                    "line_step": self.line_step, "line_script_len": len(self.line_script),
                    "feed_on": self.feed_on, "feed_frames": self.feed_frames,
                    "feed_di": self.feed_di, "feed_do": self.feed_do,
                    "feed_cycle_count": self.feed_cycle_count,
                    "feed_endian": "little" if self.feed_little_endian else "big"}

    def _ctl_feed(self, setting: str, value: str) -> str:
        """One entry point for every feed knob, so the CLI stays a thin shell."""
        with self._lock:
            if setting == "on":
                self.feed_on = True
            elif setting == "off":
                self.feed_on = False
            elif setting == "di":
                self.feed_di = int(value) & 0xFF
            elif setting == "do":
                self.feed_do = int(value) & 0xFF
            elif setting == "fz":
                self.feed_fz = float(value)
            elif setting == "endian":
                self.feed_little_endian = value != "big"
            elif setting == "len":
                length = int(value)
                self.feed_data_len = None if length <= 0 else length
            elif setting == "corrupt":
                self.feed_corrupt = int(value)
            elif setting == "garbage":
                self.feed_garbage = int(value)
            elif setting == "cycles":
                self.feed_cycle_ms = max(0, int(value))
                self._feed_next_cycle_ts = time.monotonic() + self.feed_cycle_ms / 1000.0
            else:
                return f"unknown feed setting: {setting}"
            return f"feed {setting}={value}"

    # --- port-8083 frame construction ---

    def _feed_bytes(self) -> bytes | None:
        """The next frame's wire bytes, or None while the feed is stopped."""
        with self._lock:
            if not self.feed_on:
                return None

            # Advance the DO cycle counter on its own schedule, the way the
            # generated Lua bumps it once per cycle boundary.
            if self.feed_cycle_ms:
                now = time.monotonic()
                if now >= self._feed_next_cycle_ts:
                    self.feed_cycle_count += 1
                    self._feed_next_cycle_ts = now + self.feed_cycle_ms / 1000.0
            counter = self.feed_cycle_count & CYCLE_DO_MASK
            do_word = (self.feed_do & ~(CYCLE_DO_MASK << CYCLE_DO_SHIFT)) | (
                counter << CYCLE_DO_SHIFT
            )

            values = {
                "program_state": self.program_state,
                "robot_mode": 0,
                "error_code": 0,
                "jt_cur_pos": [0.0, -30.0, 90.0, 0.0, 60.0, 0.0],
                "tl_cur_pos": [420.0, 0.0, 315.5, 180.0, 0.0, 0.0],
                "prog_cur_line": self.current_line & 0xFF,
                "prog_total_line": 113,
                "program_name": "WeldFlex",
                "cl_dgt_input_l": self.feed_di,
                "cl_dgt_output_l": do_word,
                "ft_data": [0.0, 0.0, self.feed_fz, 0.0, 0.0, 0.0],
                "ft_act_status": 1,
                "emergency_stop": 0,
                "main_errcode": 0,
                "sub_errcode": 0,
            }
            little = self.feed_little_endian
            data_len = self.feed_data_len
            corrupt = self.feed_corrupt > 0
            if corrupt:
                self.feed_corrupt -= 1
            garbage = self.feed_garbage
            self.feed_garbage = 0
            self.feed_frames += 1
            cnt = self.feed_frames & 0xFF

        data = f8.pack(values, little_endian=little)
        if data_len is not None:
            data = data[:data_len]
        frame = bytearray(f8.build_frame(data, cnt=cnt, little_endian=little))
        if corrupt:
            frame[-1] ^= 0xFF
        if garbage:
            return bytes(random.randbytes(garbage)) + bytes(frame)
        return bytes(frame)

    # --- controller API ---

    def _maybe_hang(self, method: str) -> None:
        with self._lock:
            self.calls += 1
            hang = self.hang_always or self.hang_next
            self.hang_next = False
        if hang:
            print(f"  [stub] hanging {HANG_SECONDS}s in {method}", flush=True)
            time.sleep(HANG_SECONDS)

    def GetControllerIP(self):
        self._maybe_hang("GetControllerIP")
        return [0, HOST]

    def GetProgramState(self):
        self._maybe_hang("GetProgramState")
        with self._lock:
            return [0, self.program_state]

    def GetCurrentLine(self):
        self._maybe_hang("GetCurrentLine")
        with self._lock:
            if self.line_script:
                # Hold the last value once exhausted, as a stopped program does.
                idx = min(self.line_step, len(self.line_script) - 1)
                self.current_line = self.line_script[idx]
                self.line_step += 1
            else:
                self.current_line += 1
            return [0, self.current_line]

    def GetRobotErrorCode(self):
        self._maybe_hang("GetRobotErrorCode")
        return [0, [0, 0]]

    def _dispatch(self, method, params):
        handler = getattr(self, method, None)
        if handler is None:
            print(f"  [stub] unimplemented: {method}{params}", flush=True)
            return [0]
        return handler(*params)


class _ThreadedXMLRPCServer(socketserver.ThreadingMixIn, SimpleXMLRPCServer):
    """Threaded so an armed hang blocks only its own call, not the --normal that clears it."""

    daemon_threads = True
    allow_reuse_address = True


class StatusFeedServer:
    """Pushes status frames to every client connected on port 8083.

    One thread per client, matching the real controller's behaviour of feeding
    each connection independently. Frames keep flowing while an XML-RPC call is
    hung, which is the whole point of moving telemetry off that channel.
    """

    def __init__(self, stub: StubController, host: str = HOST, port: int = STATUS_PORT,
                 period_s: float = FEED_PERIOD_S) -> None:
        self._stub = stub
        self._host = host
        self._port = port
        self._period = period_s
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen(8)
        self._sock = sock
        threading.Thread(target=self._accept_loop, name="stub-feed-accept",
                         daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    @property
    def port(self) -> int:
        """The bound port — resolves to a real one when constructed with port 0."""
        return self._sock.getsockname()[1] if self._sock is not None else self._port

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                return
            print(f"  [stub] status feed client {addr[0]}:{addr[1]}", flush=True)
            threading.Thread(target=self._serve, args=(conn,),
                             name="stub-feed-client", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            while not self._stop.is_set():
                payload = self._stub._feed_bytes()
                if payload is not None:
                    try:
                        conn.sendall(payload)
                    except OSError:
                        return
                time.sleep(self._period)


def serve() -> None:
    # allow_none so responses can carry nulls; logRequests off to keep the hang visible.
    server = _ThreadedXMLRPCServer((HOST, PORT), allow_none=True, logRequests=False)
    stub = StubController()
    server.register_instance(stub, allow_dotted_names=False)
    server.register_function(lambda forever=False: stub._ctl_hang(forever), "_ctl_hang")
    server.register_function(stub._ctl_normal, "_ctl_normal")
    server.register_function(stub._ctl_state, "_ctl_state")
    server.register_function(stub._ctl_stats, "_ctl_stats")
    server.register_function(stub._ctl_lines, "_ctl_lines")
    server.register_function(stub._ctl_cycle, "_ctl_cycle")
    server.register_function(stub._ctl_feed, "_ctl_feed")

    feed = StatusFeedServer(stub)
    feed.start()
    print(f"stub FR-16 controller on http://{HOST}:{PORT}  (ctrl-c to stop)", flush=True)
    print(f"stub status feed on tcp://{HOST}:{STATUS_PORT} every "
          f"{int(FEED_PERIOD_S * 1000)}ms", flush=True)
    # Threaded dispatch so a hung call doesn't also block the control calls.
    try:
        server.serve_forever()
    finally:
        feed.stop()


def control(args: argparse.Namespace, feed_setting: tuple[str, str] | None = None) -> int:
    proxy = ServerProxy(f"http://{HOST}:{PORT}", allow_none=True)
    try:
        if args.hang:
            print(proxy._ctl_hang(False))
        elif args.hang_forever:
            print(proxy._ctl_hang(True))
        elif args.normal:
            print(proxy._ctl_normal())
        elif args.state is not None:
            print(proxy._ctl_state(args.state))
        elif args.cycle is not None:
            loop_start, marker, cycles = (int(v) for v in args.cycle.split(":"))
            print(proxy._ctl_cycle(loop_start, marker, cycles))
        elif args.lines is not None:
            print(proxy._ctl_lines([int(v) for v in args.lines.split(",") if v.strip()]))
        elif feed_setting is not None:
            print(proxy._ctl_feed(*feed_setting))
        else:
            print(proxy._ctl_stats())
    except OSError as exc:
        print(f"no stub running on {HOST}:{PORT} ({exc})", file=sys.stderr)
        return 1
    return 0


def _feed_setting(args: argparse.Namespace) -> tuple[str, str] | None:
    """Map whichever --feed-* flag was given onto (setting, value)."""
    if args.feed_on:
        return ("on", "")
    if args.feed_off:
        return ("off", "")
    for name in ("di", "do", "fz", "endian", "len", "corrupt", "garbage", "cycles"):
        value = getattr(args, f"feed_{name}")
        if value is not None:
            return (name, str(value))
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hang", action="store_true", help="arm a one-shot hang")
    p.add_argument("--hang-forever", action="store_true", help="hang on every call")
    p.add_argument("--normal", action="store_true", help="clear hangs")
    p.add_argument("--state", type=int, help="set reported program state")
    p.add_argument("--stats", action="store_true", help="print call stats")
    p.add_argument("--cycle", help="script a loop feed: LOOP_START:MARKER:CYCLES")
    p.add_argument("--lines", help="script an explicit line sequence, comma separated")

    feed = p.add_argument_group("port-8083 status feed")
    feed.add_argument("--feed-on", action="store_true", help="resume the stream")
    feed.add_argument("--feed-off", action="store_true", help="stop sending frames")
    feed.add_argument("--feed-di", type=int, help="DI 7-0 bitmask")
    feed.add_argument("--feed-do", type=int, help="DO 7-0 bitmask (bits 4-7 are the counter)")
    feed.add_argument("--feed-fz", type=float, help="reported Fz in N, native sign")
    feed.add_argument("--feed-endian", choices=("little", "big"), help="frame byte order")
    feed.add_argument("--feed-len", type=int, help="truncate DATA to N bytes (0 = full)")
    feed.add_argument("--feed-corrupt", type=int, help="corrupt the next N checksums")
    feed.add_argument("--feed-garbage", type=int, help="inject N junk bytes before next frame")
    feed.add_argument("--feed-cycles", type=int, help="advance the DO counter every N ms (0 = off)")

    args = p.parse_args()
    feed_setting = _feed_setting(args)

    if any([args.hang, args.hang_forever, args.normal, args.stats,
            args.state is not None, args.cycle is not None, args.lines is not None,
            feed_setting is not None]):
        return control(args, feed_setting)
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
