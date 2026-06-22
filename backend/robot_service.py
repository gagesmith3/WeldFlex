from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

# The FAIRINO SDK prints Chinese debug text to stdout/stderr. On Windows the
# default console codec (cp1252) can't encode it, which raises UnicodeEncodeError
# and gets mistaken for a connection failure. Force UTF-8 stdio regardless of how
# the process was launched.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
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

    def pause_program(self) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.ProgramPause())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ProgramPause failed (code {err_code})")

    def resume_program(self) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.ProgramResume())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ProgramResume failed (code {err_code})")

    def stop_program(self) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.ProgramStop())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ProgramStop failed (code {err_code})")

    def run_liberty(self, cycles: int, program_name: str = "libertytest.lua") -> None:
        """Load and run a Lua program already on the robot."""
        self.run_program(program_name)

    def upload_program(self, local_path: str) -> None:
        with self._lock:
            robot = self._client()
        error = self._call(lambda: robot.LuaUpload(local_path), timeout=30.0)
        err_code, _ = self._unpack(error)
        if err_code != 0:
            raise RuntimeError(f"LuaUpload failed with error code {err_code}")

    def run_program(self, program_name: str) -> None:
        with self._lock:
            robot = self._client()
        self._call(lambda: robot.Mode(0))
        time.sleep(2)
        self._call(lambda: robot.ProgramLoad(f"/fruser/{program_name}"))
        self._call(lambda: robot.ProgramRun())

    def ft_activate(self, state: int) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.FT_Activate(state))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_Activate failed (code {err_code})")

    def ft_zero(self) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.FT_SetZero(1))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_SetZero failed (code {err_code})")

    def ft_read(self) -> dict[str, float]:
        with self._lock:
            robot = self._client()
        resp = self._call(lambda: robot.FT_GetForceTorqueRCS())
        err_code, values = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"FT_GetForceTorqueRCS failed (code {err_code})")
        return {
            "fx": float(values[0]), "fy": float(values[1]), "fz": float(values[2]),
            "mx": float(values[3]), "my": float(values[4]), "mz": float(values[5]),
        }

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
