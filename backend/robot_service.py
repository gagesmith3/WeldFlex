from __future__ import annotations

import concurrent.futures
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

SDK_CALL_TIMEOUT_S = 5.0
DEFAULT_STUDS_DATA_PATH = "/fruser/studs_data.lua"

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
    0: "stopped",
    1: "stopped",
    2: "running",
    3: "paused",
}


class WeldFlexRobotService:
    def __init__(
        self,
        robot_ip: str,
        controller_host: str | None = None,
        studs_data_path: str = DEFAULT_STUDS_DATA_PATH,
    ) -> None:
        self.robot_ip = robot_ip
        self.controller_host = controller_host or robot_ip
        self.studs_data_path = self._normalize_studs_data_path(studs_data_path)
        self._robot: Any | None = None
        self._last_jog_safety_sensitivity: int | None = None
        self._last_jog_safety_monotonic = 0.0
        self._jog_safety_refresh_s = 0.75
        self._lock = threading.RLock()
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
                self._robot = None
                self._last_jog_safety_sensitivity = None
                self._last_jog_safety_monotonic = 0.0

            if controller_host:
                self.controller_host = controller_host
            elif robot_ip and not controller_host:
                self.controller_host = robot_ip

            if studs_data_path:
                self.studs_data_path = self._normalize_studs_data_path(studs_data_path)

    @staticmethod
    def _normalize_studs_data_path(studs_data_path: str) -> str:
        normalized = str(studs_data_path or "").strip()
        if normalized != DEFAULT_STUDS_DATA_PATH:
            raise ValueError(
                f"studs_data_path must be '{DEFAULT_STUDS_DATA_PATH}' to match studCycle.lua include path."
            )
        return normalized

    def _client(self) -> Any:
        if self._robot is None:
            self._robot = Robot.RPC(self.robot_ip)
        return self._robot

    def _upload_lua_file(self, robot: Any, local_path: str, remote_name: str) -> Any:
        try:
            self._run_with_timeout(lambda: robot.LuaDelete(remote_name))
        except Exception:
            pass
        result = self._run_with_timeout(lambda: robot.LuaUpload(local_path), timeout=30.0)
        if not self._is_success_result(result):
            raise RuntimeError(f"LuaUpload({remote_name}) failed: {result!r}")
        return result

    def upload_program_lua(self, local_lua_path: str) -> dict[str, Any]:
        """Upload a Lua program file to the controller (one-time setup)."""
        local_path = Path(local_lua_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local Lua file not found: {local_path}")
        remote_name = local_path.name
        lua_text = local_path.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = os.path.join(tmp_dir, remote_name)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(lua_text)
            with self._lock:
                robot = self._client()
                result = self._upload_lua_file(robot, tmp_path, remote_name)

        return {"uploaded": f"/fruser/{remote_name}", "result": result}

    def configure_safety(self, collision_sensitivity: int = 3) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
        level = [float(collision_sensitivity)] * 6
        anticollision = self._run_with_timeout(
            lambda: robot.SetAnticollision(mode=0, level=level, config=0)
        )
        strategy = self._run_with_timeout(
            lambda: robot.SetCollisionStrategy(
                strategy=0, safeTime=1000, safeDistance=100, safeVel=100,
                safetyMargin=[10, 10, 10, 10, 10, 10],
            )
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

    def upload_load_run(
        self,
        studs: list[dict[str, float]],
        remote_file: str,
    ) -> dict[str, Any]:
        studs_data_lua = build_studs_data_lua(studs)
        remote_data_name = "studs_data.lua"

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = os.path.join(tmp_dir, remote_data_name)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(studs_data_lua)

            with self._lock:
                robot = self._client()
                upload_result = self._upload_lua_file(robot, tmp_path, remote_data_name)
                self._run_with_timeout(lambda: robot.Mode(0))
                self._run_with_timeout(lambda: robot.LoadDefaultProgConfig(0, remote_file))
                self._run_with_timeout(lambda: robot.ProgramLoad(remote_file))
                run_result = self._run_with_timeout(lambda: robot.ProgramRun())
                if not self._is_success_result(run_result):
                    fault_codes = self._get_fault_codes(robot)
                    raise RuntimeError(
                        f"ProgramRun failed: {run_result!r}, fault_codes={fault_codes!r}"
                    )

        return {
            "program": remote_file,
            "studs_uploaded": len(studs),
            "upload_result": upload_result,
            "run_result": run_result,
        }


    def goto_clearance_z(
        self,
        clearance_z_mm: float,
        zerozero_joints: list[float] | None = None,
        tool_id: int = 0,
    ) -> dict[str, Any]:
        configured_vel = float(os.getenv("WELDFLEX_DIRECT_MOVE_VEL", "50"))
        # Calibration jog should be slower than production moves.
        move_vel = min(configured_vel, 20.0)
        move_timeout = float(os.getenv("WELDFLEX_DIRECT_MOVE_TIMEOUT_S", "45"))

        with self._lock:
            robot = self._client()
            robot.Mode(0)
            robot.RobotEnable(1)
            tcp_resp = self._run_with_timeout(lambda: robot.GetActualTCPPose())
            tcp_error, tcp_result = self._split_error_value(tcp_resp)
            if tcp_error != 0 or not isinstance(tcp_result, list) or len(tcp_result) != 6:
                raise RuntimeError(f"GetActualTCPPose failed: code={tcp_error}, result={tcp_result!r}")
            current_tcp = [float(v) for v in tcp_result]

        # Safety: lift straight up from current pose only (no XY change).
        clearance_pos = [
            current_tcp[0],
            current_tcp[1],
            current_tcp[2] + float(clearance_z_mm),
            current_tcp[3],
            current_tcp[4],
            current_tcp[5],
        ]
        resp = self._run_with_timeout(
            lambda: robot.MoveL(desc_pos=clearance_pos, tool=int(tool_id), user=0, vel=move_vel),
            timeout=move_timeout,
        )
        err, _ = self._split_error_value(resp)
        if err == 14:
            # Err 14 is a generic controller-side execution failure. Recover once by
            # clearing resettable errors and re-establishing auto/enabled state.
            reset_result = self._run_with_timeout(lambda: robot.ResetAllError())
            mode_result = self._run_with_timeout(lambda: robot.Mode(0))
            enable_result = self._run_with_timeout(lambda: robot.RobotEnable(1))
            resp = self._run_with_timeout(
                lambda: robot.MoveL(desc_pos=clearance_pos, tool=int(tool_id), user=0, vel=move_vel),
                timeout=move_timeout,
            )
            err, _ = self._split_error_value(resp)
            if err != 0:
                fault_codes = self._get_fault_codes(robot)
                raise RuntimeError(
                    f"MoveL to clearance z={clearance_z_mm:g} mm failed after retry: code={err}, "
                    f"tool_id={int(tool_id)}, reset_result={reset_result!r}, "
                    f"mode_result={mode_result!r}, enable_result={enable_result!r}, "
                    f"fault_codes={fault_codes!r}"
                )
        elif err != 0:
            fault_codes = self._get_fault_codes(robot)
            raise RuntimeError(
                f"MoveL to clearance z={clearance_z_mm:g} mm failed: code={err}, "
                f"tool_id={int(tool_id)}, fault_codes={fault_codes!r}"
            )
        return {
            "clearance_z_mm": clearance_z_mm,
            "move_result": resp,
            "tool_id": int(tool_id),
            "start_tcp": current_tcp,
            "target_tcp": clearance_pos,
        }

    def goto_zerozero(self, zerozero_joints: list[float] | None = None) -> dict[str, Any]:
        if not zerozero_joints or len(zerozero_joints) != 6:
            raise ValueError("zerozero_joints must be a list of 6 floats.")
        joints = [float(v) for v in zerozero_joints]
        move_vel = float(os.getenv("WELDFLEX_DIRECT_MOVE_VEL", "50"))
        move_timeout = float(os.getenv("WELDFLEX_DIRECT_MOVE_TIMEOUT_S", "45"))

        with self._lock:
            robot = self._client()
            robot.Mode(0)
            robot.RobotEnable(1)

        resp = self._run_with_timeout(
            lambda: robot.MoveJ(joint_pos=joints, tool=0, user=0, vel=move_vel),
            timeout=move_timeout,
        )
        err, _ = self._split_error_value(resp)
        if err != 0:
            raise RuntimeError(f"MoveJ to zerozero failed: code={err}")
        return {"move_result": resp}

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
        pause_api = getattr(robot, "ProgramPause", None)
        if not callable(pause_api):
            raise RuntimeError("ProgramPause is not available in this SDK build.")
        pause_result = self._run_with_timeout(lambda: pause_api())
        return {"pause_result": pause_result}

    def resume(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
        resume_api = getattr(robot, "ProgramResume", None)
        if not callable(resume_api):
            raise RuntimeError("ProgramResume is not available in this SDK build.")
        resume_result = self._run_with_timeout(lambda: resume_api())
        return {"resume_result": resume_result}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
        stop_api = getattr(robot, "ProgramStop", None)
        if not callable(stop_api):
            raise RuntimeError("ProgramStop is not available in this SDK build.")
        stop_result = self._run_with_timeout(lambda: stop_api())
        return {"stop_result": stop_result}

    def reset_all_errors(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()
        result = self._run_with_timeout(lambda: robot.ResetAllError())
        # Return robot to auto mode — it may have been left in manual mode by a failed upload.
        mode_result = self._run_with_timeout(lambda: robot.Mode(0))
        return {"reset_result": result, "mode_result": mode_result}

    def close(self) -> None:
        with self._lock:
            if self._robot is not None:
                try:
                    self._robot.CloseRPC()
                except Exception:
                    pass
                self._robot = None

    def reconnect(self) -> dict[str, Any]:
        with self._lock:
            if self._robot is not None:
                try:
                    self._robot.CloseRPC()
                except Exception:
                    pass
            self._robot = None
            robot = self._client()
        return {"reconnected": True, "robot_ip": self.robot_ip}

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
        # FAIRINO examples enter manual mode before enabling drag teach.
        stop_result: Any = None
        try:
            stop_result = self._run_with_timeout(lambda: robot.ProgramStop())
        except Exception:
            pass

        mode_result = self._run_with_timeout(lambda: robot.Mode(1))
        enable_result = self._run_with_timeout(lambda: robot.RobotEnable(1))
        result = self._run_with_timeout(lambda: robot.DragTeachSwitch(state=1))
        error_code, _ = self._split_error_value(result)
        if error_code in (-1, 14):
            reset_result = self._run_with_timeout(lambda: robot.ResetAllError())
            mode_result = self._run_with_timeout(lambda: robot.Mode(1))
            enable_result = self._run_with_timeout(lambda: robot.RobotEnable(1))
            result = self._run_with_timeout(lambda: robot.DragTeachSwitch(state=1))
            error_code, _ = self._split_error_value(result)
            if error_code != 0:
                fault_codes = self._get_fault_codes(robot)
                raise RuntimeError(
                    "DragTeachSwitch(1) failed after retry. "
                    f"code={error_code}, mode_result={mode_result!r}, "
                    f"enable_result={enable_result!r}, stop_result={stop_result!r}, "
                    f"reset_result={reset_result!r}, fault_codes={fault_codes!r}"
                )
        if error_code != 0:
            fault_codes = self._get_fault_codes(robot)
            raise RuntimeError(
                f"DragTeachSwitch(1) failed with error code {error_code}. "
                f"mode_result={mode_result!r}, enable_result={enable_result!r}, "
                f"stop_result={stop_result!r}, fault_codes={fault_codes!r}"
            )

    def disable_drag(self) -> None:
        with self._lock:
            robot = self._client()
        result = self._run_with_timeout(lambda: robot.DragTeachSwitch(state=0))
        error_code, _ = self._split_error_value(result)
        if error_code != 0:
            fault_codes = self._get_fault_codes(robot)
            raise RuntimeError(
                f"DragTeachSwitch(state=0) failed with error code {error_code}. "
                f"fault_codes={fault_codes!r}"
            )

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
        # SetToolCoord activates the coordinate slot immediately (RAM); SetToolList persists it.
        # Both are required — pattern from FAIRINO SDK example (TestSetCommand.py).
        coord_resp = self._run_with_timeout(
            lambda: robot.SetToolCoord(tool_id, tcp_floats, 0, 0, tool_id, 0)
        )
        error_code2, _ = self._split_error_value(coord_resp)
        if error_code2 != 0:
            raise RuntimeError(f"SetToolCoord failed (code {error_code2}).")
        list_resp = self._run_with_timeout(
            lambda: robot.SetToolList(tool_id, tcp_floats, 0, 0, 0)
        )
        error_code3, _ = self._split_error_value(list_resp)
        if error_code3 != 0:
            raise RuntimeError(f"SetToolList failed (code {error_code3}).")
        verify_resp = self._run_with_timeout(lambda: robot.GetTCPOffset(0))
        error_code4, tcp_verified = self._split_error_value(verify_resp)
        if error_code4 != 0 or not isinstance(tcp_verified, list) or len(tcp_verified) < 6:
            raise RuntimeError(f"GetTCPOffset verification failed (code {error_code4}).")
        return [float(v) for v in tcp_verified[:6]]

    def compute_and_apply_wobj(self, wobj_id: int = 1, method: int = 1, ref_frame: int = 0) -> list[float]:
        with self._lock:
            robot = self._client()
        method = int(method)
        if method not in (0, 1):
            raise ValueError("WObj compute method must be 0 (origin-x-z) or 1 (origin-x-xy).")
        ref_frame = int(ref_frame)
        # Calibration is captured in drag mode, but applying coordinates is more reliable
        # with drag off and controller in auto/enabled state.
        try:
            self._run_with_timeout(lambda: robot.DragTeachSwitch(0))
        except Exception:
            pass
        mode_result = self._run_with_timeout(lambda: robot.Mode(0))
        enable_result = self._run_with_timeout(lambda: robot.RobotEnable(1))

        compute_resp = self._run_with_timeout(lambda: robot.ComputeWObjCoord(method, ref_frame))
        error_code, coord = self._split_error_value(compute_resp)
        if error_code != 0 or not isinstance(coord, list) or len(coord) < 6:
            raise RuntimeError(f"ComputeWObjCoord failed with error code {error_code}.")
        coord_floats = [float(v) for v in coord[:6]]
        set_resp = self._run_with_timeout(lambda: robot.SetWObjCoord(wobj_id, coord_floats, ref_frame))
        error_code2, _ = self._split_error_value(set_resp)
        if error_code2 == 14:
            # Err 14 is generic interface execution failure; recover once by clearing
            # resettable faults and re-applying in auto mode.
            reset_result = self._run_with_timeout(lambda: robot.ResetAllError())
            mode_result = self._run_with_timeout(lambda: robot.Mode(0))
            enable_result = self._run_with_timeout(lambda: robot.RobotEnable(1))
            set_resp = self._run_with_timeout(lambda: robot.SetWObjCoord(wobj_id, coord_floats, ref_frame))
            error_code2, _ = self._split_error_value(set_resp)
            if error_code2 != 0:
                fault_codes = self._get_fault_codes(robot)
                raise RuntimeError(
                    "SetWObjCoord failed after recovery retry. "
                    f"code={error_code2}, mode_result={mode_result!r}, enable_result={enable_result!r}, "
                    f"reset_result={reset_result!r}, fault_codes={fault_codes!r}"
                )
        if error_code2 != 0:
            fault_codes = self._get_fault_codes(robot)
            raise RuntimeError(
                f"SetWObjCoord failed with error code {error_code2}. "
                f"mode_result={mode_result!r}, enable_result={enable_result!r}, fault_codes={fault_codes!r}"
            )
        list_resp = self._run_with_timeout(lambda: robot.SetWObjList(wobj_id, coord_floats, ref_frame))
        error_code3, _ = self._split_error_value(list_resp)
        if error_code3 != 0:
            fault_codes = self._get_fault_codes(robot)
            raise RuntimeError(
                f"SetWObjList failed with error code {error_code3}. "
                f"fault_codes={fault_codes!r}"
            )
        verify_resp = self._run_with_timeout(lambda: robot.GetWObjOffset(0))
        error_code4, wobj_verified = self._split_error_value(verify_resp)
        if error_code4 != 0 or not isinstance(wobj_verified, list) or len(wobj_verified) < 6:
            raise RuntimeError(f"GetWObjOffset verification failed (code {error_code4}).")
        return [float(v) for v in wobj_verified[:6]]

    def status(self) -> dict[str, Any]:
        with self._lock:
            robot = self._client()

        # GetProgramState reads from local CNDE cache (no XML-RPC call).
        # GetCurrentLine is XML-RPC — used only for connection health check.
        program_state_resp, current_line_resp = self._run_with_timeout(
            lambda: (robot.GetProgramState(), robot.GetCurrentLine())
        )
        fault_codes = self._get_fault_codes(robot)

        state_error, program_state = self._split_error_value(program_state_resp)
        line_error, current_line = self._split_error_value(current_line_resp)
        connected = (state_error == 0) or (line_error == 0)

        return {
            "connected": connected,
            "program_state_error": state_error,
            "current_line_error": line_error,
            "program_state_response": program_state_resp,
            "current_line_response": current_line_resp,
            "program_state_raw": program_state,
            "program_state": STATE_MAP.get(program_state, "unknown"),
            "current_line": current_line,
            "fault_codes": fault_codes,
        }

    def _get_fault_codes(self, robot: Any) -> dict[str, Any]:
        robot_error_raw: Any = None
        safety_raw: Any = None

        get_robot_error = getattr(robot, "GetRobotErrorCode", None)
        if callable(get_robot_error):
            try:
                robot_error_raw = get_robot_error()
            except Exception as exc:
                robot_error_raw = f"GetRobotErrorCode failed: {exc}"

        get_safety_code = getattr(robot, "GetSafetyCode", None)
        if callable(get_safety_code):
            try:
                safety_raw = get_safety_code()
            except Exception as exc:
                safety_raw = f"GetSafetyCode failed: {exc}"

        error_code, error_value = self._split_error_value(robot_error_raw)
        main_code = None
        sub_code = None
        if isinstance(error_value, list) and len(error_value) >= 2:
            main_code = error_value[0]
            sub_code = error_value[1]

        safety_error, safety_value = self._split_error_value(safety_raw)
        if safety_value is None and isinstance(safety_raw, (int, float)):
            safety_value = int(safety_raw)

        return {
            "robot_error_response": robot_error_raw,
            "robot_error_error": error_code,
            "main_code": main_code,
            "sub_code": sub_code,
            "safety_response": safety_raw,
            "safety_error": safety_error,
            "safety_code": safety_value,
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

