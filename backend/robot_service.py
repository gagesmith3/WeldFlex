from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


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
    from fairino import Robot
except ImportError as exc:
    raise ImportError(
        "Cannot import FAIRINO SDK. Set WELDFLEX_FAIRINO_PATH to the SDK platform folder."
    ) from exc

SDK_TIMEOUT_S = 5.0

STATE_MAP = {
    0: "stopped",
    1: "stopped",
    2: "running",
    3: "paused",
}


class WeldFlexRobotService:
    def __init__(self, robot_ip: str) -> None:
        self.robot_ip = robot_ip
        self._robot: Any | None = None
        self._lock = threading.RLock()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="robot-sdk"
        )

    def _client(self) -> Any:
        """Return the live SDK connection, creating it if needed."""
        if self._robot is None:
            self._robot = Robot.RPC(self.robot_ip)
        return self._robot

    def _call(self, fn: Callable[[], Any], timeout: float = SDK_TIMEOUT_S) -> Any:
        """Run an SDK call in the executor thread with a hard timeout."""
        future = self._executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                self._robot = None
            raise RuntimeError(
                f"Robot did not respond within {int(timeout)}s — connection may be lost."
            )

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

    def run_program(self, program_name: str) -> None:
        with self._lock:
            robot = self._client()
        self._call(lambda: robot.Mode(0))
        time.sleep(2)
        self._call(lambda: robot.ProgramLoad(f"/fruser/{program_name}"))
        self._call(lambda: robot.ProgramRun())

    def status(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()

        state_resp, line_resp = self._call(
            lambda: (robot.GetProgramState(), robot.GetCurrentLine())
        )

        state_err, state_raw = self._unpack(state_resp)
        line_err, _ = self._unpack(line_resp)

        # -4 means SDK is_connect=False (CNDE handshake failed). Reset so the
        # next poll triggers a fresh Robot.RPC() call.
        if state_err == -4 and line_err == -4:
            with self._lock:
                self._robot = None

        connected = (state_err == 0) or (line_err == 0)

        return {
            "connected": connected,
            "program_state": STATE_MAP.get(state_raw, "unknown"),
            "program_state_raw": state_raw,
        }
