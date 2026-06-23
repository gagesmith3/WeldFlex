import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from flask import Flask, render_template, request
from markupsafe import Markup
from robot_service import WeldFlexRobotService

app = Flask(__name__, template_folder="templates", static_folder="static")

ROBOT_IP = os.getenv("WELDFLEX_ROBOT_IP", "192.168.58.2")
KIOSK_MODE = os.getenv("WELDFLEX_KIOSK", "0") == "1"
robot = WeldFlexRobotService(robot_ip=ROBOT_IP)

def _get_git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(__file__),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "dev"

GIT_SHA = _get_git_sha()

_LIBERTY_LOG_PATH = os.path.join(os.path.dirname(__file__), "liberty_log.json")
_lbt_lock = threading.Lock()
_lbt_session: dict = {}
_lbt_log: list = []

_current_job: dict = {"name": None, "started_at": None}

_tcp_calib: dict = {
    "points_recorded": set(),
    "drag_point": None,
    "drag_error": None,
    "record_error": None,
    "apply_error": None,
    "applied": False,
    "tcp_offset": None,
}
_tcp_lock = threading.Lock()

def _lbt_load() -> None:
    global _lbt_log
    try:
        with open(_LIBERTY_LOG_PATH) as f:
            _lbt_log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _lbt_log = []

def _lbt_save() -> None:
    with open(_LIBERTY_LOG_PATH, "w") as f:
        json.dump(_lbt_log, f, indent=2)

_lbt_load()

_ICONS = {
    "home":        '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "link_2":      '<path d="M15 7h3a5 5 0 0 1 5 5 5 5 0 0 1-5 5h-3m-6 0H6a5 5 0 0 1-5-5 5 5 0 0 1 5-5h3"/><line x1="8" y1="12" x2="16" y2="12"/>',
    "link_2_off":  '<path d="M9 17H7A5 5 0 0 1 7 7"/><path d="M15 7h2a5 5 0 0 1 4 8"/><line x1="8" y1="12" x2="12" y2="12"/><line x1="2" y1="2" x2="22" y2="22"/>',
    "play":        '<polygon points="5 3 19 12 5 21 5 3"/>',
    "pause":       '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
    "square":      '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>',
    "save":        '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    "folder_open": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "trash_2":     '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    "loader":      '<line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/>',
    "circle":      '<circle cx="12" cy="12" r="10"/>',
    "pen_tool":    '<path d="M15.707 21.293a1 1 0 0 1-1.414 0l-1.586-1.586a1 1 0 0 1 0-1.414l5.586-5.586a1 1 0 0 1 1.414 0l1.586 1.586a1 1 0 0 1 0 1.414z"/><path d="m18 13-1.375-6.874a1 1 0 0 0-.746-.776L3.235 2.028a1 1 0 0 0-1.207 1.207L5.35 15.879a1 1 0 0 0 .776.746L13 18"/><path d="m2.3 2.3 7.286 7.286"/><circle cx="11" cy="11" r="2"/>',
    "crosshair":   '<circle cx="12" cy="12" r="10"/><line x1="22" y1="12" x2="18" y2="12"/><line x1="6" y1="12" x2="2" y2="12"/><line x1="12" y1="6" x2="12" y2="2"/><line x1="12" y1="22" x2="12" y2="18"/>',
    "activity":    '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.48 12H2"/>',
    "settings":    '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "bot":         '<path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    "layout_dashboard": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
}

def icon_safe(name, fallback="circle", width=14, height=14, class_=""):
    paths = _ICONS.get(name) or _ICONS.get(fallback) or _ICONS["circle"]
    cls_attr = f' class="{class_}"' if class_ else ""
    return Markup(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"{cls_attr}>'
        f'{paths}</svg>'
    )

app.jinja_env.globals["icon_safe"] = icon_safe

@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/operator")
def operator():
    return render_template("operator.html", page_title="Operator")

