import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from flask import Flask, jsonify, make_response, redirect, render_template, request
from markupsafe import Markup
import frame_8083 as f8
from job_manager import JobError, JobManager
from lua_builder import (
    GATE_MODES,
    IO_MONITOR_DEFAULT_MS,
    IO_MONITOR_PATH,
    IO_MONITOR_PROGRAM_NAME,
    WELD_PATH,
    WELD_PROGRAM_NAME,
    build_io_monitor_lua,
    format_number,
    strip_lua_comments,
    _parse_pressure,
)
from robot_service import STATE_MAP as ROBOT_STATE_MAP, WeldFlexRobotService

# stderr, which systemd hands to journald. The unit already sets PYTHONUNBUFFERED=1,
# so `journalctl -u weldflex-backend -f` shows the trail live.
logging.basicConfig(
    level=os.getenv("WELDFLEX_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    stream=sys.stderr,
)

app = Flask(__name__, template_folder="templates", static_folder="static")

ROBOT_IP = os.getenv("WELDFLEX_ROBOT_IP", "192.168.58.2")
KIOSK_MODE = os.getenv("WELDFLEX_KIOSK", "0") == "1"
PORT = int(os.getenv("PORT", "5000"))
# Bring-up default. "di" is the production target but its gate input is not wired
# or commissioned yet; "pause" needs no wiring and only already-working verbs.
GATE_MODE = os.getenv("WELDFLEX_GATE_MODE", "pause")
if GATE_MODE not in GATE_MODES:
    GATE_MODE = "pause"
robot = WeldFlexRobotService(robot_ip=ROBOT_IP)

# Hold the connection from process start, independent of any browser. Before this the
# page's 1s poll was the de-facto keepalive, so closing the last tab meant nothing ever
# retried a dropped link.
robot.start()


def _shutdown_robot(*_args) -> None:
    """Release the controller cleanly. Safe to call more than once."""
    try:
        job.shutdown()
    except Exception:
        pass
    try:
        robot.shutdown(timeout=5.0)
    except Exception:
        pass


atexit.register(_shutdown_robot)


def _install_signal_handlers() -> None:
    """Close the RPC session on SIGTERM/SIGINT.

    systemd sends SIGTERM on restart; without this the process dies with the session
    open and the controller is slow to release it, which shows up as a flaky reconnect
    on the next start. Only valid on the main thread, so guard for the reloader case.
    """
    def handler(signum, frame):
        _shutdown_robot()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


_install_signal_handlers()

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

_RECIPES_PATH = os.path.join(os.path.dirname(__file__), 'recipes.json')
_rec_lock = threading.Lock()

def _recipes_load():
    try:
        with open(_RECIPES_PATH) as f:
            recipes = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    migrated = False
    for r in recipes:
        if not r.get('id'):
            r['id'] = str(uuid.uuid4())
            migrated = True
    if migrated:
        _recipes_save(recipes)
    return recipes

def _recipes_save(recipes):
    """Write via a temp file + os.replace so the library survives a mid-write crash.

    This used to truncate and rewrite in place, which was tolerable when it only ran
    on operator edits. It now also runs at the end of every production job, on a
    kiosk that gets powered off at the wall.
    """
    tmp_path = f"{_RECIPES_PATH}.tmp"
    with open(tmp_path, 'w') as f:
        json.dump(recipes, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, _RECIPES_PATH)

def _recipes_enrich(recipes):
    result = []
    for r in recipes:
        studs = r.get('studs', [])
        ts = r.get('updated_at') or r.get('created_at', '')
        try:
            label = datetime.fromisoformat(ts).strftime('%b %d, %Y')
        except Exception:
            label = 'Never'
        result.append({
            **r,
            'studs_count': len(studs),
            'updated_label': label,
            'safe_z': float(r.get('safe_z', 10.0)),
            'part_z': float(r.get('part_z', 0.0)),
            'stud_type': r.get('stud_type') or 'M4',
            'substrate': r.get('substrate') or 'Mild Steel',
            'pressure_setting': _parse_pressure(r.get('pressure_setting')),
        })
    return result

def _parse_studs(text):
    """Parse 'x,y\\nx,y' text → list of {x, y} dicts. Returns (list, error|None)."""
    studs = []
    for line in (text or '').splitlines():
        line = line.strip().rstrip(',')
        if not line:
            continue
        vals = line.split(',', 1)
        if len(vals) != 2:
            return [], f'Invalid line: {line!r}'
        try:
            studs.append({'x': float(vals[0]), 'y': float(vals[1])})
        except ValueError:
            return [], f'Non-numeric value: {line!r}'
    return studs, None

def _preview_data(studs):
    BED = 508.0
    return {
        'graph_points': [
            {'x_plot': round((BED - s['x']) / BED * 200, 2),
             'y_plot': round((BED - s['y']) / BED * 200, 2),
             'index': i + 1}
            for i, s in enumerate(studs)
        ]
    }

def _on_job_finish(record: dict) -> None:
    """Fold a finished run into the part's lifetime stats.

    `times_ran` / `avg_cycle_time` / `last_run` have existed in recipes.json since
    parts were introduced but were written once at creation and never updated.
    """
    part_id = record.get("part_id")
    if not part_id:
        return
    cycles_done = int(record.get("cycles_done", 0) or 0)
    cycle_times = [float(t) for t in record.get("cycle_times") or []]
    with _rec_lock:
        recipes = _recipes_load()
        recipe = next((r for r in recipes if r.get("id") == part_id), None)
        if recipe is None:
            return
        prior_runs = int(recipe.get("times_ran", 0) or 0)
        prior_avg = recipe.get("avg_cycle_time")
        recipe["times_ran"] = prior_runs + cycles_done
        if cycle_times:
            # Running mean over every cycle this part has ever produced, not just
            # this job's.
            prior_total = float(prior_avg) * prior_runs if prior_avg else 0.0
            prior_n = prior_runs if prior_avg else 0
            count = prior_n + len(cycle_times)
            recipe["avg_cycle_time"] = round((prior_total + sum(cycle_times)) / count, 2)
        recipe["last_run"] = record.get("ended_at")
        _recipes_save(recipes)


job = JobManager(robot, on_finish=_on_job_finish, state_map=ROBOT_STATE_MAP)

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
    "bot":              '<path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    "layout_dashboard": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "menu":             '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/>',
    "plus":             '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "plus_circle":      '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/>',
    "image":            '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "mouse_pointer_2":  '<path d="M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z"/>',
    "x":                '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "refresh_cw":       '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "shield_alert":     '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    # current_job.html has always asked for these three; icon_safe() degrades an
    # unregistered name to a plain circle without complaining, so they rendered as
    # dots rather than icons.
    "clipboard":        '<rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    "repeat":           '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "wifi":             '<path d="M5 13a10 10 0 0 1 14 0"/><path d="M8.5 16.5a5 5 0 0 1 7 0"/><path d="M2 8.82a15 15 0 0 1 20 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>',
    "bar_chart_2":      '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "zap":              '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
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

@app.route("/manager")
def manager_root():
    return redirect("/manager/part-designer")

@app.route("/manager/part-designer")
def manager_part_designer_page():
    return render_template("manager.html", page_title="Part Designer", active_tab="part-designer")

@app.route("/manager/settings")
def manager_settings_page():
    return render_template("manager.html", page_title="Settings", active_tab="settings")

@app.route("/manager/reports")
def manager_reports_page():
    return render_template("manager.html", page_title="Reports", active_tab="reports")

@app.route("/operator/admin")
def admin():
    settings = {
        # The live target, not the last-saved .env value — these can differ.
        "robot_ip": robot.robot_ip,
        "controller_host": os.getenv("WELDFLEX_CONTROLLER_HOST", robot.robot_ip),
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

        payload = {}
        # Retarget the live connection too. Writing .env alone used to leave the running
        # process talking to the old address until someone restarted it.
        new_ip = request.form.get("robot_ip", "").strip()
        if new_ip:
            if robot.set_robot_ip(new_ip):
                payload["reconnecting_to"] = new_ip
            os.environ["WELDFLEX_ROBOT_IP"] = robot.robot_ip
        for field in fields:
            val = request.form.get(field, "").strip()
            if val:
                os.environ[env_keys[field]] = val
        ok = True
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Save Settings", payload=payload)

@app.route("/operator/parts")
def parts():
    recipe_name = request.args.get('recipe_name', None)
    with _rec_lock:
        all_recipes = _recipes_load()
    enriched = _recipes_enrich(all_recipes)
    studs_text = ''
    recipe = None
    if recipe_name is not None:
        match = next((r for r in enriched if r['name'] == recipe_name), None)
        if match:
            recipe = match
            raw = match.get('studs', '')
            if isinstance(raw, list):
                studs_text = '\n'.join(f"{s['x']},{s['y']}" for s in raw)
            else:
                studs_text = raw
    return render_template('parts.html',
                           page_title='Parts',
                           recipes=enriched,
                           recipe=recipe,
                           recipe_name=recipe_name,
                           studs_text=studs_text)

@app.route('/ui/recipes/save', methods=['POST'])
def ui_recipes_save():
    name      = (request.form.get('recipe_name') or '').strip()
    recipe_id = (request.form.get('recipe_id')   or '').strip()
    if not name:
        return render_template('partials/command_result.html', ok=False,
                               title='Save Recipe', payload={'error': 'Recipe name is required'})

    try:
        safe_z = float(request.form.get('safe_z') or 10.0)
    except ValueError:
        safe_z = 10.0

    try:
        part_z = float(request.form.get('part_z') or 0.0)
    except ValueError:
        part_z = 0.0

    stud_type = (request.form.get('stud_type') or 'M4').strip()
    substrate = (request.form.get('substrate') or 'Mild Steel').strip()
    try:
        pressure_setting = float(request.form.get('pressure_setting') or 20.0)
    except (ValueError, TypeError):
        pressure_setting = _parse_pressure(request.form.get('pressure_setting'))

    studs_json = (request.form.get('studs_json') or '').strip()
    studs_text = (request.form.get('studs_text') or '').strip()
    if studs_json:
        try:
            studs = json.loads(studs_json)
        except (json.JSONDecodeError, ValueError):
            studs = []
    elif studs_text:
        studs, _ = _parse_studs(studs_text)
    else:
        studs = []
    with _rec_lock:
        recipes = _recipes_load()
        if recipe_id:
            existing = next((r for r in recipes if r.get('id') == recipe_id), None)
        else:
            existing = next((r for r in recipes if r['name'] == name), None)
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            existing['name']             = name
            existing['studs']            = studs
            existing['safe_z']           = safe_z
            existing['part_z']           = part_z
            existing['stud_type']        = stud_type
            existing['substrate']       = substrate
            existing['pressure_setting'] = pressure_setting
            existing['updated_at']       = now
            saved_id = existing['id']
        else:
            saved_id = str(uuid.uuid4())
            recipes.append({
                'id': saved_id,
                'name': name,
                'studs': studs,
                'safe_z': safe_z,
                'part_z': part_z,
                'stud_type': stud_type,
                'substrate': substrate,
                'pressure_setting': pressure_setting,
                'created_at': now,
                'updated_at': now,
                'times_ran': 0,
                'avg_cycle_time': None,
                'last_run': None,
                'pause_points': [],
            })
        _recipes_save(recipes)
    resp = make_response(render_template('partials/command_result.html', ok=True,
                                         title='Save Recipe', payload={'name': name}))
    resp.headers['X-Recipe-Id'] = saved_id
    return resp

@app.route('/ui/parts/delete', methods=['POST'])
def ui_parts_delete():
    recipe_id = (request.form.get('recipe_id')   or '').strip()
    name      = (request.form.get('recipe_name') or '').strip()
    if not recipe_id and not name:
        return render_template('partials/command_result.html', ok=False,
                               title='Delete Recipe', payload={'error': 'No recipe identifier'})
    with _rec_lock:
        recipes = _recipes_load()
        before = len(recipes)
        if recipe_id:
            recipes = [r for r in recipes if r.get('id') != recipe_id]
        else:
            recipes = [r for r in recipes if r['name'] != name]
        _recipes_save(recipes)
    deleted = len(recipes) < before
    resp = make_response(render_template(
        'partials/command_result.html', ok=deleted, title='Delete Recipe',
        payload={} if deleted else {'error': 'Recipe not found'}))
    if deleted:
        resp.headers['HX-Redirect'] = '/operator/parts'
    return resp

@app.route('/ui/studs-preview')
def ui_studs_preview():
    text = request.args.get('studs_text', '')
    studs, err = _parse_studs(text)
    if err:
        return render_template('partials/studs_preview.html', ok=False, preview={'error': err})
    return render_template('partials/studs_preview.html', ok=True, preview=_preview_data(studs))

@app.route('/ui/manager/parts-list')
def ui_manager_parts_list():
    with _rec_lock:
        recipes = _recipes_load()
    return jsonify(_recipes_enrich(recipes))

@app.route('/ui/manager/part-points')
def ui_manager_part_points():
    recipe_id = request.args.get('id',   '').strip()
    name      = request.args.get('name', '').strip()
    with _rec_lock:
        recipes = _recipes_load()
    enriched = _recipes_enrich(recipes)
    if recipe_id:
        recipe = next((r for r in enriched if r.get('id') == recipe_id), None)
    else:
        recipe = next((r for r in enriched if r['name'] == name), None)
    if not recipe:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    return jsonify({
        'ok': True,
        'points': recipe.get('studs', []),
        'recipe': recipe
    })

@app.context_processor
def inject_defaults():
    return {
        "kiosk_mode": KIOSK_MODE,
        "git_sha": GIT_SHA,
        # Server-rendered first paint, before htmx takes over the polling. Reads the
        # same snapshot the /ui/connection partial does, so the chips don't flash a
        # placeholder state on every page load.
        "init_connection_snapshot": _connection_snapshot(),
    }

# ---------------------------------------------------------------------------
# Job run manager — /ui/job/<action>
#
# These routes are thin adapters over `job` (backend/job_manager.py). All job
# state, cycle counting and history live there; nothing about a run is kept in
# this module. A command that is illegal from the current state raises JobError
# and is shown inline in the panel rather than as a toast.
# ---------------------------------------------------------------------------

def _job_panel(snap=None, note=None):
    """Render the current-job panel from an immutable snapshot."""
    return render_template(
        "partials/current_job.html",
        job=(job.snapshot() if snap is None else snap),
        conn=_connection_snapshot(),
        note=note,
    )


def _job_command(fn):
    """Run a manager command and re-render the panel, illegal transitions included."""
    try:
        return _job_panel(fn())
    except JobError as exc:
        return _job_panel(note=str(exc))


@app.route("/ui/job/load", methods=["POST"])
def ui_job_load():
    """Queue a part from the parts page. JSON in/out — the caller redirects."""
    part_id = (request.form.get("recipe_id") or request.form.get("part_id") or "").strip()
    try:
        cycles = max(1, int(request.form.get("cycles", "1")))
    except ValueError:
        cycles = 1
    gate_mode = (request.form.get("gate_mode") or GATE_MODE).strip()
    with _rec_lock:
        recipes = _recipes_load()
    enriched = _recipes_enrich(recipes)
    recipe = next((r for r in enriched if r.get("id") == part_id), None)
    if not recipe:
        return jsonify({"ok": False, "error": "Part not found"}), 404
    try:
        job.load(
            part_id,
            recipe["name"],
            recipe.get("studs", []),
            cycles,
            gate_mode=gate_mode,
            safe_z=recipe.get("safe_z", 10.0),
            part_z=recipe.get("part_z", 0.0),
            pressure_setting=recipe.get("pressure_setting", "high"),
            stud_type=recipe.get("stud_type", "M4"),
            substrate=recipe.get("substrate", "Mild Steel"),
        )
    except JobError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True})


