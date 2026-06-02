from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path


def check_fairino_import() -> tuple[bool, str]:
    try:
        from fairino import Robot  # type: ignore

        has_rpc = hasattr(Robot, "RPC")
        if not has_rpc:
            return False, "Imported fairino, but Robot.RPC is missing."
        return True, "fairino import OK and Robot.RPC is present."
    except Exception as exc:
        return False, f"fairino import failed: {exc}"


def check_app_health() -> tuple[bool, str]:
    try:
        from backend.app import create_app

        app = create_app()
        with app.test_client() as client:
            response = client.get("/api/health")
            payload = response.get_json(silent=True) or {}
            ok = response.status_code == 200 and payload.get("ok") is True
            if not ok:
                return False, f"/api/health failed: status={response.status_code}, payload={payload}"
        return True, "/api/health check passed via Flask test client."
    except Exception as exc:
        return False, f"App bootstrap failed: {exc}"


def main() -> int:
    workspace_root = Path(__file__).resolve().parents[1]

    summary = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cwd": str(Path.cwd()),
        "workspace": str(workspace_root),
        "env": {
            "WELDFLEX_FAIRINO_PATH": os.getenv("WELDFLEX_FAIRINO_PATH", ""),
            "WELDFLEX_ROBOT_IP": os.getenv("WELDFLEX_ROBOT_IP", ""),
            "WELDFLEX_CONTROLLER_HOST": os.getenv("WELDFLEX_CONTROLLER_HOST", ""),
        },
        "checks": [],
    }

    fairino_ok, fairino_msg = check_fairino_import()
    summary["checks"].append({"name": "fairino_import", "ok": fairino_ok, "message": fairino_msg})

    app_ok, app_msg = check_app_health()
    summary["checks"].append({"name": "app_health", "ok": app_ok, "message": app_msg})

    print(json.dumps(summary, indent=2))

    return 0 if fairino_ok and app_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
