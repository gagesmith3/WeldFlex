from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from robot_link import (
    ConnSnapshot,
    ConnState,
    RobotLink,
    has_conn_error,
    is_conn_code,
)

SDK_TIMEOUT_S = 5.0

STATE_MAP = {
    -1: "offline",
    0: "stopped",
    1: "stopped",
    2: "running",
    3: "paused",
    4: "drag",  # drag-teach active; undocumented in the SDK's own docstring
}

# StartJOG ref: 0-joint, 2-base coord, 4-tool coord, 8-workpiece coord.
# StopJOG ref is always start-ref + 1 (e.g. base-jog stop is ref 3, not 2).
JOG_START_REF = {
    ("cartesian", "base"): 2,
    ("cartesian", "tool"): 4,
    ("cartesian", "workpiece"): 8,
}
JOG_AXIS_NB = {"x": 1, "y": 2, "z": 3, "rx": 4, "ry": 5, "rz": 6}
JOG_DIRECTION = {"negative": 0, "positive": 1}

JOG_MOTION_TIMEOUT_S = 5.0
JOG_MOTION_POLL_S = 0.02
JOG_MOTION_SETTLE_S = 0.05


class WeldFlexRobotService:
    """Robot operations. Connection concerns live in RobotLink; this is the verb layer."""

    def __init__(self, robot_ip: str) -> None:
        self._link = RobotLink(robot_ip)
        self._start_time = time.time()

    # --- connection surface (delegated to the link) ---

    @property
    def robot_ip(self) -> str:
        return self._link.robot_ip

    def start(self) -> None:
        """Begin maintaining the connection. Non-blocking; call once at app startup."""
        self._link.start(connect=True)

    def shutdown(self, timeout: float = 5.0) -> None:
        self._link.shutdown(timeout=timeout)

    def connect(self) -> None:
        self._link.connect()

    def disconnect(self) -> None:
        self._link.disconnect()

    def reconnect(self) -> None:
        self._link.request_reconnect()

    def set_robot_ip(self, ip: str) -> bool:
        """Retarget the live connection. Returns True if the address changed."""
        return self._link.set_ip(ip)

    def snapshot(self) -> ConnSnapshot:
        return self._link.snapshot()

    def set_running_hint(self, running: bool) -> None:
        """Tell the link to probe faster while a program runs, for live line tracking."""
        self._link.set_heartbeat_hint(running)

    def thread_report(self) -> dict[str, Any]:
        return self._link.thread_report()

    # --- SDK call plumbing ---

    def _call(self, fn: Callable[[Any], Any], timeout: float = SDK_TIMEOUT_S, retries: int = 3) -> Any:
        """Run an SDK call on the link's worker thread with a hard timeout."""
        return self._link.call(
            fn, timeout=timeout, retries=retries, label=getattr(fn, "__name__", "call")
        )

    # Kept as staticmethods for the documented wrapper pattern; the implementations
    # live in robot_link so the link's own dispatch path shares them.
    _is_conn_code = staticmethod(is_conn_code)
    _has_conn_error = staticmethod(has_conn_error)

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
        err = self._call(lambda r: r.ProgramPause())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ProgramPause failed (code {err_code})")

    def resume_program(self) -> None:
        err = self._call(lambda r: r.ProgramResume())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ProgramResume failed (code {err_code})")

    def stop_program(self) -> None:
        err = self._call(lambda r: r.ProgramStop())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ProgramStop failed (code {err_code})")

    def upload_program(self, local_path: str, replace: bool = False) -> str:
        """Upload a Lua file to the robot. Returns the program name as stored on the robot."""
        path = Path(local_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Program file not found: {path}")
        if replace:
            # Ignore delete errors; file may not already exist.
            self._call(lambda r: r.LuaDelete(path.name))
        error = self._call(lambda r: r.LuaUpload(str(path)), timeout=30.0)
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

        # Delete the old copy — ignore errors (file may not exist yet)
        self._call(lambda r: r.LuaDelete(filename))

        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, filename)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(lua_content)
            error = self._call(lambda r: r.LuaUpload(tmp_path), timeout=30.0)
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
        self._call(lambda r: r.Mode(0))
        time.sleep(2)
        load_resp = self._call(lambda r: r.ProgramLoad(f"/fruser/{program_name}"))
        load_err, _ = self._unpack(load_resp)
        if load_err != 0:
            raise RuntimeError(f"ProgramLoad failed (code {load_err}): {program_name}")
        run_resp = self._call(lambda r: r.ProgramRun())
        run_err, _ = self._unpack(run_resp)
        if run_err != 0:
            raise RuntimeError(f"ProgramRun failed (code {run_err}): {program_name}")

    def upload_and_run(self, local_path: str) -> None:
        """Upload a Lua file then immediately run it."""
        program_name = self.upload_program(local_path)
        self.run_program(program_name)

    def jog_step(self, mode: str, frame: str, axis: str, direction: str, step: float, vel: float) -> None:
        """Jog one bounded step (StartJOG's max_dis) and block until the robot reports motion done."""
        ref = JOG_START_REF.get((mode, frame))
        nb = JOG_AXIS_NB.get(axis)
        dir_ = JOG_DIRECTION.get(direction)
        if ref is None or nb is None or dir_ is None:
            raise ValueError(f"Invalid jog request: mode={mode} frame={frame} axis={axis} direction={direction}")

        err = self._call(lambda r: r.StartJOG(ref, nb, dir_, float(step), float(vel)))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"StartJOG failed (code {err_code})")

        time.sleep(JOG_MOTION_SETTLE_S)
        deadline = time.time() + JOG_MOTION_TIMEOUT_S
        while time.time() < deadline:
            done_resp = self._call(lambda r: r.GetRobotMotionDone(), retries=1)
            _, done = self._unpack(done_resp)
            if done:
                return
            time.sleep(JOG_MOTION_POLL_S)

    def jog_stop(self) -> None:
        """Immediate jog stop — no ref needed, halts whatever mode is active."""
        err = self._call(lambda r: r.ImmStopJOG())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ImmStopJOG failed (code {err_code})")

    def jog_pose(self) -> list:
        """Current TCP pose [x, y, z, rx, ry, rz] for the jog position readout."""
        resp = self._call(lambda r: r.GetActualTCPPose(1), retries=1)
        err_code, pose = self._unpack(resp)
        if err_code != 0 or pose is None:
            raise RuntimeError(f"GetActualTCPPose failed (code {err_code})")
        return [float(v) for v in pose]

    def tcp_enable_drag(self) -> None:
        """Enter drag teach mode so the operator can physically position the robot."""
        err = self._call(lambda r: r.DragTeachSwitch(1))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"DragTeachSwitch(1) failed (code {err_code})")

    def tcp_record_point(self, point_num: int) -> None:
        """Exit drag mode and record current pose as TCP reference point N (1-4)."""
        def _seq(r):
            r.DragTeachSwitch(0)
            return r.SetTcp4RefPoint(point_num)
        err = self._call(_seq)
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"SetTcp4RefPoint({point_num}) failed (code {err_code})")

    def tcp_compute_and_apply(self, tool_id: int = 1) -> list:
        """Compute TCP from 4 recorded points and save to tool slot via SetToolCoord."""
        resp = self._call(lambda r: r.ComputeTcp4(), timeout=10.0)
        err_code, tcp_pose = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"ComputeTcp4 failed (code {err_code})")
        apply_resp = self._call(lambda r: r.SetToolCoord(tool_id, tcp_pose, 0, 0, 0, 0))
        apply_code, _ = self._unpack(apply_resp)
        if apply_code != 0:
            raise RuntimeError(f"SetToolCoord failed (code {apply_code})")
        return list(tcp_pose)

    def ft_setup(self) -> None:
        """Full init sequence per SDK example: configure → reset → activate → zero.
        Takes ~10 s due to required waits between commands."""
        def _sequence(r):
            r.FT_SetConfig(24, 0)   # company 24 = XJC (鑫精诚), device 0
            time.sleep(1)
            # Report in the tool frame (0=tool, 1=base). Asserted rather than
            # inherited: weld contact force acts along the torch approach axis,
            # which stays on one tool-frame axis as the robot reorients.
            r.FT_SetRCS(0)
            time.sleep(1)
            r.FT_Activate(0)        # reset first
            time.sleep(2)
            err = r.FT_Activate(1)  # activate
            if isinstance(err, int) and err != 0:
                raise RuntimeError(f"FT_Activate(1) failed (code {err})")
            time.sleep(2)
            r.FT_SetZero(0)         # clear old zero
            time.sleep(2)
            r.FT_SetZero(1)         # apply new zero

        self._call(_sequence, timeout=30.0)

    def ft_deactivate(self) -> None:
        err = self._call(lambda r: r.FT_Activate(0))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_Activate(0) failed (code {err_code})")

    def ft_zero(self) -> None:
        err = self._call(lambda r: r.FT_SetZero(1))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"FT_SetZero failed (code {err_code})")

    def ft_read(self) -> dict:
        def _read(r):
            resp = r.FT_GetForceTorqueRCS()
            active = bool(r.robot_state_pkg.ft_sensor_active)
            return resp, active

        resp, active = self._call(_read, retries=1)
        err_code, values = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"FT_GetForceTorqueRCS failed (code {err_code})")
        return {
            "fx": float(values[0]), "fy": float(values[1]), "fz": float(values[2]),
            "mx": float(values[3]), "my": float(values[4]), "mz": float(values[5]),
            "active": active,
        }

    def status(self) -> dict[str, Any]:
        """Connection + program state from the link's cached snapshot. Does no robot I/O.

        Polled once a second by every open page, so it must stay free: the robot is
        contacted once per heartbeat by the supervisor regardless of how many browsers
        are watching.
        """
        snap = self._link.snapshot()
        return {
            "connected": snap.connected,
            "program_state": STATE_MAP.get(snap.program_state_raw, "unknown"),
            "program_state_raw": snap.program_state_raw,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Everything status() reports plus link internals. Also a pure cache read."""
        snap = self._link.snapshot()
        live = snap.state in (ConnState.CONNECTED.value, ConnState.DEGRADED.value)
        err = 0 if live else -1

        return {
            "connected": snap.connected,
            "program_state": STATE_MAP.get(snap.program_state_raw, "unknown"),
            "program_state_error": err,
            "program_state_source": snap.program_state_source,
            "current_line": snap.current_line,
            "current_line_error": err,
            "fault_codes": self._fault_codes(snap),
            # link internals
            "state": snap.state,
            "ip": snap.ip,
            "last_error": snap.last_error,
            "last_success_age_s": snap.age_s(),
            "since_s": snap.since_s(),
            "attempts": snap.attempts,
            "consecutive_failures": snap.consecutive_failures,
            "generation": snap.generation,
            "worker_restarts": snap.worker_restarts,
            "probe_latency_ms": snap.probe_latency_ms,
            "retry_in_s": snap.retry_in_s,
            "busy": snap.busy,
            "busy_label": snap.busy_label,
            "threads": self._link.thread_report(),
        }

    @staticmethod
    def _fault_codes(snap: ConnSnapshot) -> dict[str, Any]:
        """Controller fault codes as of the last heartbeat.

        Collected by the probe rather than fetched here, so the diagnostics page costs
        no robot traffic. The underlying source is robot_state_pkg — the same struct
        GetRobotErrorCode reads — which only the CNDE stream fills. CNDE does not
        connect on the FR-16, so `source` reports "none" there and a blank code is not
        evidence of no fault.
        """
        return {
            "main_code": snap.fault_main,
            "sub_code": snap.fault_sub,
            "safety_code": None,
            "safety_error": 0,
            "robot_error_error": 0 if snap.fault_source == "cache" else -1,
            "source": snap.fault_source,
        }

    def reset_errors(self) -> None:
        err = self._call(lambda r: r.ResetAllError())
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"ResetAllError failed (code {err_code})")

    def uptime(self) -> str:
        elapsed = int(time.time() - self._start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
