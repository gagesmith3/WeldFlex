import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from flask import Flask, make_response, render_template, request, jsonify
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

_run_lock = threading.Lock()
_run_session: dict = {}
# Keys when active: state, recipe_id, recipe_name, cycles_target, cycles_done,
#                   program, started_at (ISO), launched_ts (epoch float)

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
    with open(_RECIPES_PATH, 'w') as f:
        json.dump(recipes, f, indent=2)

def _recipes_enrich(recipes):
    result = []
    for r in recipes:
        studs = r.get('studs', [])
        ts = r.get('updated_at') or r.get('created_at', '')
        try:
            label = datetime.fromisoformat(ts).strftime('%b %d, %Y')
        except Exception:
            label = 'Never'
        result.append({**r, 'studs_count': len(studs), 'updated_label': label})
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
    "bot":              '<path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    "layout_dashboard": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "menu":             '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/>',
    "plus":             '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "plus_circle":      '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/>',
    "image":            '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "mouse_pointer_2":  '<path d="M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z"/>',
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
    recipe_name = request.args.get('recipe_name', None)
    with _rec_lock:
        all_recipes = _recipes_load()
    studs_text = ''
    if recipe_name is not None:
        match = next((r for r in all_recipes if r['name'] == recipe_name), None)
        if match:
            raw = match.get('studs', '')
            if isinstance(raw, list):
                studs_text = '\n'.join(f"{s['x']},{s['y']}" for s in raw)
            else:
                studs_text = raw
    return render_template('parts.html',
                           page_title='Parts',
                           recipes=_recipes_enrich(all_recipes),
                           recipe_name=recipe_name,
                           studs_text=studs_text)

@app.route('/ui/recipes/save', methods=['POST'])
def ui_recipes_save():
    name      = (request.form.get('recipe_name') or '').strip()
    recipe_id = (request.form.get('recipe_id')   or '').strip()
    if not name:
        return render_template('partials/command_result.html', ok=False,
                               title='Save Recipe', payload={'error': 'Recipe name is required'})
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
            existing['name']       = name
            existing['studs']      = studs
            existing['updated_at'] = now
            saved_id = existing['id']
        else:
            saved_id = str(uuid.uuid4())
            recipes.append({
                'id': saved_id,
                'name': name,
                'studs': studs,
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
    if recipe_id:
        recipe = next((r for r in recipes if r.get('id') == recipe_id), None)
    else:
        recipe = next((r for r in recipes if r['name'] == name), None)
    if not recipe:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    return jsonify({'ok': True, 'points': recipe.get('studs', [])})

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

def _robot_status_safe():
    try:
        return robot.status()
    except Exception:
        return {"connected": False, "program_state": "unknown", "program_state_raw": None}

def _render_current_job():
    status = _robot_status_safe()
    with _run_lock:
        session = dict(_run_session)
    return render_template(
        "partials/current_job.html",
        session=session,
        robot_state=status.get("program_state", "unknown"),
        connected=status.get("connected", False),
    )

@app.route("/ui/parts/run", methods=["POST"])
def ui_parts_run():
    recipe_id = (request.form.get("recipe_id") or "").strip()
    try:
        cycles = max(1, int(request.form.get("cycles", "1")))
    except ValueError:
        cycles = 1
    with _rec_lock:
        recipes = _recipes_load()
    recipe = next((r for r in recipes if r.get("id") == recipe_id), None)
    if not recipe:
        from flask import jsonify
        return jsonify({"ok": False, "error": "Part not found"}), 404
    program = recipe.get("program", "WeldFlex.lua")
    now = datetime.now(timezone.utc).isoformat()
    with _run_lock:
        _run_session.clear()
        _run_session.update({
            "state": "queued",
            "recipe_id": recipe_id,
            "recipe_name": recipe["name"],
            "cycles_target": cycles,
            "cycles_done": 0,
            "program": program,
            "started_at": now,
            "launched_ts": 0,
        })
    _current_job["name"] = recipe["name"]
    _current_job["started_at"] = now
    from flask import jsonify
    return jsonify({"ok": True})

@app.route("/ui/operator/run", methods=["POST"])
def ui_operator_run():
    with _run_lock:
        session = dict(_run_session)
    if not session or session.get("state") not in ("queued", "error"):
        return _render_current_job()
    try:
        # Upload the generated studs data file before running the base program
        with _rec_lock:
            recipes = _recipes_load()
        recipe = next((r for r in recipes if r.get("id") == session.get("recipe_id")), None)
        studs = recipe.get("studs", []) if recipe else []
        robot.upload_studs_data(studs)
        robot.run_program(session["program"])
        with _run_lock:
            _run_session["state"] = "running"
            _run_session["launched_ts"] = time.time()
    except Exception as exc:
        with _run_lock:
            _run_session["state"] = "error"
            _run_session["error_msg"] = str(exc)
    return _render_current_job()

@app.route("/ui/pause", methods=["POST"])
def ui_pause():
    try:
        robot.pause_program()
        with _run_lock:
            if _run_session.get("state") == "running":
                _run_session["state"] = "paused"
    except Exception:
        pass
    return _render_current_job()

@app.route("/ui/resume", methods=["POST"])
def ui_resume():
    try:
        robot.resume_program()
        with _run_lock:
            if _run_session.get("state") == "paused":
                _run_session["state"] = "running"
    except Exception:
        pass
    return _render_current_job()

@app.route("/ui/operator/current-job")
def ui_operator_current_job():
    status = _robot_status_safe()
    robot_state = status.get("program_state", "unknown")
    with _run_lock:
        session = dict(_run_session)
    # Cycle advancement — piggybacks on polling
    if session.get("state") == "running" and robot_state == "stopped":
        age = time.time() - session.get("launched_ts", 0)
        if age > 10:
            cycles_done = session.get("cycles_done", 0) + 1
            cycles_target = session.get("cycles_target", 1)
            if cycles_done >= cycles_target:
                with _run_lock:
                    _run_session["state"] = "completed"
                    _run_session["cycles_done"] = cycles_done
            else:
                try:
                    with _rec_lock:
                        recipes = _recipes_load()
                    recipe = next((r for r in recipes if r.get("id") == session.get("recipe_id")), None)
                    studs = recipe.get("studs", []) if recipe else []
                    robot.upload_studs_data(studs)
                    robot.run_program(session["program"])
                    with _run_lock:
                        _run_session["cycles_done"] = cycles_done
                        _run_session["launched_ts"] = time.time()
                except Exception:
                    with _run_lock:
                        _run_session["state"] = "error"
                        _run_session["cycles_done"] = cycles_done
            with _run_lock:
                session = dict(_run_session)
    return render_template(
        "partials/current_job.html",
        session=session,
        robot_state=robot_state,
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
    except Exception:
        pass
    with _run_lock:
        if _run_session.get("state") in ("running", "paused"):
            _run_session["state"] = "stopped"
    return _render_current_job()


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
