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

# Mode(): the controller's operating mode, not a program state. Auto is what a
# Lua program runs under; manual is the one with the green indicator, and is
# where the cell should sit whenever WeldFlex is not driving it.
MODE_AUTO = 0
MODE_MANUAL = 1

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


def _upload_hint(err_code: int, path: Path, detail: Any = None) -> str:
    """Turn the SDK's catch-all upload codes into something actionable.

    `LuaUpload` reports -1 (RobotError.ERR_OTHER) for five unrelated failures in
    `__FileUpLoad` — a refused FileUpload RPC, no connect to the file port, a
    short send, and a reply that wasn't "SUCCESS" — and never says which. Worth
    naming, because a bare "code -1" reads like a compile error and it is not:
    the transfer fails before the controller ever parses the Lua.

    The exception: when the transfer succeeds but the post-upload
    `LuaUpLoadUpdate` check refuses the file, the SDK returns (code, errorStr)
    instead of a bare int. `detail` carries that errorStr — the controller's
    own stated reason — so when it is present, report it verbatim and skip the
    transfer guesswork, which would be wrong.
    """
    if detail not in (None, ""):
        return (
            f" — the transfer completed; the controller refused the file at the "
            f"post-upload check: {detail}"
        )
    if err_code == -1:
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        return (
            f" — the controller refused the transfer of {size} bytes; the Lua was "
            "never parsed. Every program known to upload here is under 2 KB, so try "
            "a smaller file first, then Reconnect from Robot Diagnostics (an aborted "
            "transfer leaves the file port unusable until the session is remade)."
        )
    if err_code == -7:
        return " — the SDK could not find the local file it was asked to send."
    return ""


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
        err_code, detail = self._unpack(error)
        if err_code != 0:
            raise RuntimeError(
                f"LuaUpload failed (code {err_code}): {path.name}{_upload_hint(err_code, path, detail)}"
            )
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

    def joint_overspeed_protect(self, strategy: int = 3, speed_percent: int = 50) -> None:
        """Arm the controller's joint-overspeed handling for subsequent motion.

        Raw RPC: the SDK only issues JointOverSpeedProtectStart as a private
        bracket around its own MoveL (Robot.py:3088), but the motion that needs
        it here — weld.lua's FT_FindSurface descent — executes inside a
        controller-side program, so it is armed session-wide instead. strategy:
        0 off, 1 standard, 2 error-stop on overspeed, 3 adaptive slowdown.
        speed_percent is the vendor's "allowed slow-down threshold" [0-100]
        (their default is 10); 50 gives the adaptive strategy real headroom.

        Deliberately never paired with JointOverSpeedProtectEnd: nothing
        host-side observes the program end reliably, and armed is the safer
        resting state while the FT_FindSurface axis-2 overspeed fault
        (2026-07-28) is under investigation. Whether this API governs
        program-executed motion at all on this firmware is unverified — a
        nonzero return here is how we find out it does not exist.
        """
        resp = self._call(
            lambda r: r.robot.JointOverSpeedProtectStart(int(strategy), int(speed_percent))
        )
        err_code, _ = self._unpack(resp)
        if err_code != 0:
            raise RuntimeError(f"JointOverSpeedProtectStart failed (code {err_code})")

    def set_manual_mode(self) -> None:
        """Hand the cell back to the operator in manual mode (the green indicator).

        `run_program` puts the controller into auto and nothing takes it out again
        when the program ends, so this is the way back.
        """
        err = self._call(lambda r: r.Mode(MODE_MANUAL))
        err_code, _ = self._unpack(err)
        if err_code != 0:
            raise RuntimeError(f"Mode(manual) failed (code {err_code})")

    def run_program(self, program_name: str) -> None:
        """Run a Lua program already stored on the robot under /fruser/."""
        self._call(lambda r: r.Mode(MODE_AUTO))
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
        """Force/torque via raw XML-RPC, bypassing the SDK's local-cache read.

        The SDK's FT_GetForceTorqueRCS and ft_sensor_active both read
        robot_state_pkg, which only the CNDE stream (port 20005) ever writes —
        and CNDE never connects on this FR-16 firmware, so the cache is zeroed
        forever and the sensor always looked inactive. Same bypass as
        robot_link's _read_program_state. The raw response is flat:
        [err, fx, fy, fz, tx, ty, tz].
        """
        resp = self._call(lambda r: r.robot.FT_GetForceTorqueRCS(0), retries=1)
        if isinstance(resp, (list, tuple)) and len(resp) >= 7:
            err_code, values = int(resp[0]), resp[1:7]
        else:
            err_code, values = self._unpack(resp)
        if err_code != 0 or values is None:
            raise RuntimeError(f"FT_GetForceTorqueRCS failed (code {err_code})")
        return {
            "fx": float(values[0]), "fy": float(values[1]), "fz": float(values[2]),
            "mx": float(values[3]), "my": float(values[4]), "mz": float(values[5]),
            # No liveness flag exists over RPC; err==0 only says the controller
            # answered. Real liveness is the commissioning checks (values dither,
            # RCS != Origin, hand push moves the expected axis).
            "active": True,
        }

    def weld_probe(
        self,
        stud_di: int = 1,
        ready_di: int = 0,
        sysvar_slots: tuple[int, ...] = (1, 2),
    ) -> dict:
        """Everything the Weld Test page shows, in a single worker dispatch.

        The two inputs are different signals and both have to be watched:
        `stud_di` (DI1) is continuity welder -> work -> gun, i.e. the stud is
        seated; `ready_di` (DI0) is the welder's caps-at-charge line. weld.lua
        gates the pulse on both, so a stalled weld test is only diagnosable if the
        page can show which one is missing.

        `sysvar_slots` are the controller system variables weld.lua's pub() writes
        its progress to — slot 1 is the phase code, slot 2 the raw return value of
        the last FT_* instruction. This is the only channel that reports anything
        from inside a running Lua program: print()/error() never leave the pendant,
        and force reads are refused for the whole time force control owns the
        sensor. GetSysVarValue is a genuine RPC call (Robot.py:5460), not another
        robot_state_pkg read, which is what makes it work mid-run.

        Polled a few times a second while a weld test runs, so every read shares
        one submission rather than queueing behind the others on the link's single
        worker. All bypass the SDK wrappers for the reason ft_read documents: the
        wrapped GetDI and FT_GetForceTorqueRCS read robot_state_pkg, which only the
        CNDE stream fills, and CNDE never connects on this firmware. GetActualTCPPose
        is unwrapped for consistency and to keep the whole probe on one code path —
        its SDK wrapper does do real RPC.

        `fz` is the sensor's native value — negative under compression. Callers
        that display it flip the sign; see FT_FZ_DISPLAY_SIGN in app.py.

        Every read here is best-effort — nothing raises. The DI reads come back
        as `None` on failure (they have no live-verified precedent). The force
        read reports its error code in `ft_err` with `fz = None` instead of
        raising, because a nonzero code is routine, not exceptional: the
        controller refuses FT_GetForceTorqueRCS with code 14 for the whole time
        FT_FindSurface is executing (observed live 2026-07-28 — the force-control
        task owns the sensor), which is exactly when this gets polled hardest.
        The caller decides whether a given code is "busy" or a real fault.
        """
        def _probe(r):
            ft = r.robot.FT_GetForceTorqueRCS(0)
            dis = []
            for di_id in (stud_di, ready_di):
                try:
                    dis.append(r.robot.GetDI(int(di_id), 0))
                except Exception:
                    dis.append(None)
            svars = []
            for slot in sysvar_slots:
                try:
                    svars.append(r.robot.GetSysVarValue(int(slot)))
                except Exception:
                    svars.append(None)
            try:
                pose = r.robot.GetActualTCPPose(0)
            except Exception:
                pose = None
            try:
                state_resp = r.robot.GetProgramState()
            except Exception:
                state_resp = None
            try:
                line_resp = r.robot.GetCurrentLine()
            except Exception:
                line_resp = None
            try:
                fault_resp = r.robot.GetRobotErrorCode()
            except Exception:
                fault_resp = None
            return ft, dis[0], dis[1], svars, pose, state_resp, line_resp, fault_resp

        ft_resp, stud_resp, ready_resp, svar_resps, pose_resp, state_resp, line_resp, fault_resp = self._call(_probe, retries=1, timeout=1.0)

        if isinstance(ft_resp, (list, tuple)) and len(ft_resp) >= 7:
            ft_err, fz = int(ft_resp[0]), float(ft_resp[3])
        else:
            ft_err, fz = int(self._unpack(ft_resp)[0]), None
        if ft_err != 0:
            fz = None

        def _level(resp):
            if isinstance(resp, (list, tuple)) and len(resp) >= 2 and int(resp[0]) == 0:
                return 1 if int(resp[1]) else 0
            return None

        def _number(resp):
            """Value out of an (err, value) response, or None on any failure."""
            if isinstance(resp, (list, tuple)) and len(resp) >= 2 and int(resp[0]) == 0:
                try:
                    return float(resp[1])
                except (TypeError, ValueError):
                    return None
            return None

        # Keyed by slot so a caller that asks for different slots still gets a
        # dict it can index by the number weld.lua wrote to.
        sysvars = {
            int(slot): _number(resp)
            for slot, resp in zip(sysvar_slots, svar_resps)
        }

        tcp_z = None
        if isinstance(pose_resp, (list, tuple)) and len(pose_resp) >= 4 and int(pose_resp[0]) == 0:
            try:
                tcp_z = float(pose_resp[3])
            except (TypeError, ValueError):
                tcp_z = None

        program_state_raw = None
        if isinstance(state_resp, (list, tuple)) and len(state_resp) >= 2 and int(state_resp[0]) == 0:
            try:
                program_state_raw = int(state_resp[1])
            except (TypeError, ValueError):
                program_state_raw = None
        elif isinstance(state_resp, (int, float)):
            try:
                program_state_raw = int(state_resp)
            except (TypeError, ValueError):
                program_state_raw = None

        line = None
        if isinstance(line_resp, (list, tuple)) and len(line_resp) >= 2 and int(line_resp[0]) == 0:
            try:
                line = int(line_resp[1])
            except (TypeError, ValueError):
                line = None
        elif isinstance(line_resp, (int, float)):
            try:
                line = int(line_resp)
            except (TypeError, ValueError):
                line = None

        fault_main = None
        fault_sub = None
        if isinstance(fault_resp, (list, tuple)) and len(fault_resp) >= 3 and int(fault_resp[0]) == 0:
            try:
                fault_main = int(fault_resp[1]) or None
            except (TypeError, ValueError):
                fault_main = None
            try:
                fault_sub = int(fault_resp[2]) or None
            except (TypeError, ValueError):
                fault_sub = None

        return {
            "ft_err": ft_err,
            "fz": fz,
            "stud_di": int(stud_di),
            "stud_on_work": _level(stud_resp),
            "ready_di": int(ready_di),
            "weld_ready": _level(ready_resp),
            "sysvars": sysvars,
            "tcp_z": tcp_z,
            "program_state_raw": program_state_raw,
            "line": line,
            "fault_main": fault_main,
            "fault_sub": fault_sub,
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
        no robot traffic. `source` says where the codes came from and must be read
        alongside them:

          "rpc"   — asked the controller directly (robot_link._read_fault_codes).
          "cache" — robot_state_pkg, which only the CNDE stream fills.
          "none"  — neither channel is available, so a blank code is NOT evidence
                    of no fault. CNDE does not connect on the FR-16, so this was
                    the only outcome until the raw read was added.
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
