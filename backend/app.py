from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
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


def create_app() -> Flask:
    app = Flask(__name__)
    Lucide(app)

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
            items.append(
                {
                    "name": name,
                    "updated_at": updated_at,
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
    def index() -> Any:
        return render_template("home.html", page_title="Home", status_interval_ms=runtime_settings["status_interval_ms"])

    @app.get("/part-library")
    def part_library() -> Any:
        return render_template(
            "part_library.html",
            page_title="Part Library",
            recipes=recipe_catalog(),
            status_interval_ms=runtime_settings["status_interval_ms"],
        )

    @app.get("/part-designer")
    def part_designer() -> Any:
        return render_template(
            "operator.html",
            page_title="Part Designer",
            default_program_path=runtime_settings["program_path"],
            robot_ip=runtime_settings["robot_ip"],
            controller_host=runtime_settings["controller_host"],
            status_interval_ms=runtime_settings["status_interval_ms"],
            recipes=recipe_list(),
        )

    @app.get("/robot-diagnostics")
    def robot_diagnostics() -> Any:
        return render_template(
            "robot_diagnostics.html",
            page_title="Robot Diagnostics",
            status_interval_ms=runtime_settings["status_interval_ms"],
        )

    @app.get("/settings")
    def settings() -> Any:
        return render_template("settings.html", page_title="Settings", settings=runtime_settings)

    @app.get("/calibration")
    def calibration() -> Any:
        return render_template("calibration.html", page_title="Calibration")

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
                return "", 204, {"HX-Redirect": "/"}
            return redirect("/")
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
                return "", 204, {"HX-Redirect": "/part-library"}
            return redirect("/part-library")
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
            result = robot_service.upload_load_run(studs, program_path)
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
            run_state_manager.start_next_batch_cycle(
                launch_cycle=robot_service.upload_load_run,
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
        snapshot = get_connection_snapshot()
        try:
            status = robot_service.status()
        except Exception as exc:
            status = {"error": str(exc)}
            return render_template("partials/diagnostics_readout.html", snapshot=snapshot, ok=False, status=status)

        return render_template("partials/diagnostics_readout.html", snapshot=snapshot, ok=True, status=status)

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

    @app.get("/api/health")
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
            result = robot_service.upload_load_run(normalized_studs, program_path)
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
