import json
import os
import subprocess
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
@app.route("/operator")
def operator():
    return render_template("operator.html", page_title="Operator")

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
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Run", payload=payload)

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

@app.route("/ui/ft/activate", methods=["POST"])
def ui_ft_activate():
    try:
        robot.ft_activate(1)
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Activate", payload=payload)

@app.route("/ui/ft/deactivate", methods=["POST"])
def ui_ft_deactivate():
    try:
        robot.ft_activate(0)
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
