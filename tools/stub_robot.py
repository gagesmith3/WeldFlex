"""Fake FR-16 controller: an XML-RPC server on port 20003 that can be made to hang.

Lets the connection layer be exercised — timeouts, watchdog, reconnect, backoff —
without a robot on the bench, and reproduces the one failure mode real hardware can't
be made to produce on demand: a socket that accepts the connection and then never
answers.

    python tools/stub_robot.py                  # serve on 127.0.0.1:20003
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
"""

from __future__ import annotations

import argparse
import socketserver
import sys
import threading
import time
from xmlrpc.client import ServerProxy
from xmlrpc.server import SimpleXMLRPCServer

HOST = "127.0.0.1"
PORT = 20003
HANG_SECONDS = 600.0


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

        Each cycle walks the body, dwells on the boundary marker (which the manager
        polls several times), then jumps back to the loop head. The dwell repeats
        are what prove the detector does not double-count a held line.
        """
        loop_start, marker, cycles = int(loop_start), int(marker), int(cycles)
        body = list(range(loop_start, marker))
        script: list[int] = []
        for _ in range(cycles):
            script += body + [marker] * 4 + [marker + 1, marker + 2]
        return self._ctl_lines(script)

    def _ctl_stats(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "hang_next": self.hang_next,
                    "hang_always": self.hang_always, "program_state": self.program_state,
                    "line_step": self.line_step, "line_script_len": len(self.line_script)}

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
    print(f"stub FR-16 controller on http://{HOST}:{PORT}  (ctrl-c to stop)", flush=True)
    # Threaded dispatch so a hung call doesn't also block the control calls.
    server.serve_forever()


def control(args: argparse.Namespace) -> int:
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
        else:
            print(proxy._ctl_stats())
    except OSError as exc:
        print(f"no stub running on {HOST}:{PORT} ({exc})", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hang", action="store_true", help="arm a one-shot hang")
    p.add_argument("--hang-forever", action="store_true", help="hang on every call")
    p.add_argument("--normal", action="store_true", help="clear hangs")
    p.add_argument("--state", type=int, help="set reported program state")
    p.add_argument("--stats", action="store_true", help="print call stats")
    p.add_argument("--cycle", help="script a loop feed: LOOP_START:MARKER:CYCLES")
    p.add_argument("--lines", help="script an explicit line sequence, comma separated")
    args = p.parse_args()

    if any([args.hang, args.hang_forever, args.normal, args.stats,
            args.state is not None, args.cycle is not None, args.lines is not None]):
        return control(args)
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