@app.route("/ui/job/start", methods=["POST"])
def ui_job_start():
    return _job_command(job.start)


@app.route("/ui/job/pause", methods=["POST"])
def ui_job_pause():
    return _job_command(job.pause)


@app.route("/ui/job/resume", methods=["POST"])
def ui_job_resume():
    return _job_command(job.resume)


@app.route("/ui/job/continue", methods=["POST"])
def ui_job_continue():
    return _job_command(job.continue_)


@app.route("/ui/job/stop", methods=["POST"])
def ui_job_stop():
    return _job_command(job.stop)


@app.route("/ui/job/clear", methods=["POST"])
def ui_job_clear():
    return _job_command(job.clear)


@app.route("/ui/job/status")
def ui_job_status():
    """Read-only. The monitor thread advances the job; this only reports it."""
    return _job_panel()


@app.route("/ui/job/history")
def ui_job_history():
    attempted, completed = job.today_stats()
    return render_template(
        "partials/job_history.html",
        runs=job.history(limit=20),
        today_attempted=attempted,
        today_completed=completed,
    )


@app.route("/operator/job-history")
def job_history_page():
    return render_template("job_history.html", page_title="Run History")

@app.route("/operator/faceplate")
def faceplate_page():
    return render_template("faceplate.html", page_title="Faceplate")

