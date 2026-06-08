from __future__ import annotations

import getpass
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, current_app, redirect, render_template, request
from flask_lucide import Lucide
from flask_lucide.icons import i as LUCIDE_ICONS

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_local_env_file() -> None:
    if load_dotenv is None:
        return

    workspace_root = Path(__file__).resolve().parents[1]
    env_file = workspace_root / ".env"
    if env_file.exists():
        load_dotenv(env_file)


_load_local_env_file()

try:
    from .robot_service import WeldFlexRobotService
    from .run_state_manager import RunStateManager
except ImportError:
    from robot_service import WeldFlexRobotService
    from run_state_manager import RunStateManager


_mock_system_state: dict[str, Any] = {
    "timezone": "America/New_York",
    "ntp_enabled": True,
    "ntp_synced": True,
    "ssid": "WeldFlex_Local_WiFi",
    "ip_address": "192.168.1.100",
    "sys_user": "pi",
}
_mock_time_delta_seconds = 0.0
_mock_time_set_at = 0.0

COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Phoenix",
    "America/Los_Angeles",
    "America/Anchorage",
    "America/Honolulu",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Shanghai",
    "Asia/Tokyo",
]



def _get_system_info() -> dict:
    """Gather live system info for the General Settings page."""
    is_linux = sys.platform == "linux"

    # Calculate current time (handling mock time override if on dev platform)
    if not is_linux and not _mock_system_state["ntp_enabled"] and _mock_time_set_at > 0:
        elapsed = time.time() - _mock_time_set_at
        now = datetime.now() + timedelta(seconds=_mock_time_delta_seconds + elapsed)
    else:
        now = datetime.now()

    info = {
        "datetime": now.strftime("%Y-%m-%d %H:%M"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "timezone": "Unknown",
        "ntp_enabled": False,
        "ntp_synced": False,
        "ssid": "Unknown",
        "ip_address": "Unknown",
        "sys_user": "Unknown",
    }

    if is_linux:
        try:
            ntp_out = subprocess.check_output(
                ["timedatectl", "show", "--property=NTP,NTPSynchronized"],
                text=True, timeout=3,
            )
            props = dict(line.split("=", 1) for line in ntp_out.splitlines() if "=" in line)
            info["ntp_enabled"] = props.get("NTP", "no").strip().lower() == "yes"
            info["ntp_synced"] = props.get("NTPSynchronized", "no").strip().lower() == "yes"
        except Exception:
            pass
        try:
            tz = subprocess.check_output(
                ["timedatectl", "show", "--property=Timezone", "--value"],
                text=True, timeout=3,
            ).strip()
            if tz:
                info["timezone"] = tz
        except Exception:
            pass
        try:
            wifi_out = subprocess.check_output(
                ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                text=True, timeout=3,
            )
            for line in wifi_out.splitlines():
                if line.startswith("yes:"):
                    info["ssid"] = line[4:].strip() or "Unknown"
                    break
        except Exception:
            pass
        try:
            hostname_out = subprocess.check_output(
                ["hostname", "-I"], text=True, timeout=3
            ).strip()
            ip = hostname_out.split()[0] if hostname_out.split() else None
            if ip:
                info["ip_address"] = ip
        except Exception:
            try:
                info["ip_address"] = socket.gethostbyname(socket.gethostname())
            except Exception:
                pass
        try:
            info["sys_user"] = getpass.getuser()
        except Exception:
            pass
    else:
        info["timezone"] = _mock_system_state["timezone"]
        info["ntp_enabled"] = _mock_system_state["ntp_enabled"]
        info["ntp_synced"] = _mock_system_state["ntp_synced"]
        info["ssid"] = _mock_system_state["ssid"]
        info["ip_address"] = _mock_system_state["ip_address"]
        info["sys_user"] = _mock_system_state["sys_user"]

    return info


def create_app() -> Flask:
    app = Flask(__name__)
    Lucide(app)
    _app_start = time.monotonic()

    def icon_safe(icon_name: str, fallback: str = "circle", **kwargs: Any) -> Any:
        lucide_ext = current_app.extensions.get("lucide")
        if lucide_ext is None:
            return ""

        resolved = icon_name if icon_name in LUCIDE_ICONS else fallback
        try:
            return lucide_ext.icon(resolved, **kwargs)
        except KeyError:
            if fallback in LUCIDE_ICONS and fallback != resolved:
                return lucide_ext.icon(fallback, **kwargs)
            return ""

    app.jinja_env.globals["icon_safe"] = icon_safe

    try:
        _git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        _git_sha = "unknown"
    app.jinja_env.globals["git_sha"] = _git_sha

    robot_ip = os.getenv("WELDFLEX_ROBOT_IP", "192.168.58.2")
    controller_host = os.getenv("WELDFLEX_CONTROLLER_HOST", robot_ip)
    default_program_path = os.getenv("WELDFLEX_PROGRAM_PATH", "/fruser/studCycle.lua")
    studs_data_path = os.getenv("WELDFLEX_STUDS_DATA_PATH", "/fruser/studs_data.lua")
    status_interval_ms = int(os.getenv("WELDFLEX_STATUS_INTERVAL_MS", "1000"))

    runtime_settings: dict[str, Any] = {
        "robot_ip": robot_ip,
        "controller_host": controller_host,
        "program_path": default_program_path,
        "studs_data_path": studs_data_path,
        "status_interval_ms": status_interval_ms,
    }

    run_state_manager = RunStateManager()

    robot_service = WeldFlexRobotService(
        robot_ip=robot_ip,
        controller_host=controller_host,
        studs_data_path=studs_data_path,
    )

    workspace_root = Path(__file__).resolve().parents[1]
    recipes_dir = workspace_root / "data"
    recipes_file = recipes_dir / "recipes.json"
    recipes_lock = threading.Lock()
    machine_config_file = recipes_dir / "machine_config.json"
    machine_config_lock = threading.Lock()

    _machine_config_defaults: dict[str, Any] = {"clearance_z_mm": 50.0, "collision_sensitivity": 3}

    def read_machine_config() -> dict[str, Any]:
        with machine_config_lock:
            if not machine_config_file.exists():
                return dict(_machine_config_defaults)
            try:
                data = json.loads(machine_config_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return dict(_machine_config_defaults)
        if not isinstance(data, dict):
            return dict(_machine_config_defaults)
        return data

    def write_machine_config(config: dict[str, Any]) -> None:
        recipes_dir.mkdir(parents=True, exist_ok=True)
        with machine_config_lock:
            machine_config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def get_clearance_z_mm() -> float:
        config = read_machine_config()
        try:
            return float(config.get("clearance_z_mm", 50.0))
        except (TypeError, ValueError):
            return 50.0

    def get_collision_sensitivity() -> int:
        config = read_machine_config()
        try:
            return max(1, min(5, int(config.get("collision_sensitivity", 3))))
        except (TypeError, ValueError):
            return 3

    def parse_studs_text(studs_text: str) -> list[dict[str, float]]:
        studs: list[dict[str, float]] = []
        for line_index, raw_line in enumerate(studs_text.splitlines(), start=1):
            line = raw_line.strip().rstrip(",")
            if not line:
                continue

            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 2:
                raise ValueError(f"Line {line_index}: expected 'x,y'.")

            try:
                x = float(parts[0])
                y = float(parts[1])
            except ValueError as exc:
                raise ValueError(f"Line {line_index}: x and y must be numeric.") from exc

            studs.append({"x": x, "y": y})

        if not studs:
            raise ValueError("Enter at least one stud coordinate line.")

        return studs

    def studs_to_text(studs: list[dict[str, float]]) -> str:
        return "\n".join(f"{stud['x']:g},{stud['y']:g}" for stud in studs)

    def sanitize_recipe_name(name: str) -> str:
        clean = name.strip()
        if not clean:
            raise ValueError("Recipe name is required.")
        if len(clean) > 64:
            raise ValueError("Recipe name must be 64 characters or less.")
        if not re.match(r"^[A-Za-z0-9 _\-]+$", clean):
            raise ValueError("Recipe name can only include letters, numbers, spaces, dash, and underscore.")
        return clean

    def read_recipe_store() -> dict[str, Any]:
        with recipes_lock:
            if not recipes_file.exists():
                return {"recipes": {}}
            try:
                data = json.loads(recipes_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {"recipes": {}}

        if not isinstance(data, dict) or not isinstance(data.get("recipes"), dict):
            return {"recipes": {}}
        return data

    def write_recipe_store(store: dict[str, Any]) -> None:
        recipes_dir.mkdir(parents=True, exist_ok=True)
        with recipes_lock:
            recipes_file.write_text(json.dumps(store, indent=2), encoding="utf-8")

    def recipe_list() -> list[dict[str, str]]:
        store = read_recipe_store()
        recipes = store.get("recipes", {})
        items = []
        for name, data in recipes.items():
            updated_at = data.get("updated_at", "") if isinstance(data, dict) else ""
            items.append({"name": name, "updated_at": updated_at})
        items.sort(key=lambda item: item["name"].lower())
        return items

    def recipe_catalog() -> list[dict[str, Any]]:
        store = read_recipe_store()
        recipes = store.get("recipes", {})
        items: list[dict[str, Any]] = []
        for name, data in recipes.items():
            if not isinstance(data, dict):
                continue
            studs_text = data.get("studs_text", "")
            updated_at = data.get("updated_at", "")
            studs_count = 0
            if isinstance(studs_text, str) and studs_text.strip():
                try:
                    studs_count = len(parse_studs_text(studs_text))
                except ValueError:
                    studs_count = 0
            updated_label = "-"
            if isinstance(updated_at, str) and updated_at:
                try:
                    updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    updated_label = updated_dt.astimezone().strftime("%Y-%m-%d %I:%M %p")
                except ValueError:
                    updated_label = updated_at
            items.append(
                {
                    "name": name,
                    "updated_at": updated_at,
                    "updated_label": updated_label,
                    "studs_count": studs_count,
                }
            )

        items.sort(key=lambda item: item["name"].lower())
        return items

    def get_recipe_text(name: str) -> str:
        store = read_recipe_store()
        recipes = store.get("recipes", {})
        entry = recipes.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("studs_text"), str):
            raise ValueError(f"Recipe '{name}' not found.")
        return entry["studs_text"]

    def parse_run_count(raw: str) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Run count must be a whole number.") from exc
        if value < 1 or value > 100:
            raise ValueError("Run count must be between 1 and 100.")
        return value

    def parse_bounded_float(raw: str, label: str, minimum: float, maximum: float) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be numeric.") from exc
        if value < minimum or value > maximum:
            raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}.")
        return value

    def parse_jog_axis(raw: str, allowed: dict[str, int]) -> tuple[str, int]:
        axis_key = raw.strip().lower()
        if axis_key not in allowed:
            raise ValueError("Invalid jog axis.")
        return axis_key, allowed[axis_key]

    def parse_jog_direction(raw: str) -> tuple[str, int]:
        direction_key = raw.strip().lower()
        direction_map = {
            "negative": ("-", 0),
            "positive": ("+", 1),
        }
        if direction_key not in direction_map:
            raise ValueError("Invalid jog direction.")
        symbol, value = direction_map[direction_key]
        return symbol, value

    def format_axis_values(values: list[float] | None, decimals: int) -> list[str]:
        if not values or len(values) != 6:
            return ["-"] * 6
        return [f"{value:.{decimals}f}" for value in values]

    def studs_preview_data(studs_text: str) -> dict[str, Any]:
        graph_size = 508.0
        view_size = 200.0

        if not studs_text.strip():
            return {
                "count": 0,
                "x_min": "-",
                "x_max": "-",
                "y_min": "-",
                "y_max": "-",
                "preview_points": [],
                "graph_points": [],
                "out_of_bounds_count": 0,
                "warning": "Add at least one X/Y row in millimeters to preview stud locations.",
            }

        studs = parse_studs_text(studs_text)
        x_values = [point["x"] for point in studs]
        y_values = [point["y"] for point in studs]
        graph_points: list[dict[str, Any]] = []
        out_of_bounds_count = 0

        for index, point in enumerate(studs, start=1):
            x_value = point["x"]
            y_value = point["y"]

            if 0 <= x_value <= graph_size and 0 <= y_value <= graph_size:
                x_plot = view_size - ((x_value / graph_size) * view_size)
                y_plot = view_size - ((y_value / graph_size) * view_size)
                graph_points.append(
                    {
                        "index": index,
                        "x": x_value,
                        "y": y_value,
                        "x_plot": round(x_plot, 2),
                        "y_plot": round(y_plot, 2),
                    }
                )
            else:
                out_of_bounds_count += 1

        warnings: list[str] = []
        if len(studs) > 8:
            warnings.append(f"Showing first 8 of {len(studs)} studs in the text list.")
        if out_of_bounds_count:
            warnings.append(
                f"{out_of_bounds_count} stud(s) fall outside the 508 x 508 mm preview area and are not plotted."
            )

        return {
            "count": len(studs),
            "x_min": min(x_values),
            "x_max": max(x_values),
            "y_min": min(y_values),
            "y_max": max(y_values),
            "preview_points": studs[:8],
            "graph_points": graph_points,
            "out_of_bounds_count": out_of_bounds_count,
            "warning": " ".join(warnings),
        }

    def get_connection_snapshot() -> dict[str, Any]:
        error_labels = {
            -4: "controller unreachable or not connected",
            -3: "rpc timeout",
            -2: "rpc communication error",
            -1: "sdk/client error",
        }

        snapshot: dict[str, Any] = {
            "robot_ip": runtime_settings["robot_ip"],
            "controller_host": runtime_settings["controller_host"],
            "program_path": runtime_settings["program_path"],
            "online": False,
            "program_state": "unknown",
            "current_line": "-",
        }

        try:
            status = robot_service.status()
            snapshot["online"] = bool(status.get("connected", False))
            snapshot["program_state"] = status.get("program_state", "unknown")
            snapshot["current_line"] = status.get("current_line", "-")
            if not snapshot["online"]:
                state_error = status.get("program_state_error")
                line_error = status.get("current_line_error")
                state_label = error_labels.get(state_error, "unknown")
                line_label = error_labels.get(line_error, "unknown")
                snapshot["error"] = (
                    f"status read failed: state={state_label} ({state_error}), "
                    f"line={line_label} ({line_error})"
                )
        except Exception as exc:
            snapshot["error"] = str(exc)

        return snapshot

    def current_run_summary() -> dict[str, Any]:
        try:
            status = robot_service.status()
            program_state = status.get("program_state", "unknown")
            current_line = status.get("current_line", "-")
        except Exception:
            program_state = "unknown"
            current_line = "-"
        return run_state_manager.current_summary(program_state, current_line)

    def stamp_command_state(command: str, status: str, detail: str = "") -> None:
        run_state_manager.stamp_command_state(command, status, detail)

    @app.get("/")
    def landing() -> Any:
        return render_template("landing.html", page_title="WeldFlex")

    @app.get("/operator")
    def index() -> Any:
        return render_template("home.html", page_title="Home", status_interval_ms=runtime_settings["status_interval_ms"])

    @app.get("/manager")
    def manager() -> Any:
        return render_template("manager.html", page_title="Manager")

    @app.get("/ui/manager/part-designer")
    def ui_mgr_part_designer() -> Any:
        return render_template("partials/mgr_part_designer.html")

    @app.get("/ui/manager/settings")
    def ui_mgr_settings() -> Any:
        return render_template("partials/mgr_settings.html")

    @app.get("/ui/manager/parts-list")
    def ui_mgr_parts_list() -> Any:
        return jsonify(recipe_catalog())

    @app.get("/ui/manager/part-points")
    def ui_mgr_part_points() -> Any:
        name = request.args.get("name", "").strip()
        try:
            text = get_recipe_text(name)
            studs = parse_studs_text(text)
            points = [{"id": i + 1, "x": s["x"], "y": s["y"]} for i, s in enumerate(studs)]
            return jsonify({"ok": True, "points": points})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404

    @app.get("/operator/parts")
    def parts() -> Any:
        selected_recipe_name: str | None = None
        studs_text = ""
        parts_error = ""

        recipe_query = request.args.get("recipe_name", "").strip()
        new_part = request.args.get("new") == "1"

        if recipe_query:
            try:
                selected_recipe_name = sanitize_recipe_name(recipe_query)
                studs_text = get_recipe_text(selected_recipe_name)
            except ValueError as exc:
                parts_error = str(exc)
        elif new_part:
            selected_recipe_name = ""

        return render_template(
            "parts.html",
            page_title="Parts",
            content_class="app-content-scroll-locked",
            recipes=recipe_catalog(),
            recipe_name=selected_recipe_name,
            studs_text=studs_text,
            parts_error=parts_error,
        )

    @app.get("/operator/part-library")
    def part_library() -> Any:
        return redirect("/operator/parts", code=301)

    @app.get("/operator/part-designer")
    def part_designer() -> Any:
        return redirect("/operator/parts", code=301)

    @app.get("/operator/robot-diagnostics")
    def robot_diagnostics() -> Any:
        return render_template(
            "robot_diagnostics.html",
            page_title="Robot Diagnostics",
            status_interval_ms=runtime_settings["status_interval_ms"],
            settings=runtime_settings,
            collision_sensitivity=get_collision_sensitivity(),
        )

    @app.get("/operator/admin")
    def admin() -> Any:
        return render_template("admin.html", page_title="Admin", settings=runtime_settings)

    @app.get("/operator/settings")
    def settings() -> Any:
        return render_template(
            "settings.html",
            page_title="Settings",
            sys_info=_get_system_info(),
            common_timezones=COMMON_TIMEZONES,
        )

    @app.get("/operator/calibration")
    def calibration() -> Any:
        return render_template("calibration.html", page_title="Calibration")

    _calib_state: dict[str, Any] = {"pins": set(), "drag_pin": None}

    def _calib_render(
        *,
        applied: bool = False,
        coord: list[float] | None = None,
        record_error: str | None = None,
        apply_error: str | None = None,
        drag_error: str | None = None,
    ) -> Any:
        pins = _calib_state["pins"]
        return render_template(
            "partials/calibrate_steps.html",
            pins_recorded=pins,
            all_recorded=len(pins) == 3,
            drag_pin=_calib_state["drag_pin"],
            applied=applied,
            coord=coord,
            record_error=record_error,
            apply_error=apply_error,
            drag_error=drag_error,
            clearance_z_mm=get_clearance_z_mm(),
            collision_sensitivity=get_collision_sensitivity(),
        )

    @app.get("/operator/calibrate")
    def calibrate() -> Any:
        return render_template(
            "calibrate.html",
            page_title="Calibrate",
            content_class="app-content-scroll-locked",
            clearance_z_mm=get_clearance_z_mm(),
        )

    @app.post("/ui/calibrate/clearance")
    def ui_calibrate_clearance() -> Any:
        try:
            clearance_z_mm = parse_bounded_float(
                request.form.get("clearance_z_mm", ""), "Clearance height", 5.0, 500.0
            )
            config = read_machine_config()
            config["clearance_z_mm"] = clearance_z_mm
            write_machine_config(config)
            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Clearance Height",
                payload={"message": f"Clearance height set to {clearance_z_mm:g} mm."},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Clearance Height",
                payload={"error": str(exc)},
            )

    @app.get("/ui/calibrate/status")
    def ui_calibrate_status() -> Any:
        return _calib_render()

    @app.post("/ui/calibrate/enable-drag")
    def ui_calibrate_enable_drag() -> Any:
        try:
            pin = int(request.form.get("pin", "0"))
            if pin not in (1, 2, 3):
                raise ValueError("Pin number must be 1, 2, or 3.")
            if _calib_state["drag_pin"] is not None:
                try:
                    robot_service.disable_drag()
                except Exception:
                    pass
            robot_service.enable_drag()
            _calib_state["drag_pin"] = pin
            return _calib_render()
        except Exception as exc:
            _calib_state["drag_pin"] = None
            return _calib_render(drag_error=str(exc))

    @app.post("/ui/calibrate/record-pin")
    def ui_calibrate_record_pin() -> Any:
        try:
            pin = int(request.form.get("pin", "0"))
            if pin not in (1, 2, 3):
                raise ValueError("Pin number must be 1, 2, or 3.")
            robot_service.record_wobj_point(pin)
            try:
                robot_service.disable_drag()
            except Exception:
                pass
            _calib_state["pins"].add(pin)
            _calib_state["drag_pin"] = None
            return _calib_render()
        except Exception as exc:
            return _calib_render(record_error=str(exc))

    @app.post("/ui/calibrate/apply")
    def ui_calibrate_apply() -> Any:
        try:
            if len(_calib_state["pins"]) < 3:
                raise ValueError("All 3 pins must be recorded before applying.")
            clearance_z_mm = parse_bounded_float(
                request.form.get("clearance_z_mm", str(get_clearance_z_mm())),
                "Clearance height", 5.0, 500.0,
            )
            try:
                collision_sensitivity = max(1, min(5, int(request.form.get("collision_sensitivity", str(get_collision_sensitivity())))))
            except (TypeError, ValueError):
                collision_sensitivity = 3
            coord = robot_service.compute_and_apply_wobj(wobj_id=1)
            config = read_machine_config()
            config["clearance_z_mm"] = clearance_z_mm
            config["collision_sensitivity"] = collision_sensitivity
            write_machine_config(config)
            _calib_state["pins"].clear()
            _calib_state["drag_pin"] = None
            return _calib_render(applied=True, coord=coord)
        except Exception as exc:
            return _calib_render(apply_error=str(exc))

    @app.post("/ui/calibrate/reset")
    def ui_calibrate_reset() -> Any:
        if _calib_state["drag_pin"] is not None:
            try:
                robot_service.disable_drag()
            except Exception:
                pass
        _calib_state["pins"].clear()
        _calib_state["drag_pin"] = None
        return _calib_render()

    @app.get("/operator/jog")
    def jog() -> Any:
        return render_template(
            "jog.html",
            page_title="Jog",
            status_interval_ms=runtime_settings["status_interval_ms"],
            content_class="app-content-scroll-locked",
        )

    @app.get("/ui/jog/status")
    def ui_jog_status() -> Any:
        try:
            snapshot = robot_service.jog_snapshot()
        except Exception as exc:
            snapshot = {
                "connected": False,
                "program_state": "unknown",
                "current_line": "-",
                "tcp_pose": None,
                "joint_positions": None,
                "error": str(exc),
            }

        snapshot["tcp_pose_labels"] = format_axis_values(snapshot.get("tcp_pose"), 3)
        snapshot["joint_labels"] = format_axis_values(snapshot.get("joint_positions"), 2)
        return render_template("partials/jog_status.html", snapshot=snapshot)

    @app.post("/ui/jog/move")
    def ui_jog_move() -> Any:
        try:
            mode = request.form.get("mode", "").strip().lower()
            direction_symbol, direction_value = parse_jog_direction(request.form.get("direction", ""))
            velocity = parse_bounded_float(request.form.get("jog_velocity", "20"), "Jog velocity", 1, 100)

            if mode == "joint":
                axis_name, axis_number = parse_jog_axis(
                    request.form.get("axis", ""),
                    {"j1": 1, "j2": 2, "j3": 3, "j4": 4, "j5": 5, "j6": 6},
                )
                distance = parse_bounded_float(request.form.get("joint_step", "2"), "Joint step", 0.1, 180)
                ref = 0
                move_label = f"{axis_name.upper()} {direction_symbol} {distance:g} deg"
            elif mode == "cartesian":
                frame_key = request.form.get("cartesian_frame", "").strip().lower()
                frame_map = {
                    "base": (2, "Base"),
                    "tool": (4, "Tool"),
                    "workpiece": (8, "Workpiece"),
                }
                if frame_key not in frame_map:
                    raise ValueError("Invalid cartesian jog frame.")
                axis_name, axis_number = parse_jog_axis(
                    request.form.get("axis", ""),
                    {"x": 1, "y": 2, "z": 3, "rx": 4, "ry": 5, "rz": 6},
                )
                distance = parse_bounded_float(request.form.get("cartesian_step", "5"), "Cartesian step", 0.1, 200)
                ref, frame_label = frame_map[frame_key]
                unit_label = "deg" if axis_name.startswith("r") else "mm"
                move_label = f"{frame_label} {axis_name.upper()} {direction_symbol} {distance:g} {unit_label}"
            else:
                raise ValueError("Invalid jog mode.")

            robot_service.jog(ref=ref, axis=axis_number, direction=direction_value, distance=distance, velocity=velocity)
            stamp_command_state("Jog", "ok", move_label)
            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Jog Move",
                payload={"message": move_label},
            )
        except Exception as exc:
            stamp_command_state("Jog", "error", str(exc))
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Jog Move",
                payload={"error": str(exc)},
            )

    @app.post("/ui/jog/stop")
    def ui_jog_stop() -> Any:
        try:
            robot_service.stop_jog()
            stamp_command_state("Jog Stop", "ok", "Immediate jog stop sent")
            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Jog Stop",
                payload={"message": "Immediate jog stop sent"},
            )
        except Exception as exc:
            stamp_command_state("Jog Stop", "error", str(exc))
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Jog Stop",
                payload={"error": str(exc)},
            )

    @app.get("/ui/parts/list")
    def ui_parts_list() -> Any:
        return render_template("partials/parts_recipe_list.html", recipes=recipe_catalog())

    @app.get("/ui/parts/new")
    def ui_parts_new() -> Any:
        return render_template("partials/parts_editor.html", recipe_name="", studs_text="")

    @app.post("/ui/parts/load")
    def ui_parts_load() -> Any:
        try:
            name = sanitize_recipe_name(request.form.get("recipe_name", ""))
            studs_text = get_recipe_text(name)
            return render_template("partials/parts_editor.html", recipe_name=name, studs_text=studs_text)
        except Exception as exc:
            return render_template(
                "partials/command_result.html", ok=False, title="Load Part", payload={"error": str(exc)}
            )

    @app.post("/ui/parts/delete")
    def ui_parts_delete() -> Any:
        try:
            name = sanitize_recipe_name(request.form.get("recipe_name", ""))
            store = read_recipe_store()
            recipes = store.get("recipes", {})
            if name not in recipes:
                raise ValueError(f"Part '{name}' not found.")
            del recipes[name]
            write_recipe_store(store)
            if request.headers.get("HX-Request") == "true":
                return "", 204, {"HX-Redirect": "/operator/parts"}
            return render_template(
                "partials/command_result.html", ok=True, title="Delete Part", payload={"message": f"Deleted '{name}'"}
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html", ok=False, title="Delete Part", payload={"error": str(exc)}
            )

    @app.get("/ui")
    def operator_ui() -> Any:
        return render_template(
            "operator.html",
            page_title="Part Designer",
            default_program_path=runtime_settings["program_path"],
            robot_ip=runtime_settings["robot_ip"],
            controller_host=runtime_settings["controller_host"],
            status_interval_ms=runtime_settings["status_interval_ms"],
            recipes=recipe_list(),
        )

    @app.get("/ui/recipes")
    def ui_recipes() -> Any:
        return render_template("partials/recipe_library.html", recipes=recipe_list())

    @app.get("/ui/studs-preview")
    def ui_studs_preview() -> Any:
        studs_text = request.args.get("studs_text", "")
        x_values = request.args.getlist("x")
        y_values = request.args.getlist("y")

        # Preview is tolerant: ignore incomplete rows until both X and Y are present.
        lines: list[str] = []
        if x_values or y_values:
            incomplete_rows = 0
            for x_raw, y_raw in zip(x_values, y_values):
                x_clean = x_raw.strip()
                y_clean = y_raw.strip()
                if not x_clean and not y_clean:
                    continue
                if not x_clean or not y_clean:
                    incomplete_rows += 1
                    continue
                lines.append(f"{x_clean},{y_clean}")

            studs_text = "\n".join(lines)

            preview = studs_preview_data(studs_text)
            if incomplete_rows:
                existing_warning = preview.get("warning", "")
                prefix = " " if existing_warning else ""
                preview["warning"] = f"{existing_warning}{prefix}{incomplete_rows} incomplete row(s) ignored in preview."
            return render_template("partials/studs_preview.html", ok=True, preview=preview)

        try:
            preview = studs_preview_data(studs_text)
            return render_template("partials/studs_preview.html", ok=True, preview=preview)
        except Exception as exc:
            return render_template("partials/studs_preview.html", ok=False, preview={"error": str(exc)})

    @app.get("/ui/home-current-run")
    def ui_home_current_run() -> Any:
        return render_template("partials/home_current_run.html", summary=current_run_summary())

    @app.post("/ui/recipes/save")
    def ui_recipe_save() -> Any:
        try:
            name = sanitize_recipe_name(request.form.get("recipe_name", ""))
            studs_text = request.form.get("studs_text", "")

            if not studs_text.strip():
                x_values = request.form.getlist("x")
                y_values = request.form.getlist("y")
                lines: list[str] = []
                for x_raw, y_raw in zip(x_values, y_values):
                    x_clean = x_raw.strip()
                    y_clean = y_raw.strip()
                    if not x_clean and not y_clean:
                        continue
                    lines.append(f"{x_clean},{y_clean}")
                studs_text = "\n".join(lines)

            studs = parse_studs_text(studs_text)
            normalized_text = studs_to_text(studs)

            store = read_recipe_store()
            recipes = store.setdefault("recipes", {})
            recipes[name] = {
                "studs_text": normalized_text,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            write_recipe_store(store)

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Recipe Save",
                payload={"message": f"Saved recipe '{name}'", "recipe_name": name},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Recipe Save",
                payload={"error": str(exc)},
            )

    @app.post("/ui/recipes/load")
    def ui_recipe_load() -> Any:
        try:
            name = sanitize_recipe_name(request.form.get("selected_recipe", ""))
            studs_text = get_recipe_text(name)
            return render_template("partials/studs_input.html", studs_text=studs_text)
        except Exception as exc:
            return render_template("partials/studs_input.html", studs_text="", error=str(exc))

    @app.post("/ui/recipes/delete")
    def ui_recipe_delete() -> Any:
        try:
            name = sanitize_recipe_name(request.form.get("selected_recipe", ""))
            store = read_recipe_store()
            recipes = store.get("recipes", {})
            if name not in recipes:
                raise ValueError(f"Recipe '{name}' not found.")
            del recipes[name]
            write_recipe_store(store)

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Recipe Delete",
                payload={"message": f"Deleted recipe '{name}'", "recipe_name": name},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Recipe Delete",
                payload={"error": str(exc)},
            )

    @app.post("/ui/library/run")
    def ui_library_run() -> Any:
        try:
            recipe_name = sanitize_recipe_name(request.form.get("recipe_name", ""))
            run_count = parse_run_count(request.form.get("run_count", "1"))
            studs = parse_studs_text(get_recipe_text(recipe_name))

            if run_state_manager.has_remaining_runs():
                raise ValueError("Another production run is already active. Stop it before starting a new run.")

            run_state_manager.stage_batch(recipe_name, run_count, studs)
            stamp_command_state("Run", "ok", f"Staged {recipe_name} for {run_count} target parts. Click Run on Home to start.")

            if request.headers.get("HX-Request") == "true":
                return "", 204, {"HX-Redirect": "/operator"}
            return redirect("/operator")
        except Exception as exc:
            stamp_command_state("Run", "error", str(exc))
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Library Run",
                payload={"error": str(exc)},
            )

    @app.post("/ui/library/delete")
    def ui_library_delete() -> Any:
        try:
            recipe_name = sanitize_recipe_name(request.form.get("recipe_name", ""))
            store = read_recipe_store()
            recipes = store.get("recipes", {})
            if recipe_name not in recipes:
                raise ValueError(f"Recipe '{recipe_name}' not found.")
            del recipes[recipe_name]
            write_recipe_store(store)
            if request.headers.get("HX-Request") == "true":
                return "", 204, {"HX-Redirect": "/operator/parts"}
            return redirect("/operator/parts")
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Part Delete",
                payload={"error": str(exc)},
            )

    @app.get("/ui/connection")
    def ui_connection() -> Any:
        snapshot = get_connection_snapshot()
        return render_template("partials/connection_chips.html", snapshot=snapshot)

    @app.get("/favicon.ico")
    def favicon() -> Any:
        return ("", 204)

    @app.post("/ui/run")
    def ui_run_program() -> Any:
        studs_text = request.form.get("studs_text", "")
        program_path = runtime_settings["program_path"]

        if not studs_text.strip():
            x_values = request.form.getlist("x")
            y_values = request.form.getlist("y")
            lines: list[str] = []
            for x_raw, y_raw in zip(x_values, y_values):
                x_clean = x_raw.strip()
                y_clean = y_raw.strip()
                if not x_clean and not y_clean:
                    continue
                lines.append(f"{x_clean},{y_clean}")
            studs_text = "\n".join(lines)

        try:
            studs = parse_studs_text(studs_text)
            result = robot_service.upload_load_run(studs, program_path, clearance_z_mm=get_clearance_z_mm(), collision_sensitivity=get_collision_sensitivity())
            run_state_manager.start_designer_cycle()
            stamp_command_state("Run", "ok", "Launched unsaved designer job")
            return render_template("partials/command_result.html", ok=True, title="Run", payload=result)
        except Exception as exc:
            stamp_command_state("Run", "error", str(exc))
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Run",
                payload={"error": str(exc)},
            )

    @app.post("/ui/pause")
    def ui_pause_program() -> Any:
        try:
            result = robot_service.pause()
            stamp_command_state("Pause", "ok", "Pause command sent")
            return render_template("partials/command_result.html", ok=True, title="Pause", payload=result)
        except Exception as exc:
            stamp_command_state("Pause", "error", str(exc))
            return (
                render_template(
                    "partials/command_result.html",
                    ok=False,
                    title="Pause",
                    payload={"error": str(exc)},
                ),
                500,
            )

    @app.post("/ui/stop")
    def ui_stop_program() -> Any:
        try:
            result = robot_service.stop()
            run_state_manager.stop_batch()
            stamp_command_state("Stop", "ok", "Stop command sent")
            return render_template("partials/command_result.html", ok=True, title="Stop", payload=result)
        except Exception as exc:
            stamp_command_state("Stop", "error", str(exc))
            return (
                render_template(
                    "partials/command_result.html",
                    ok=False,
                    title="Stop",
                    payload={"error": str(exc)},
                ),
                500,
            )

    @app.post("/ui/home/run-next")
    def ui_home_run_next() -> Any:
        try:
            clearance_z = get_clearance_z_mm()
            sensitivity = get_collision_sensitivity()
            run_state_manager.start_next_batch_cycle(
                launch_cycle=lambda studs, path: robot_service.upload_load_run(studs, path, clearance_z_mm=clearance_z, collision_sensitivity=sensitivity),
                program_path=runtime_settings["program_path"],
            )
            return "", 204
        except Exception as exc:
            stamp_command_state("Run", "error", str(exc))
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Run",
                payload={"error": str(exc)},
            )

    @app.get("/ui/status")
    def ui_status() -> Any:
        try:
            status = robot_service.status()
            return render_template("partials/status.html", ok=True, status=status)
        except Exception as exc:
            return render_template("partials/status.html", ok=False, status={"error": str(exc)})

    @app.get("/ui/diagnostics")
    def ui_diagnostics() -> Any:
        elapsed = int(time.monotonic() - _app_start)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        uptime = f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"

        snapshot = get_connection_snapshot()
        try:
            status = robot_service.status()
        except Exception as exc:
            status = {"error": str(exc)}
            return render_template("partials/diagnostics_readout.html", snapshot=snapshot, ok=False, status=status, uptime=uptime)

        return render_template("partials/diagnostics_readout.html", snapshot=snapshot, ok=True, status=status, uptime=uptime)

    @app.post("/ui/settings/save")
    def ui_settings_save() -> Any:
        try:
            new_robot_ip = request.form.get("robot_ip", "").strip()
            new_controller_host = request.form.get("controller_host", "").strip()
            new_program_path = request.form.get("program_path", "").strip()
            new_studs_data_path = request.form.get("studs_data_path", "").strip()
            new_interval_raw = request.form.get("status_interval_ms", "").strip()

            if not new_robot_ip:
                raise ValueError("Robot IP is required.")
            if not new_controller_host:
                new_controller_host = new_robot_ip
            if not new_program_path:
                raise ValueError("Program path is required.")
            if not new_studs_data_path:
                raise ValueError("Studs data path is required.")

            try:
                new_interval = int(new_interval_raw)
            except ValueError as exc:
                raise ValueError("Status interval must be an integer in milliseconds.") from exc
            if new_interval < 250 or new_interval > 10000:
                raise ValueError("Status interval must be between 250 and 10000 ms.")

            runtime_settings["robot_ip"] = new_robot_ip
            runtime_settings["controller_host"] = new_controller_host
            runtime_settings["program_path"] = new_program_path
            runtime_settings["studs_data_path"] = new_studs_data_path
            runtime_settings["status_interval_ms"] = new_interval

            robot_service.reconfigure(
                robot_ip=new_robot_ip,
                controller_host=new_controller_host,
                studs_data_path=new_studs_data_path,
            )

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Settings Save",
                payload={
                    "message": "Runtime settings updated.",
                    "settings": runtime_settings,
                    "note": "Changes apply immediately for this running app instance.",
                },
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Settings Save",
                payload={"error": str(exc)},
            )



    @app.post("/ui/diagnostics/save-collision-sensitivity")
    def ui_diagnostics_save_collision_sensitivity() -> Any:
        try:
            try:
                collision_sensitivity = max(1, min(5, int(request.form.get("collision_sensitivity", str(get_collision_sensitivity())))))
            except (TypeError, ValueError):
                collision_sensitivity = 3
            config = read_machine_config()
            config["collision_sensitivity"] = collision_sensitivity
            write_machine_config(config)
            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Safety Settings",
                payload={"message": f"Collision sensitivity set to {collision_sensitivity}."},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Safety Settings",
                payload={"error": str(exc)},
            )

    @app.post("/ui/settings/ntp")
    def ui_settings_ntp() -> Any:
        try:
            if sys.platform == "linux":
                subprocess.check_call(["sudo", "timedatectl", "set-ntp", "true"], timeout=10)
            else:
                _mock_system_state["ntp_enabled"] = True
                _mock_system_state["ntp_synced"] = True

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Auto-Sync",
                payload={"message": "NTP auto-sync enabled. Time will update when network is available."},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Auto-Sync",
                payload={"error": str(exc)},
            )

    @app.post("/ui/settings/timezone")
    def ui_settings_timezone() -> Any:
        try:
            tz = request.form.get("timezone", "").strip()
            if not tz:
                raise ValueError("Timezone is required.")

            if sys.platform == "linux":
                subprocess.check_call(["sudo", "timedatectl", "set-timezone", tz], timeout=10)
            else:
                _mock_system_state["timezone"] = tz

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Timezone",
                payload={"message": f"Timezone set to {tz}."},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Timezone",
                payload={"error": str(exc)},
            )

    @app.post("/ui/settings/wifi")
    def ui_settings_wifi() -> Any:
        try:
            ssid = request.form.get("ssid", "").strip()
            if ssid == "__custom__":
                ssid = request.form.get("ssid_manual", "").strip()
            password = request.form.get("wifi_password", "").strip()

            if not ssid:
                raise ValueError("SSID is required.")

            if sys.platform == "linux":
                cmd = ["sudo", "nmcli", "device", "wifi", "connect", ssid]
                if password:
                    cmd += ["password", password]
                subprocess.check_call(cmd, timeout=30)
            else:
                _mock_system_state["ssid"] = ssid
                time.sleep(1.0)  # Simulate network connection latency

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="Network / WiFi",
                payload={"message": f"Connected to '{ssid}'."},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="Network / WiFi",
                payload={"error": str(exc)},
            )

    @app.get("/ui/settings/wifi/scan")
    def ui_settings_wifi_scan() -> Any:
        networks = []
        try:
            if sys.platform == "linux":
                try:
                    subprocess.run(["sudo", "nmcli", "device", "wifi", "rescan"], timeout=5)
                except Exception:
                    pass

                scan_out = subprocess.check_output(
                    ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi"],
                    text=True, timeout=8,
                )
                seen_ssids = set()
                for line in scan_out.splitlines():
                    parts = line.split(":")
                    if len(parts) >= 3:
                        ssid = parts[0].strip()
                        if not ssid or ssid in seen_ssids:
                            continue
                        seen_ssids.add(ssid)

                        try:
                            signal = int(parts[1].strip())
                        except ValueError:
                            signal = 0

                        security = parts[2].strip() or "Open"
                        networks.append({
                            "ssid": ssid,
                            "signal": signal,
                            "security": security
                        })
            else:
                networks = [
                    {"ssid": "WeldFlex_Local_WiFi", "signal": 95, "security": "WPA2"},
                    {"ssid": "Guest_Net", "signal": 60, "security": "WPA2"},
                    {"ssid": "Robot_Cell_3", "signal": 80, "security": "WPA2"},
                    {"ssid": "Office_WiFi", "signal": 45, "security": "WPA2 Enterprise"},
                ]

            networks.sort(key=lambda n: n["signal"], reverse=True)
        except Exception as exc:
            return f"<option value=''>Error scanning: {exc}</option>", 500

        return render_template("partials/wifi_scan.html", networks=networks)

    @app.post("/ui/settings/system/reboot")
    def ui_settings_system_reboot() -> Any:
        try:
            def run_reboot():
                time.sleep(2.0)
                if sys.platform == "linux":
                    subprocess.run(["sudo", "reboot"])
                else:
                    print("Mock Reboot Triggered!")

            threading.Thread(target=run_reboot, daemon=True).start()

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="System Reboot",
                payload={"message": "System reboot sequence initiated. The application will disconnect shortly."},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="System Reboot",
                payload={"error": str(exc)},
            )

    @app.post("/ui/settings/system/shutdown")
    def ui_settings_system_shutdown() -> Any:
        try:
            def run_shutdown():
                time.sleep(2.0)
                if sys.platform == "linux":
                    subprocess.run(["sudo", "poweroff"])
                else:
                    print("Mock Shutdown Triggered!")

            threading.Thread(target=run_shutdown, daemon=True).start()

            return render_template(
                "partials/command_result.html",
                ok=True,
                title="System Shutdown",
                payload={"message": "System shutdown sequence initiated. Please wait for the Pi status LEDs to stop flashing before turning off power."},
            )
        except Exception as exc:
            return render_template(
                "partials/command_result.html",
                ok=False,
                title="System Shutdown",
                payload={"error": str(exc)},
            )




    def health() -> Any:
        return jsonify({"ok": True})

    @app.post("/api/run")
    def run_program() -> Any:
        payload = request.get_json(silent=True) or {}
        studs = payload.get("studs")
        program_path = payload.get("program_path", runtime_settings["program_path"])

        if not isinstance(studs, list) or not studs:
            return jsonify({"error": "Body must include a non-empty studs array."}), 400

        normalized_studs: list[dict[str, float]] = []
        for index, stud in enumerate(studs):
            if not isinstance(stud, dict) or "x" not in stud or "y" not in stud:
                return jsonify({"error": f"studs[{index}] must contain x and y."}), 400

            try:
                x = float(stud["x"])
                y = float(stud["y"])
            except (TypeError, ValueError):
                return jsonify({"error": f"studs[{index}] x/y must be numeric."}), 400

            normalized_studs.append({"x": x, "y": y})

        try:
            result = robot_service.upload_load_run(normalized_studs, program_path, clearance_z_mm=get_clearance_z_mm(), collision_sensitivity=get_collision_sensitivity())
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/pause")
    def pause_program() -> Any:
        try:
            result = robot_service.pause()
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/stop")
    def stop_program() -> Any:
        try:
            result = robot_service.stop()
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/status")
    def get_status() -> Any:
        try:
            result = robot_service.status()
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