@app.route("/operator/admin")
def admin():
    settings = {
        "robot_ip": os.getenv("WELDFLEX_ROBOT_IP", "192.168.58.2"),
        "controller_host": os.getenv("WELDFLEX_CONTROLLER_HOST", "192.168.58.2"),
        "program_path": os.getenv("WELDFLEX_PROGRAM_PATH", "/fruser/"),
        "studs_data_path": os.getenv("WELDFLEX_STUDS_DATA_PATH", "/fruser/studs/"),
        "status_interval_ms": os.getenv("WELDFLEX_STATUS_INTERVAL_MS", "1000"),
    }
    return render_template("admin.html", page_title="Admin", settings=settings)

@app.route("/ui/settings/save", methods=["POST"])
def ui_settings_save():
    fields = ["robot_ip", "controller_host", "program_path", "studs_data_path", "status_interval_ms"]
    env_keys = {
        "robot_ip": "WELDFLEX_ROBOT_IP",
        "controller_host": "WELDFLEX_CONTROLLER_HOST",
        "program_path": "WELDFLEX_PROGRAM_PATH",
        "studs_data_path": "WELDFLEX_STUDS_DATA_PATH",
        "status_interval_ms": "WELDFLEX_STATUS_INTERVAL_MS",
    }
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    try:
        try:
            with open(env_path) as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        existing = {}
        for line in lines:
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.rstrip("\n")
        for field in fields:
            val = request.form.get(field, "").strip()
            if val:
                existing[env_keys[field]] = val
        with open(env_path, "w") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Save Settings", payload=payload)

@app.route("/operator/parts")
def parts():
    sample_parts = [
        {"name": "Bracket A", "last_run": "Jun 12, 2026"},
        {"name": "Bracket B", "last_run": "Jun 14, 2026"},
        {"name": "Mounting Plate", "last_run": "Never"},
    ]
    return render_template("parts.html", page_title="Parts", parts=sample_parts)

@app.context_processor
def inject_defaults():
    return {
        "kiosk_mode": KIOSK_MODE,
        "git_sha": GIT_SHA,
        "init_connection_snapshot": {
            "online": False,
            "program_state": "connecting...",
            "error": None,
        }
    }

@app.route("/ui/run", methods=["POST"])
def ui_run():
    program = request.form.get("program", "feedCycle.lua")
    try:
        robot.run_program(program)
        _current_job["name"] = program
        _current_job["started_at"] = datetime.now(timezone.utc).isoformat()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Run", payload=payload)

@app.route("/ui/operator/current-job")
def ui_operator_current_job():
    try:
        status = robot.status()
    except Exception:
        status = {"connected": False, "program_state": "error", "program_state_raw": None}
    return render_template(
        "partials/current_job.html",
        job_name=_current_job.get("name"),
        started_at=_current_job.get("started_at"),
        program_state=status.get("program_state", "unknown"),
        connected=status.get("connected", False),
    )

@app.route("/operator/liberty")
def liberty():
    with _lbt_lock:
        session = dict(_lbt_session)
        log = list(_lbt_log)
    return render_template("liberty.html", page_title="Liberty Test",
                           session=session, log=log)

@app.route("/ui/liberty/launch", methods=["POST"])
def ui_liberty_launch():
    try:
        cycles = max(1, int(request.form.get("cycles", "1")))
    except ValueError:
        cycles = 1
    program = request.form.get("program", "libertyTest.lua").strip() or "libertyTest.lua"
    error = None
    try:
        robot.run_liberty(cycles, program)
        with _lbt_lock:
            _lbt_session.clear()
            _lbt_session.update({
                "state": "running",
                "cycles_target": cycles,
                "program": program,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "launched_ts": time.time(),
            })
    except Exception as exc:
        error = str(exc)
    with _lbt_lock:
        session = dict(_lbt_session)
    return render_template("partials/liberty_status.html", session=session, error=error)

@app.route("/ui/liberty/pause", methods=["POST"])
def ui_liberty_pause():
    error = None
    try:
        robot.pause_program()
        with _lbt_lock:
            _lbt_session["state"] = "paused"
    except Exception as exc:
        error = str(exc)
    with _lbt_lock:
        session = dict(_lbt_session)
    return render_template("partials/liberty_status.html", session=session, error=error)

@app.route("/ui/liberty/resume", methods=["POST"])
def ui_liberty_resume():
    error = None
    try:
        robot.resume_program()
        with _lbt_lock:
            _lbt_session["state"] = "running"
    except Exception as exc:
        error = str(exc)
    with _lbt_lock:
        session = dict(_lbt_session)
    return render_template("partials/liberty_status.html", session=session, error=error)