@app.route("/operator/calibration")
def calibration():
    return render_template("calibration.html", page_title="Calibration")

@app.route("/operator/jog")
def jog_page():
    return render_template("jog.html", page_title="Jog",
                           status_interval_ms=int(os.getenv("WELDFLEX_STATUS_INTERVAL_MS", "1000")))

@app.route("/ui/jog/status")
def ui_jog_status():
    try:
        pose = robot.jog_pose()
        labels = [f"{v:.2f}" for v in pose]
    except Exception:
        labels = ["—"] * 6
    return render_template("partials/jog_status.html", snapshot={"tcp_pose_labels": labels})

@app.route("/ui/jog/move", methods=["POST"])
def ui_jog_move():
    try:
        robot.jog_step(
            mode=request.form.get("mode", "cartesian"),
            frame=request.form.get("cartesian_frame", "base"),
            axis=request.form.get("axis", ""),
            direction=request.form.get("direction", ""),
            step=float(request.form.get("cartesian_step") or 5),
            vel=float(request.form.get("jog_velocity") or 20),
        )
        return ("", 204)
    except Exception as e:
        return (str(e), 500)

@app.route("/ui/jog/stop", methods=["POST"])
def ui_jog_stop():
    try:
        robot.jog_stop()
    except Exception:
        pass
    return ("", 204)

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

