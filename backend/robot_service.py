from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from ftplib import FTP
from typing import Any

def _bootstrap_local_fairino() -> None:
    """Add local FAIRINO SDK directories to sys.path for offline use."""
    workspace_root = Path(__file__).resolve().parents[1]
    sdk_root = workspace_root / "fairino-python-sdk-main"

    candidates = [
        os.getenv("WELDFLEX_FAIRINO_PATH"),
        str(sdk_root / "windows"),
        str(sdk_root / "linux"),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        fairino_pkg = path / "fairino"
        if fairino_pkg.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


_bootstrap_local_fairino()

try:
    from fairino import Robot
except ImportError as exc:
    raise ImportError(
        "Unable to import FAIRINO SDK. Set WELDFLEX_FAIRINO_PATH to the SDK platform folder, "
        "for example .../fairino-python-sdk-main/windows"
    ) from exc

try:
    from .lua_builder import build_studs_data_lua
except ImportError:
    from lua_builder import build_studs_data_lua


STATE_MAP = {
    1: "stopped",
    2: "running",
    3: "paused",
}


class WeldFlexRobotService:
    def __init__(
        self,
        robot_ip: str,
        controller_host: str | None = None,
        studs_data_path: str = "/fruser/studs_data.lua",
    ) -> None:
        self.robot_ip = robot_ip
        self.controller_host = controller_host or robot_ip
        self.studs_data_path = studs_data_path
        self._robot: Any | None = None
        self._lock = threading.Lock()

    def reconfigure(
        self,
        robot_ip: str | None = None,
        controller_host: str | None = None,
        studs_data_path: str | None = None,
    ) -> None:
        with self._lock:
            if robot_ip and robot_ip != self.robot_ip:
                self.robot_ip = robot_ip
                # Force a fresh RPC client when IP changes.
                self._robot = None

            if controller_host:
                self.controller_host = controller_host
            elif robot_ip and not controller_host:
                self.controller_host = robot_ip

            if studs_data_path:
                self.studs_data_path = studs_data_path

    def _client(self) -> Any:
        if self._robot is None:
            self._robot = Robot.RPC(self.robot_ip)
        return self._robot

    def _upload_with_sdk(self, local_file: str, remote_file: str) -> bool:
        robot = self._client()

        lua_upload = getattr(robot, "LuaUpload", None)
        if lua_upload is not None:
            result = lua_upload(local_file)
            if self._is_success_result(result):
                return True

        method_names = [
            "FileUpload",
            "UploadFile",
            "ProgramUpload",
            "UploadProgram",
        ]

        for name in method_names:
            method = getattr(robot, name, None)
            if method is None:
                continue

            try:
                result = method(local_file, remote_file)
            except TypeError:
                continue

            if self._is_success_result(result):
                return True

        return False

    def _upload_with_ftp(self, local_file: str, remote_file: str) -> None:
        user = os.getenv("WELDFLEX_FTP_USER", "anonymous")
        password = os.getenv("WELDFLEX_FTP_PASS", "")
        remote_dir, remote_name = remote_file.rsplit("/", 1)

        with FTP(self.controller_host, timeout=8) as ftp:
            ftp.login(user=user, passwd=password)
            ftp.cwd(remote_dir)
            with open(local_file, "rb") as fp:
                ftp.storbinary(f"STOR {remote_name}", fp)

    def upload_lua(self, lua_text: str, remote_file: str) -> None:
        remote_name = remote_file.rsplit("/", 1)[1]
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = os.path.join(tmp_dir, remote_name)
            with open(tmp_path, "w", encoding="utf-8") as tmp:
                tmp.write(lua_text)

            if self._upload_with_sdk(tmp_path, remote_file):
                return
            self._upload_with_ftp(tmp_path, remote_file)

    def upload_load_run(self, studs: list[dict[str, float]], remote_file: str) -> dict[str, Any]:
        studs_data_lua = build_studs_data_lua(studs)

        with self._lock:
            self.upload_lua(studs_data_lua, self.studs_data_path)
            robot = self._client()
            try:
                load_result = robot.ProgramLoad(program_name=remote_file)
            except TypeError:
                load_result = robot.ProgramLoad(remote_file)
            mode_result = robot.Mode(0)
            run_result = robot.ProgramRun()

            # Capture immediate post-run state for troubleshooting when run command succeeds but no motion occurs.
            time.sleep(0.2)
            program_state_resp = robot.GetProgramState()
            current_line_resp = robot.GetCurrentLine()
            state_error, program_state = self._split_error_value(program_state_resp)
            line_error, current_line = self._split_error_value(current_line_resp)
            loaded_program = self._get_loaded_program(robot)

        return {
            "program_path": remote_file,
            "studs_data_path": self.studs_data_path,
            "load_result": load_result,
            "mode_result": mode_result,
            "run_result": run_result,
            "loaded_program": loaded_program,
            "post_run_status": {
                "connected": state_error == 0 and line_error == 0,
                "program_state": STATE_MAP.get(program_state, "unknown"),
                "program_state_raw": program_state,
                "program_state_error": state_error,
                "current_line": current_line,
                "current_line_error": line_error,
                "program_state_response": program_state_resp,
                "current_line_response": current_line_resp,
            },
        }

    def pause(self) -> dict[str, Any]:
        with self._lock:
            pause_result = self._client().ProgramPause()
        return {"pause_result": pause_result}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            stop_result = self._client().ProgramStop()
        return {"stop_result": stop_result}

    def status(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
            program_state_resp = robot.GetProgramState()
            current_line_resp = robot.GetCurrentLine()

        state_error, program_state = self._split_error_value(program_state_resp)
        line_error, current_line = self._split_error_value(current_line_resp)
        connected = state_error == 0 and line_error == 0

        return {
            "connected": connected,
            "program_state_error": state_error,
            "current_line_error": line_error,
            "program_state_response": program_state_resp,
            "current_line_response": current_line_resp,
            "program_state_raw": program_state,
            "program_state": STATE_MAP.get(program_state, "unknown"),
            "current_line": current_line,
        }

    @staticmethod
    def _split_error_value(response: Any) -> tuple[int, Any]:
        if isinstance(response, tuple):
            if len(response) >= 2:
                error_code = int(response[0]) if isinstance(response[0], (int, float)) else -1
                return error_code, response[1]
            if len(response) == 1:
                error_code = int(response[0]) if isinstance(response[0], (int, float)) else -1
                return error_code, None

        if isinstance(response, list):
            if len(response) >= 2:
                error_code = int(response[0]) if isinstance(response[0], (int, float)) else -1
                return error_code, response[1]
            if len(response) == 1:
                error_code = int(response[0]) if isinstance(response[0], (int, float)) else -1
                return error_code, None

        if isinstance(response, (int, float)):
            error_code = int(response)
            return error_code, None

        return -1, response

    @staticmethod
    def _is_success_result(result: Any) -> bool:
        if result in (None, True, 0):
            return True
        if isinstance(result, tuple) and len(result) > 0:
            return result[0] == 0
        if isinstance(result, list) and len(result) > 0:
            return result[0] == 0
        return False

    def _get_loaded_program(self, robot: Any) -> Any:
        getter = getattr(robot, "GetLoadedProgram", None)
        if getter is None:
            return "GetLoadedProgram not supported"

        try:
            result = getter()
        except Exception as exc:
            return f"GetLoadedProgram failed: {exc}"

        if isinstance(result, tuple):
            if len(result) >= 2 and result[0] == 0:
                return result[1]
            return {"raw": result}
        if isinstance(result, list):
            if len(result) >= 2 and result[0] == 0:
                return result[1]
            return {"raw": result}
        return result