@app.route("/ui/liberty/stop", methods=["POST"])
def ui_liberty_stop():
    try:
        robot.stop_program()
    except Exception:
        pass
    with _lbt_lock:
        if _lbt_session:
            _lbt_log.insert(0, {
                "timestamp": _lbt_session.get("started_at", ""),
                "program": _lbt_session.get("program", ""),
                "cycles_target": _lbt_session.get("cycles_target", 0),
                "cycles_done": _lbt_session.get("cycles_done", 0),
                "status": "aborted",
                "ended_at": datetime.now().isoformat(timespec="seconds"),
            })
            _lbt_save()
            _lbt_session.clear()
    with _lbt_lock:
        session = dict(_lbt_session)
    return render_template("partials/liberty_status.html", session=session, error=None)

@app.route("/ui/liberty/status")
def ui_liberty_status():
    try:
        robot_state = robot.status()["program_state"]
    except Exception:
        robot_state = None
    with _lbt_lock:
        age = time.time() - _lbt_session.get("launched_ts", 0)
        if _lbt_session.get("state") in ("running", "paused") and robot_state == "stopped" and age > 10:
            _lbt_log.insert(0, {
                "timestamp": _lbt_session.get("started_at", ""),
                "program": _lbt_session.get("program", ""),
                "cycles_target": _lbt_session.get("cycles_target", 0),
                "cycles_done": _lbt_session.get("cycles_target", 0),
                "status": "completed",
                "ended_at": datetime.now().isoformat(timespec="seconds"),
            })
            _lbt_save()
            _lbt_session.clear()
        session = dict(_lbt_session)
    return render_template("partials/liberty_status.html", session=session, error=None)

@app.route("/ui/liberty/log")
def ui_liberty_log():
    with _lbt_lock:
        log = list(_lbt_log)
    return render_template("partials/liberty_log.html", log=log)

@app.route("/ui/liberty/log/delete/<int:index>", methods=["POST"])
def ui_liberty_log_delete(index: int):
    global _lbt_log
    with _lbt_lock:
        if 0 <= index < len(_lbt_log):
            _lbt_log.pop(index)
            _lbt_save()
        log = list(_lbt_log)
    return render_template("partials/liberty_log.html", log=log)

@app.route("/operator/calibration")
def calibration():
    return render_template("calibration.html", page_title="Calibration")

@app.route("/operator/calibration/force-sensor")
def force_sensor():
    return render_template("force_sensor.html", page_title="Force Sensor")

@app.route("/operator/tcp-calibrate")
def tcp_calibrate_page():
    return render_template("tcp_calibrate.html", page_title="Tool Calibration")

def _tcp_render():
    with _tcp_lock:
        pts = set(_tcp_calib["points_recorded"])
        state = dict(_tcp_calib)
    return render_template(
        "partials/tcp_calibrate_steps.html",
        points_recorded=pts,
        drag_point=state["drag_point"],
        all_recorded=len(pts) == 4,
        drag_error=state["drag_error"],
        record_error=state["record_error"],
        apply_error=state["apply_error"],
        applied=state["applied"],
        tcp_offset=state["tcp_offset"] or [0.0] * 6,
    )

@app.route("/ui/tcp-calibrate/status")
def ui_tcp_calibrate_status():
    return _tcp_render()

@app.route("/ui/tcp-calibrate/enable-drag", methods=["POST"])
def ui_tcp_calibrate_enable_drag():
    point = int(request.form.get("point", 0))
    try:
        robot.tcp_enable_drag()
        with _tcp_lock:
            _tcp_calib["drag_point"] = point
            _tcp_calib["drag_error"] = None
    except Exception as e:
        with _tcp_lock:
            _tcp_calib["drag_point"] = None
            _tcp_calib["drag_error"] = str(e)
    return _tcp_render()

