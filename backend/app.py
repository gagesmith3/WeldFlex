import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request
from markupsafe import Markup
from robot_service import WeldFlexRobotService

app = Flask(__name__, template_folder="templates", static_folder="static")

ROBOT_IP = os.getenv("ROBOT_IP", "192.168.58.2")
robot = WeldFlexRobotService(robot_ip=ROBOT_IP)

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
    return render_template("operator.html", page_title="Operator", git_sha="dev")

@app.context_processor
def inject_defaults():
    return {
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
