from __future__ import annotations

import concurrent.futures
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

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
        self._start_time = time.time()
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

    def upload_program(self, local_path: str) -> str:
        """Upload a Lua file to the robot. Returns the program name as stored on the robot."""
        path = Path(local_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Program file not found: {path}")
        with self._lock:
            robot = self._client()
        error = self._call(lambda: robot.LuaUpload(str(path)), timeout=30.0)
        err_code, _ = self._unpack(error)
        if err_code != 0:
            raise RuntimeError(f"LuaUpload failed (code {err_code}): {path.name}")
        return path.name

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

        with self._lock:
            robot = self._client()

        # Delete the old copy — ignore errors (file may not exist yet)
        self._call(lambda: robot.LuaDelete(filename))

        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, filename)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(lua_content)
            error = self._call(lambda: robot.LuaUpload(tmp_path), timeout=30.0)
            err_code, _ = self._unpack(error)
            if err_code != 0:
                raise RuntimeError(f"LuaUpload failed (code {err_code}): {filename}")
        finally:
            try:
                os.remove(tmp_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def run_program(self, program_name: str) -> None:
        """Run a Lua program already stored on the robot under /fruser/."""
        with self._lock:
            robot = self._client()
        self._call(lambda: robot.Mode(0))
        time.sleep(2)
        load_resp = self._call(lambda: robot.ProgramLoad(f"/fruser/{program_name}"))
        load_err, _ = self._unpack(load_resp)
        if load_err != 0:
            raise RuntimeError(f"ProgramLoad failed (code {load_err}): {program_name}")
        run_resp = self._call(lambda: robot.ProgramRun())
        run_err, _ = self._unpack(run_resp)
        if run_err != 0:
            raise RuntimeError(f"ProgramRun failed (code {run_err}): {program_name}")

    def upload_and_run(self, local_path: str) -> None:
        """Upload a Lua file then immediately run it."""
        program_name = self.upload_program(local_path)
        self.run_program(program_name)

    def tcp_enable_drag(self) -> None:
        """Enter drag teach mode so the operator can physically position the robot."""
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.DragTeachSwitch(1))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"DragTeachSwitch(1) failed (code {err_code})")

    def tcp_record_point(self, point_num: int) -> None:
        """Exit drag mode and record current pose as TCP reference point N (1-4)."""
        with self._lock:
            robot = self._client()
        def _seq():
            robot.DragTeachSwitch(0)
            return robot.SetTcp4RefPoint(point_num)
        err = self._call(_seq)
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"SetTcp4RefPoint({point_num}) failed (code {err_code})")

    def tcp_compute_and_apply(self, tool_id: int = 1) -> list:
        """Compute TCP from 4 recorded points and save to tool slot via SetToolCoord."""
        with self._lock:
            robot = self._client()
        resp = self._call(lambda: robot.ComputeTcp4(), timeout=10.0)
        err_code, tcp_pose = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"ComputeTcp4 failed (code {err_code})")
        apply_resp = self._call(lambda: robot.SetToolCoord(tool_id, tcp_pose, 0, 0, 0, 0))
        apply_code, _ = self._unpack(apply_resp)
        if apply_code != 0:
            raise RuntimeError(f"SetToolCoord failed (code {apply_code})")
        return list(tcp_pose)

    def ft_setup(self) -> None:
        """Full init sequence per SDK example: configure → reset → activate → zero.
        Takes ~10 s due to required waits between commands."""
        with self._lock:
            robot = self._client()

        def _sequence():
            robot.FT_SetConfig(24, 0)   # company 24 = XJC (鑫精诚), device 0
            time.sleep(1)
            robot.FT_Activate(0)        # reset first
            time.sleep(2)
            err = robot.FT_Activate(1)  # activate
            if isinstance(err, int) and err != 0:
                raise RuntimeError(f"FT_Activate(1) failed (code {err})")
            time.sleep(2)
            robot.FT_SetZero(0)         # clear old zero
            time.sleep(2)
            robot.FT_SetZero(1)         # apply new zero

        self._call(_sequence, timeout=30.0)

    def ft_deactivate(self) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.FT_Activate(0))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_Activate(0) failed (code {err_code})")

    def ft_zero(self) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.FT_SetZero(1))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_SetZero failed (code {err_code})")

    def ft_read(self) -> dict:
        with self._lock:
            robot = self._client()

        def _read():
            resp = robot.FT_GetForceTorqueRCS()
            active = bool(robot.robot_state_pkg.ft_sensor_active)
            return resp, active

        resp, active = self._call(_read)
        err_code, values = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"FT_GetForceTorqueRCS failed (code {err_code})")
        return {
            "fx": float(values[0]), "fy": float(values[1]), "fz": float(values[2]),
            "mx": float(values[3]), "my": float(values[4]), "mz": float(values[5]),
            "active": active,
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

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()

        def _gather():
            state_resp = robot.GetProgramState()
            line_resp = robot.GetCurrentLine()
            try:
                err_resp = robot.GetRobotErrCode()
            except Exception:
                err_resp = None
            return state_resp, line_resp, err_resp

        state_resp, line_resp, err_resp = self._call(_gather)

        state_err, state_raw = self._unpack(state_resp)
        line_err, line_val = self._unpack(line_resp)

        if state_err == -4 and line_err == -4:
            with self._lock:
                self._robot = None

        connected = (state_err == 0) or (line_err == 0)

        if err_resp is not None and isinstance(err_resp, (tuple, list)) and len(err_resp) >= 3:
            robot_error_error = int(err_resp[0])
            main_code = err_resp[1] if err_resp[1] != 0 else None
            sub_code = err_resp[2] if len(err_resp) > 2 and err_resp[2] != 0 else None
        else:
            robot_error_error = -1
            main_code = None
            sub_code = None

        return {
            "connected": connected,
            "program_state": STATE_MAP.get(state_raw, "unknown"),
            "program_state_error": state_err,
            "current_line": line_val,
            "current_line_error": line_err,
            "fault_codes": {
                "main_code": main_code,
                "sub_code": sub_code,
                "safety_code": None,
                "safety_error": 0,
                "robot_error_error": robot_error_error,
            },
        }

    def reset_errors(self) -> None:
        with self._lock:
            robot = self._client()
        err = self._call(lambda: robot.ResetAllError())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ResetAllError failed (code {err_code})")

    def reconnect(self) -> None:
        with self._lock:
            self._robot = None
        with self._lock:
            self._client()

    def uptime(self) -> str:
        elapsed = int(time.time() - self._start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