@app.route("/ui/tcp-calibrate/record-point", methods=["POST"])
def ui_tcp_calibrate_record_point():
    point = int(request.form.get("point", 0))
    try:
        robot.tcp_record_point(point)
        with _tcp_lock:
            _tcp_calib["points_recorded"].add(point)
            _tcp_calib["drag_point"] = None
            _tcp_calib["record_error"] = None
    except Exception as e:
        with _tcp_lock:
            _tcp_calib["drag_point"] = None
            _tcp_calib["record_error"] = str(e)
    return _tcp_render()

@app.route("/ui/tcp-calibrate/apply", methods=["POST"])
def ui_tcp_calibrate_apply():
    try:
        tcp_offset = robot.tcp_compute_and_apply()
        with _tcp_lock:
            _tcp_calib["applied"] = True
            _tcp_calib["tcp_offset"] = tcp_offset
            _tcp_calib["apply_error"] = None
    except Exception as e:
        with _tcp_lock:
            _tcp_calib["apply_error"] = str(e)
    return _tcp_render()

@app.route("/ui/tcp-calibrate/reset", methods=["POST"])
def ui_tcp_calibrate_reset():
    with _tcp_lock:
        _tcp_calib.update({
            "points_recorded": set(),
            "drag_point": None,
            "drag_error": None,
            "record_error": None,
            "apply_error": None,
            "applied": False,
            "tcp_offset": None,
        })
    return _tcp_render()

@app.route("/ui/ft/setup", methods=["POST"])
def ui_ft_setup():
    try:
        robot.ft_setup()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Initialize", payload=payload)

@app.route("/ui/ft/deactivate", methods=["POST"])
def ui_ft_deactivate():
    try:
        robot.ft_deactivate()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Deactivate", payload=payload)

@app.route("/ui/ft/zero", methods=["POST"])
def ui_ft_zero():
    try:
        robot.ft_zero()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Zero Sensor", payload=payload)

@app.route("/ui/ft/reading")
def ui_ft_reading():
    try:
        reading = robot.ft_read()
        return render_template("partials/ft_reading.html", ok=True, reading=reading)
    except Exception as e:
        return render_template("partials/ft_reading.html", ok=False, error=str(e))

@app.route("/ui/connection")
def ui_connection():
    try:
        status = robot.status()
        snapshot = {
            "online": status["connected"],
            "program_state": status["program_state"],
            "error": None,
        }
    except Exception as e:
        snapshot = {"online": False, "program_state": "stopped", "error": str(e)}
    return render_template("partials/connection_chips.html", snapshot=snapshot)

@app.route("/operator/robot-diagnostics")
def robot_diagnostics_page():
    return render_template("robot_diagnostics.html", page_title="Robot Diagnostics",
                           status_interval_ms=int(os.getenv("WELDFLEX_STATUS_INTERVAL_MS", "1000")))

@app.route("/ui/diagnostics")
def ui_diagnostics():
    robot_ip = os.getenv("WELDFLEX_ROBOT_IP", "192.168.58.2")
    controller_host = os.getenv("WELDFLEX_CONTROLLER_HOST", "192.168.58.2")
    program_path = os.getenv("WELDFLEX_PROGRAM_PATH", "/fruser/")
    try:
        status = robot.diagnostics()
        snapshot = {
            "online": status["connected"],
            "robot_ip": robot_ip,
            "controller_host": controller_host,
            "program_path": program_path,
            "error": None,
        }
        ok = True
    except Exception as e:
        status = {
            "connected": False,
            "program_state": "unknown",
            "program_state_error": -1,
            "current_line": None,
            "current_line_error": -1,
            "fault_codes": {
                "main_code": None, "sub_code": None,
                "safety_code": None, "safety_error": -1,
                "robot_error_error": -1,
            },
        }
        snapshot = {
            "online": False,
            "robot_ip": robot_ip,
            "controller_host": controller_host,
            "program_path": program_path,
            "error": str(e),
        }
        ok = False
    return render_template("partials/diagnostics_readout.html",
                           ok=ok, status=status, snapshot=snapshot,
                           uptime=robot.uptime())

@app.route("/ui/diagnostics/reset-errors", methods=["POST"])
def ui_diagnostics_reset_errors():
    try:
        robot.reset_errors()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Reset Errors", payload=payload)

@app.route("/ui/diagnostics/reconnect", methods=["POST"])
def ui_diagnostics_reconnect():
    try:
        robot.reconnect()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Reconnect", payload=payload)