# XJC X-6A-XD80-H28, 200 N / 5 N·m variant — force-axis full scale.
# Used only to give the readout a sense of scale; it does not limit anything.
FT_FZ_FULL_SCALE_N = 200.0
FT_WARN_FRAC = 0.70
FT_CRIT_FRAC = 0.90
N_TO_LBF = 0.2248089
# Tool-frame Z points out of the flange, so pressing the tool against the work
# reads as a negative Fz reaction (observed live 2026-07-28). Operators expect
# "pressing = positive", so the readout flips it. Display-only — ft_read()
# still returns the sensor's native sign.
FT_FZ_DISPLAY_SIGN = -1.0


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
        ustate = robot.get_universal_state()
        reading = robot.ft_read()
        fz = (ustate.fz_lbf / N_TO_LBF) if ustate.fz_lbf is not None else (reading["fz"] * FT_FZ_DISPLAY_SIGN)
        frac = min(abs(fz) / FT_FZ_FULL_SCALE_N, 1.0)
        if frac >= FT_CRIT_FRAC:
            level = "crit"
        elif frac >= FT_WARN_FRAC:
            level = "warn"
        else:
            level = "ok"
        return render_template(
            "partials/ft_reading.html", ok=True, active=reading["active"],
            lbf=fz * N_TO_LBF, fz_n=fz, pct=round(frac * 100), level=level,
            sign="pos" if fz >= 0 else "neg", source=reading["source"],
            age_s=reading["age_s"],
        )
    except Exception as e:
        # Polled 3x/sec — render the same shape with placeholder values so a
        # transient failure doesn't collapse the layout out from under the operator.
        return render_template(
            "partials/ft_reading.html", ok=False, error=str(e), active=False,
            lbf=None, fz_n=None, pct=0, level="ok", sign="pos", source=None,
            age_s=None,
        )

