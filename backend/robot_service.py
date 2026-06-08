from __future__ import annotations

import concurrent.futures
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from ftplib import FTP
from typing import Any, Callable

SDK_CALL_TIMEOUT_S = 5.0

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
        self._last_jog_safety_sensitivity: int | None = None
        self._last_jog_safety_monotonic = 0.0
        self._jog_safety_refresh_s = 0.75
        self._lock = threading.Lock()
        self._sdk_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="robot-sdk"
        )

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
                self._last_jog_safety_sensitivity = None
                self._last_jog_safety_monotonic = 0.0

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

    def _upload_static_lua_scripts(self) -> None:
        lua_dir = Path(__file__).resolve().parents[1] / "robot" / "lua"
        for script_name in ("studCycle.lua", "weld.lua"):
            script_path = lua_dir / script_name
            if script_path.exists():
                self.upload_lua(script_path.read_text(encoding="utf-8"), f"/fruser/{script_name}")

    def configure_safety(self, collision_sensitivity: int = 3) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
        level = [float(collision_sensitivity)] * 6
        anticollision = self._run_with_timeout(
            lambda: robot.SetAnticollision(mode=0, level=level, config=0)
        )
        strategy = self._run_with_timeout(
            lambda: robot.SetCollisionStrategy(strategy=0, safeTime=1000, safeDistance=100, safetyMargin=[10, 10, 10, 10, 10, 10])
        )
        static = self._run_with_timeout(
            lambda: robot.SetStaticCollisionOnOff(status=1)
        )
        return {
            "anticollision": anticollision,
            "strategy": strategy,
            "static_collision": static,
        }

    def _ensure_jog_safety(self, collision_sensitivity: int) -> None:
        sensitivity = max(1, min(5, int(collision_sensitivity)))
        now = time.monotonic()
        should_apply = (
            self._last_jog_safety_sensitivity != sensitivity
            or (now - self._last_jog_safety_monotonic) > self._jog_safety_refresh_s
        )
        if should_apply:
            self.configure_safety(sensitivity)
            self._last_jog_safety_sensitivity = sensitivity
            self._last_jog_safety_monotonic = now

    def upload_load_run(self, studs: list[dict[str, float]], remote_file: str, clearance_z_mm: float = 50.0, collision_sensitivity: int = 3) -> dict[str, Any]:
        studs_data_lua = build_studs_data_lua(studs, clearance_z_mm)

        with self._lock:
            robot = self._client()
            robot.SetAnticollision(mode=0, level=[float(collision_sensitivity)] * 6, config=0)
            robot.SetCollisionStrategy(strategy=0, safeTime=1000, safeDistance=100, safetyMargin=[10, 10, 10, 10, 10, 10])
            robot.SetStaticCollisionOnOff(status=1)
            self._upload_static_lua_scripts()
            self.upload_lua(studs_data_lua, self.studs_data_path)
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

    def goto_clearance_z(self, clearance_z_mm: float, program_path: str) -> dict[str, Any]:
        studs_data_lua = build_studs_data_lua([], clearance_z_mm)
        with self._lock:
            robot = self._client()
            self._upload_static_lua_scripts()
            self.upload_lua(studs_data_lua, self.studs_data_path)
            try:
                load_result = robot.ProgramLoad(program_name=program_path)
            except TypeError:
                load_result = robot.ProgramLoad(program_path)
            robot.Mode(0)
            run_result = robot.ProgramRun()
        return {"load_result": load_result, "run_result": run_result}

    def _run_with_timeout(self, fn: Callable[[], Any], timeout: float = SDK_CALL_TIMEOUT_S) -> Any:
        future = self._sdk_executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                self._robot = None
            raise RuntimeError(
                f"Robot did not respond within {int(timeout)} s — connection may be lost."
            )

    def pause(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
        pause_result = self._run_with_timeout(lambda: robot.ProgramPause())
        return {"pause_result": pause_result}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
        stop_result = self._run_with_timeout(lambda: robot.ProgramStop())
        return {"stop_result": stop_result}

    def get_tcp_pose(self) -> list[float]:
        with self._lock:
            robot = self._client()

        response = self._run_with_timeout(lambda: robot.GetActualTCPPose())
        error_code, pose = self._split_error_value(response)
        if error_code != 0 or not isinstance(pose, list) or len(pose) != 6:
            raise RuntimeError(f"Read TCP pose failed with error code {error_code}.")
        return [float(value) for value in pose]

    def get_joint_positions(self) -> list[float]:
        with self._lock:
            robot = self._client()

        response = self._run_with_timeout(lambda: robot.GetActualJointPosDegree())
        error_code, joints = self._split_error_value(response)
        if error_code != 0 or not isinstance(joints, list) or len(joints) != 6:
            raise RuntimeError(f"Read joint positions failed with error code {error_code}.")
        return [float(value) for value in joints]

    def jog(
        self,
        ref: int,
        axis: int,
        direction: int,
        distance: float,
        velocity: float,
        collision_sensitivity: int = 3,
    ) -> dict[str, Any]:
        self._ensure_jog_safety(collision_sensitivity)
        with self._lock:
            robot = self._client()

        result = self._run_with_timeout(
            lambda: robot.StartJOG(ref=ref, nb=axis, dir=direction, max_dis=distance, vel=velocity, acc=100.0)
        )
        error_code, _ = self._split_error_value(result)
        if error_code != 0:
            raise RuntimeError(f"Jog command failed with error code {error_code}.")

        return {
            "ref": ref,
            "axis": axis,
            "direction": direction,
            "distance": distance,
            "velocity": velocity,
            "result": result,
        }

    def stop_jog(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()

        result = self._run_with_timeout(lambda: robot.ImmStopJOG())
        error_code, _ = self._split_error_value(result)
        if error_code != 0:
            raise RuntimeError(f"Jog stop failed with error code {error_code}.")
        return {"result": result}

    def jog_snapshot(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()

        program_state_resp, current_line_resp, tcp_pose_resp, joint_pos_resp = self._run_with_timeout(
            lambda: (
                robot.GetProgramState(),
                robot.GetCurrentLine(),
                robot.GetActualTCPPose(),
                robot.GetActualJointPosDegree(),
            )
        )

        state_error, program_state = self._split_error_value(program_state_resp)
        line_error, current_line = self._split_error_value(current_line_resp)
        tcp_error, tcp_pose = self._split_error_value(tcp_pose_resp)
        joint_error, joint_positions = self._split_error_value(joint_pos_resp)

        connected = all(code == 0 for code in (state_error, line_error, tcp_error, joint_error))
        snapshot: dict[str, Any] = {
            "connected": connected,
            "program_state": STATE_MAP.get(program_state, "unknown"),
            "program_state_raw": program_state,
            "program_state_error": state_error,
            "current_line": current_line,
            "current_line_error": line_error,
            "tcp_pose": tcp_pose if isinstance(tcp_pose, list) and len(tcp_pose) == 6 else None,
            "tcp_pose_error": tcp_error,
            "joint_positions": joint_positions if isinstance(joint_positions, list) and len(joint_positions) == 6 else None,
            "joint_positions_error": joint_error,
        }

        if not connected:
            snapshot["error"] = (
                "Live jog data unavailable "
                f"(state={state_error}, line={line_error}, tcp={tcp_error}, joints={joint_error})."
            )

        return snapshot

    def enable_drag(self) -> None:
        with self._lock:
            robot = self._client()
        result = self._run_with_timeout(lambda: robot.DragTeachSwitch(1))
        error_code, _ = self._split_error_value(result)
        if error_code != 0:
            raise RuntimeError(f"DragTeachSwitch(1) failed with error code {error_code}.")

    def disable_drag(self) -> None:
        with self._lock:
            robot = self._client()
        result = self._run_with_timeout(lambda: robot.DragTeachSwitch(0))
        error_code, _ = self._split_error_value(result)
        if error_code != 0:
            raise RuntimeError(f"DragTeachSwitch(0) failed with error code {error_code}.")

    def record_wobj_point(self, point_num: int) -> None:
        with self._lock:
            robot = self._client()
        result = self._run_with_timeout(lambda: robot.SetWObjCoordPoint(point_num))
        error_code, _ = self._split_error_value(result)
        if error_code != 0:
            raise RuntimeError(f"SetWObjCoordPoint({point_num}) failed with error code {error_code}.")

    def compute_and_apply_tcp(self, joint_positions: list[list[float]], tool_id: int = 1) -> list[float]:
        with self._lock:
            robot = self._client()
        result = self._run_with_timeout(
            lambda: robot.ComputeToolCoordWithPoints(0, joint_positions)
        )
        error_code, tcp_offset = self._split_error_value(result)
        if error_code != 0 or not isinstance(tcp_offset, list) or len(tcp_offset) < 6:
            raise RuntimeError(f"ComputeToolCoordWithPoints failed (code {error_code}).")
        tcp_floats = [float(v) for v in tcp_offset[:6]]
        with self._lock:
            robot = self._client()
        set_resp = self._run_with_timeout(
            lambda: robot.SetToolList(tool_id, tcp_floats, 0, 0, 0)
        )
        error_code2, _ = self._split_error_value(set_resp)
        if error_code2 != 0:
            raise RuntimeError(f"SetToolList failed (code {error_code2}).")
        return tcp_floats

    def compute_and_apply_wobj(self, wobj_id: int = 1) -> list[float]:
        with self._lock:
            robot = self._client()
        compute_resp = self._run_with_timeout(lambda: robot.ComputeWObjCoord(wobj_id, 0))
        error_code, coord = self._split_error_value(compute_resp)
        if error_code != 0 or not isinstance(coord, list) or len(coord) < 6:
            raise RuntimeError(f"ComputeWObjCoord failed with error code {error_code}.")
        coord_floats = [float(v) for v in coord[:6]]
        set_resp = self._run_with_timeout(lambda: robot.SetWObjCoord(wobj_id, coord_floats, 0))
        error_code2, _ = self._split_error_value(set_resp)
        if error_code2 != 0:
            raise RuntimeError(f"SetWObjCoord failed with error code {error_code2}.")
        self._run_with_timeout(lambda: robot.SetWObjList(wobj_id, coord_floats, 0))
        return coord_floats

    def status(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()

        program_state_resp, current_line_resp = self._run_with_timeout(
            lambda: (robot.GetProgramState(), robot.GetCurrentLine())
        )

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