@app.route("/ui/stop", methods=["POST"])
def ui_stop():
    try:
        robot.stop_program()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Stop", payload=payload)


_COMMON_TIMEZONES = [
    "UTC",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Anchorage", "America/Honolulu", "America/Phoenix",
    "Europe/London", "Europe/Paris", "Europe/Berlin",
    "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata",
    "Australia/Sydney",
]

def _get_sys_info() -> dict:
    info = {
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "timezone": "UTC",
        "ntp_enabled": False,
        "ntp_synced": False,
        "ssid": "—",
        "ip_address": "—",
    }
    try:
        info["ip_address"] = socket.gethostbyname(socket.gethostname())
    except Exception:
        pass
    if sys.platform != "win32":
        try:
            td_out = subprocess.check_output(
                ["timedatectl", "show", "--no-pager"], stderr=subprocess.DEVNULL
            ).decode()
            for line in td_out.splitlines():
                k, _, v = line.partition("=")
                if k == "Timezone":
                    info["timezone"] = v.strip()
                elif k == "NTP" and v.strip() == "yes":
                    info["ntp_enabled"] = True
                elif k == "NTPSynchronized" and v.strip() == "yes":
                    info["ntp_synced"] = True
        except Exception:
            pass
        try:
            ssid = subprocess.check_output(
                ["iwgetid", "-r"], stderr=subprocess.DEVNULL
            ).decode().strip()
            if ssid:
                info["ssid"] = ssid
        except Exception:
            pass
    return info

@app.route("/operator/settings")
def settings():
    return render_template("settings.html", page_title="Settings",
                           sys_info=_get_sys_info(),
                           common_timezones=_COMMON_TIMEZONES)

@app.route("/manager")
def manager():
    return render_template("manager.html", page_title="Manager")

@app.route("/ui/manager/part-designer")
def ui_manager_part_designer():
    return render_template("partials/mgr_part_designer.html")

@app.route("/ui/manager/settings")
def ui_manager_settings():
    return render_template("partials/mgr_settings.html")

@app.route("/ui/settings/ntp", methods=["POST"])
def ui_settings_ntp():
    try:
        if sys.platform != "win32":
            subprocess.check_call(["timedatectl", "set-ntp", "true"])
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Enable NTP", payload=payload)

@app.route("/ui/settings/timezone", methods=["POST"])
def ui_settings_timezone():
    tz = request.form.get("timezone", "").strip()
    try:
        if sys.platform != "win32" and tz:
            subprocess.check_call(["timedatectl", "set-timezone", tz])
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Set Timezone", payload=payload)

@app.route("/ui/settings/wifi/scan")
def ui_settings_wifi_scan():
    networks = []
    if sys.platform != "win32":
        try:
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                stderr=subprocess.DEVNULL
            ).decode()
            seen: set = set()
            for line in out.splitlines():
                parts = line.split(":")
                ssid = parts[0].strip()
                if ssid and ssid not in seen:
                    seen.add(ssid)
                    networks.append({
                        "ssid": ssid,
                        "signal": parts[1].strip() if len(parts) > 1 else "?",
                        "security": parts[2].strip() if len(parts) > 2 else "?",
                    })
        except Exception:
            pass
    return render_template("partials/wifi_scan.html", networks=networks)

@app.route("/ui/settings/wifi", methods=["POST"])
def ui_settings_wifi():
    ssid = (request.form.get("ssid_manual") or request.form.get("ssid", "")).strip()
    password = request.form.get("wifi_password", "").strip()
    try:
        if sys.platform != "win32" and ssid:
            cmd = ["nmcli", "dev", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            subprocess.check_call(cmd)
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Connect WiFi", payload=payload)

@app.route("/ui/settings/system/reboot", methods=["POST"])
def ui_settings_reboot():
    try:
        if sys.platform != "win32":
            subprocess.Popen(["sudo", "reboot"])
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Reboot", payload=payload)

@app.route("/ui/settings/system/shutdown", methods=["POST"])
def ui_settings_shutdown():
    try:
        if sys.platform != "win32":
            subprocess.Popen(["sudo", "shutdown", "-h", "now"])
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Shutdown", payload=payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