# ---------------------------------------------------------------------------
# Weld test — one stud, one pass of programs/weld.lua.
#
# A bring-up tool, not part of the operator's production path. weld.lua is a
# sub-process that WeldFlex.lua does not call yet (see docs/ARCHITECTURE.md), so
# this section is the only way it gets exercised on hardware in the meantime. It
# uploads weld.lua alongside a generated single-point harness and runs the
# harness; weld.lua on its own would just fault on its input contract.
#
# The controller gives Python no way to read a Lua print() — there is no such
# call anywhere in the SDK — so "feedback" here is what can genuinely be observed
# over RPC: program state, the executing line, contact force, and the
# stud-on-work input. The page turns those samples into a trace.
# ---------------------------------------------------------------------------

WELD_TEST_POLL_MS = int(os.getenv("WELDFLEX_WELD_TEST_POLL_MS", "400"))
# Idle sampling still costs robot I/O, so back off hard when nothing is running.
WELD_TEST_IDLE_POLL_MS = int(os.getenv("WELDFLEX_WELD_TEST_IDLE_POLL_MS", "1200"))
WELD_TEST_STARTUP_GRACE_S = 3.0
# The two weld interlock inputs, and they are not interchangeable:
#   DI1 — stud on work: continuity welder -> work surface -> gun.
#   DI0 — ready / caps at charge: the welder itself is able to fire.
# These must match DI_STUD_ON_WORK and DI_WELD_READY in programs/weld.lua. Nothing
# enforces that across the language boundary, so they are changed together;
# tests/test_lua_builder.py asserts the pair still agrees.
WELD_STUD_DI = int(os.getenv("WELDFLEX_WELD_STUD_DI", "1"))
WELD_READY_DI = int(os.getenv("WELDFLEX_WELD_READY_DI", "0"))
# The controller answers FT_GetForceTorqueRCS with 14 ("interface execution
# failed") the whole time a force-control move — weld.lua's FT_FindSurface —
# owns the sensor (observed live 2026-07-28). While a program is running that
# is expected, not a fault; the page shows it as "sensor busy". The same code
# also appears when a latched controller fault blocks all reads, but that case
# surfaces through fault_main, which outranks the busy note in the template.
FT_RPC_BUSY_CODE = 14

# Controller system variables weld.lua's pub() writes to. This is the only channel
# that reports from inside a running Lua program — see weld_probe. Hand-kept in
# sync with the SV_* constants in programs/weld.lua the same way the DI numbers
# above are; tests/test_lua_builder.py asserts the two sides still agree.
WELD_SV_PHASE = 1        # phase code
WELD_SV_LAST_RET = 2     # raw return of the last FT_* instruction
WELD_SV_PRESS_Z0 = 3     # base-frame tool Z at contact, mm
WELD_SV_PRESS_TRAVEL = 4 # travel the press achieved, mm (only set if it finished)
WELD_SV_PRESS_GUARD = 5  # which collision-threshold instruction the press got
WELD_SV_STUD_ON_WORK = 6 # DI1 level, published by weld.lua's controller-side GetDI
WELD_SV_WELD_READY = 7   # DI0 level, published by weld.lua's controller-side GetDI
WELD_SV_PRESS_LBF = 8    # press target this run used, lbf — the force-ladder rung

# weld.lua's PRESS_MAX_MM — the travel FT_LinInsertion is allowed past contact.
# Mirrored here so the page can show travel against its budget. That reading
# separates the two ways the press can fail: settling near the ~13 mm the chuck
# spring needs for 20 lbf means the regulator had real force to work with, while
# running all the way to the budget means it was driving blind and weld.lua's
# FTC_SENSOR_NUM is wrong. Changed together with programs/weld.lua.
#
# It is NOT what ends the run. Tripling this between the two 2026-07-29 press runs
# left the axis-3 collision fault landing in the same place, which is what ruled
# the travel abort out — see _WELD_GUARD_CODES.
WELD_PRESS_MAX_MM = 60.0
# How close to the budget counts as "hit the limit" on the page, in mm.
WELD_PRESS_LIMIT_EPS_MM = 1.0

# Mirrors the PH_* constants in programs/weld.lua. Nothing enforces that across
# the language boundary, so the two tables are changed together.
_WELD_PHASES = {
    10: "entered",
    20: "search: approaching",
    21: "search: contact",
    30: "press: force control on",
    31: "press: driving in",
    32: "press: holding",
    33: "press: held",
    40: "weld",
    50: "retracting",
    60: "done",
}

# weld.lua's fault codes are 90 + the beacon site, so they decode as a group.
_WELD_FAULT_BASE = 90
_WELD_FAULT_SITES = {
    1: "FT_FindSurface refused the approach",
    4: "DI1 (stud on work) not active",
    5: "FT_LinInsertion refused the press",
    9: "FT_Control unavailable or refused",
    10: "DI1 dropped before the weld pulse",
    11: "DI0 (caps at charge) never came up",
}

# weld.lua cannot write nil to a numeric system variable, so it encodes the
# return value it most wants to report. See RET_NIL / RET_NON_NUM there.
#
# A value shown here is NOT necessarily an error. FAIRINO's error-code table (SDK
# manual 2.5) is -7..-1, 0 = success, then 3..207 — it contains no 1 and no 2. So
# the 1 this firmware returns from FT_FindSurface after a successful search is
# outside the error space entirely; weld.lua's ftRefused() only faults on
# negatives and values >= 3, and that is the rule to keep the two sides agreeing.
_WELD_RET_CODES = {
    -999: "nil (no return value)",
    -998: "non-numeric",
    1: "1 (contact, not an error)",
}

# Mirrors the GUARD_* constants in programs/weld.lua's pressCollisionGuard().
#
# The controller scores a collision as a force on a 0-100 N scale (FR Lua manual
# 3.3.9: "1~100% corresponds to 0~100N"), and weld.lua presses to 88.96 N — 89% of
# it, held. So a correct 20 lbf press and a crash are the same signal to the
# joint-torque monitor, which is what killed both 2026-07-29 runs on a resettable
# axis-3 fault. weld.lua now widens the monitor for the press window only, and
# publishes here which instruction actually took, because neither has ever been
# called on this cell. If the press faults again this is the first thing to read:
# "not applied" means the fix never ran, anything else means it ran and was not
# enough.
_WELD_GUARD_CODES = {
    0: "released",
    1: "custom thresholds",
    2: "custom + level",
    3: "level only",
    4: "not needed at this force",
    9: "not applied",
}


_WELD_GUARD_NOT_APPLIED = 9

# The codes that mean collision detection was genuinely modified for the press.
# Everything outside this set left the monitor at whatever the pendant configured.
_WELD_GUARD_APPLIED_CODES = frozenset({1, 2, 3})


def _weld_guard_state(value: float | None) -> dict | None:
    """Which collision-threshold instruction weld.lua got for the press.

    `applied` is carried separately from the label so the page can call out the
    one reading that means the fix never ran, rather than the template having to
    match on wording.
    """
    if value is None:
        return None
    code = int(value)
    return {
        "label": _WELD_GUARD_CODES.get(code, f"unknown ({code})"),
        # True only when collision detection was actually changed. Code 4 reports
        # a guard that declined — the press target was below weld.lua's
        # PRESS_GUARD_MIN_N, so detection was deliberately left alone. That is a
        # different answer from 9 ("the instruction does not exist"), but it is
        # equally not a guard that ran, and a faulted press at code 4 is telling
        # you the rung was low enough that the monitor was never the suspect.
        "applied": code in _WELD_GUARD_APPLIED_CODES,
    }


def _weld_phase_label(code: float | None) -> str | None:
    """Human label for weld.lua's phase code, or None if nothing has published."""
    if code is None:
        return None
    code = int(code)
    if code >= _WELD_FAULT_BASE:
        site = code - _WELD_FAULT_BASE
        return f"FAULT: {_WELD_FAULT_SITES.get(site, f'site {site}')}"
    return _WELD_PHASES.get(code, f"unknown ({code})")


def _weld_ret_label(value: float | None) -> str | None:
    """Human label for the last FT_* return value weld.lua published.

    Worth showing on its own row: the FR Lua manual documents every FT_*
    instruction as returning null, but this file's history claims a live run saw
    a success code. Whichever this shows is the answer.
    """
    if value is None:
        return None
    code = int(value)
    return _WELD_RET_CODES.get(code, str(code))


def _weld_di_level(value: float | None) -> int | None:
    """A controller-published DI level, with -1/unset kept visibly unknown."""
    if value in (0, 1):
        return int(value)
    return None


def _weld_press_travel(z0: float | None, final: float | None,
                       tcp_z: float | None) -> dict | None:
    """How far the press has driven since contact, against its budget.

    weld.lua cannot read force at all — controller Lua has no force-read
    instruction — so travel against the known ~1.5 lbf/mm chuck spring is the
    available proxy for what the press is doing while RPC force reads are refused.
    A press that settles near 13 mm was regulating on real force; one that runs to
    the budget was driving blind, which points at weld.lua's FTC_SENSOR_NUM rather
    than at anything here.

    Two sources, in order:
      * `final` (s_var_4) is the travel weld.lua measured itself once
        FT_LinInsertion returned. Authoritative, but it only exists if the
        insertion COMPLETED — an aborted press never publishes it.
      * otherwise, `z0` (s_var_3, published before the press began) differenced
        against the tool Z sampled this tick. Live during the press, and still
        correct after the program was killed mid-insertion, which is exactly the
        case worth diagnosing.

    Both are base-frame Z deltas, not tool-frame insertion distance. They are the
    same number here only because this cell presses along base -Z; the template
    labels it as what it is.

    weld.lua zeroes both slots at the top of each stud, so 0.0 means "no press
    yet this run" rather than a stale reading — hence z0 == 0 is treated as unset.
    """
    if final:
        travel, live = float(final), False
    elif z0 and tcp_z is not None:
        travel, live = float(z0) - float(tcp_z), True
    else:
        return None
    return {
        "mm": travel,
        "max_mm": WELD_PRESS_MAX_MM,
        "live": live,
        "at_limit": travel >= WELD_PRESS_MAX_MM - WELD_PRESS_LIMIT_EPS_MM,
    }


_weld_test: dict = {
    "last_run": None,
    "armed": False,
    # Not 0,0 — the taught zerozero point itself is not a usable test target, and
    # an operator who taps Start on a freshly loaded page should get a move to
    # clear space rather than a press at the origin.
    "weld_x": 100.0,
    "weld_y": 100.0,
    # None = press at weld.lua's production 20 lbf. Remembered between runs so
    # walking the ladder up does not mean retyping the rung every time, but never
    # defaulted to a value — a blank field has to mean "full force" only because
    # that is what weld.lua does, not because this page prefilled it.
    "press_lbf": None,
    "error": None,
    "hint_on": False,
    "hint_started_ts": None,
}
_weld_test_lock = threading.Lock()


def _weld_test_toast(ok: bool, title: str, payload: dict):
    return render_template("partials/command_result.html", ok=ok, title=title, payload=payload)


# Idle: just the two interlock levels. Each slot is its own GetSysVarValue RPC
# inside the sample, and nothing else changes while no program is running.
_WELD_SV_IDLE = (WELD_SV_PHASE, WELD_SV_STUD_ON_WORK, WELD_SV_WELD_READY)
_WELD_SV_RUNNING = (
    WELD_SV_PHASE,
    WELD_SV_LAST_RET,
    WELD_SV_PRESS_Z0,
    WELD_SV_PRESS_TRAVEL,
    WELD_SV_PRESS_GUARD,
    WELD_SV_STUD_ON_WORK,
    WELD_SV_WELD_READY,
    WELD_SV_PRESS_LBF,
)


def _start_weld_telemetry(interval_ms: int, slots: tuple[int, ...] = _WELD_SV_IDLE) -> None:
    """Retime the sampler behind the weld-test readout.

    `slots` is widened to `_WELD_SV_RUNNING` while a test is actually running, so
    the press diagnostics cost robot I/O only for the seconds they are meaningful.
    The tables that decode them (`_WELD_PHASES`, `_WELD_GUARD_CODES` and their
    label helpers) mirror constants in programs/weld.lua, and a test asserts the
    two sides agree.
    """
    robot.start_weld_telemetry(
        WELD_STUD_DI,
        WELD_READY_DI,
        slots,
        interval_s=interval_ms / 1000.0,
    )





def _connection_snapshot() -> dict:
    """Shape the link snapshot for the connection chips.

    Pure cache read from UniversalRobotState.
    """
    ustate = robot.get_universal_state()
    snap = robot.snapshot()
    detail = None
    if ustate.state == "connected" and ustate.busy:
        detail = ustate.busy_label
    elif ustate.state == "connecting" and snap.attempts > 1:
        detail = f"attempt {snap.attempts}"
    elif ustate.state == "telemetry":
        detail = "no commands"
    elif ustate.state == "degraded":
        age = snap.age_s()
        detail = f"{age:.0f}s ago" if age is not None else "no reply"
    elif ustate.state == "disconnected":
        detail = "manual"

    return {
        "state": ustate.state,
        "online": ustate.connected,
        "busy": ustate.busy,
        "detail": detail,
        "program_state": ustate.program_state,
        "ip": snap.ip,
        "error": snap.last_error,
    }


@app.route("/ui/connection")
def ui_connection():
    return render_template("partials/connection_chips.html", snapshot=_connection_snapshot())


@app.route("/ui/connection/connect", methods=["POST"])
def ui_connection_connect():
    try:
        robot.connect()
        ok, payload = True, {"address": robot.robot_ip}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Connect", payload=payload)


@app.route("/ui/connection/disconnect", methods=["POST"])
def ui_connection_disconnect():
    try:
        robot.disconnect()
        ok, payload = True, {"note": "Reconnect is manual until Connect is pressed."}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Disconnect", payload=payload)

@app.route("/operator/robot-diagnostics")
def robot_diagnostics_page():
    return render_template("robot_diagnostics.html", page_title="Robot Diagnostics",
                           status_interval_ms=int(os.getenv("WELDFLEX_STATUS_INTERVAL_MS", "1000")))

@app.route("/ui/diagnostics")
def ui_diagnostics():
    # robot.robot_ip is the live target, which can differ from the .env value after a
    # settings change — show what we are actually talking to.
    controller_host = os.getenv("WELDFLEX_CONTROLLER_HOST", robot.robot_ip)
    program_path = os.getenv("WELDFLEX_PROGRAM_PATH", "/fruser/")
    status = robot.diagnostics()
    snapshot = {
        "online": status["connected"],
        "state": status["state"],
        "robot_ip": status["ip"],
        "controller_host": controller_host,
        "program_path": program_path,
        "error": status["last_error"],
    }
    return render_template("partials/diagnostics_readout.html",
                           ok=True, status=status, snapshot=snapshot,
                           uptime=robot.uptime())

def _feed_compare_rows(feed, rpc) -> list[dict]:
    """The four signals the 8083 feed is slated to take over, side by side.

    Only `robot.snapshot()` is used for the XML-RPC column — it is a pure cache
    read. `get_universal_state()` would be richer but calls `ft_read()`, which
    falls back to a real XML-RPC round trip whenever the force cache is stale,
    and adding a controller call to a once-a-second panel is the exact load this
    whole change exists to remove.
    """
    def norm_fault(value):
        # ConnSnapshot normalises "no fault" to None; the frame reports 0.
        return None if not value else value

    rows = [
        ("Program state",
         f8.PROGRAM_STATES.get(feed.program_state), ROBOT_STATE_MAP.get(rpc.program_state_raw)),
        ("Current line", feed.current_line, rpc.current_line),
        ("Fault main", norm_fault(feed.fault_main), norm_fault(rpc.fault_main)),
        ("Fault sub", norm_fault(feed.fault_sub), norm_fault(rpc.fault_sub)),
    ]
    out = []
    for label, feed_value, rpc_value in rows:
        # Only claim agreement when both sides actually reported something.
        both = feed_value is not None and rpc_value is not None
        out.append({
            "label": label,
            "feed": feed_value,
            "rpc": rpc_value,
            "agrees": (feed_value == rpc_value) if both else None,
        })
    return out


@app.route("/ui/diagnostics/feed")
def ui_diagnostics_feed():
    """Read-only view of the port-8083 status stream.

    State, line and fault codes now come from the feed; force and DI still do
    not. This panel is how the remaining signals get proven before they move —
    it shows every parsed field beside the XML-RPC value it would replace, plus
    the frame-integrity counters. Purely observational: it performs no robot I/O
    of its own, reading the feed's cached frame the same way every other panel
    reads cache. That is what makes it safe to poll during a live run, and what
    `tools/feed_continuity.py` relies on.
    """
    feed = robot.feed_snapshot()
    stats = robot.feed_stats()
    return render_template(
        "partials/feed_readout.html",
        feed=feed,
        stats=stats,
        rows=_feed_compare_rows(feed, robot.snapshot()),
        age_s=feed.age_s(),
        fresh=feed.is_fresh(),
        expected_len=f8.EXPECTED_DATA_LEN,
        mode_label=f8.ROBOT_MODES.get(feed.robot_mode),
        error_label=f8.ERROR_CODES.get(feed.get("error_code")),
        stud_di=WELD_STUD_DI,
        ready_di=WELD_READY_DI,
    )


@app.route("/ui/diagnostics/reset-errors", methods=["POST"])
def ui_diagnostics_reset_errors():
    try:
        robot.reset_errors()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Reset Errors", payload=payload)

@app.route("/ui/diagnostics/stop-program", methods=["POST"])
def ui_diagnostics_stop_program():
    """Raw controller stop, independent of whether the job manager owns the program.

    Prefers `job.stop()` so an active job is finalized rather than left reporting
    "running" against a stopped program; falls back to the bare verb when there is
    no job — a program can be started from the teach pendant too.
    """
    try:
        try:
            job.stop()
        except JobError:
            robot.stop_program()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Stop Program", payload=payload)

@app.route("/ui/diagnostics/reconnect", methods=["POST"])
def ui_diagnostics_reconnect():
    try:
        robot.reconnect()
        ok, payload = True, {}
    except Exception as e:
        ok, payload = False, {"error": str(e)}
    return render_template("partials/command_result.html", ok=ok, title="Reconnect", payload=payload)

@app.route("/operator/settings")
def settings():
    return render_template("settings.html", page_title="Settings")

@app.route("/manager")
def manager():
    return render_template("manager.html", page_title="Manager")

@app.route("/ui/manager/part-designer")
def ui_manager_part_designer():
    return render_template("partials/mgr_part_designer.html")

@app.route("/ui/manager/settings")
def ui_manager_settings():
    return render_template("partials/mgr_settings.html")

@app.route("/ui/manager/reports")
def ui_manager_reports():
    recipes = _recipes_enrich(_recipes_load())
    return render_template("partials/mgr_reports.html", recipes=recipes)

@app.route("/api/reports/summary")
def api_reports_summary():
    runs = job.history(limit=500)
    recipes = _recipes_enrich(_recipes_load())
    return jsonify({"runs": runs, "recipes": recipes})

@app.route("/api/reports/parts/<part_id>")
def api_reports_part(part_id):
    all_runs = job.history(limit=500)
    part_runs = [r for r in all_runs if r.get("part_id") == part_id]
    return jsonify({"part_id": part_id, "runs": part_runs})

@app.route("/api/reports/runs/<run_id>")
def api_reports_run(run_id):
    all_runs = job.history(limit=500)
    run_rec = next((r for r in all_runs if r.get("run_id") == run_id), None)
    events = job.events_for_run(run_id)
    return jsonify({"run_id": run_id, "run": run_rec, "events": events})

if __name__ == "__main__":
    # Werkzeug's dev server spawns an unbounded thread per request, so an unreachable
    # robot plus a few polling pages grows threads without limit on a 1-2GB Pi. waitress
    # caps them and reaps stuck clients. use_reloader stays off either way: a reloader
    # would give two processes, each with its own supervisor polling the robot.
    if os.getenv("WELDFLEX_DEV_SERVER") == "1":
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    else:
        from waitress import serve
        serve(app, host="0.0.0.0", port=PORT, threads=8, channel_timeout=30)
