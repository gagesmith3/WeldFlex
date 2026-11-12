"""PreToolUse guard: no ad-hoc scripts against the live FR-16 controller.

Diagnostic scripts run against the real controller crashed it on 2026-07-28, so
any shell command that would reach the robot must be approved by the user per
run. This hook can't see the network, so it uses two proxies:

* ``backend/app.py`` started without ``WELDFLEX_ROBOT_IP`` pointed at the stub
  (127.0.0.1) or a dead TEST-NET address (192.0.2.1) — the app auto-connects to
  whatever ``.env`` names, which is the real controller.
* a Python script (file, ``-c`` or stdin heredoc) whose source imports one of
  the modules that talk to the controller.

pytest, py_compile and tools/stub_robot.py always pass. Matches return
``permissionDecision: "ask"`` so the user sees the prompt; nothing is blocked
outright. Any internal error exits 0 (fail open) — this is a speed bump, not a
security boundary.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

ROBOT_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:robot_link|robot_service|robot_feed|command_port|"
    r"job_manager|fairino|Robot)\b",
    re.MULTILINE,
)
INLINE_IMPORT = re.compile(
    r"(?:^|[\s\"';(])(?:from|import)\s+(?:robot_link|robot_service|robot_feed|"
    r"command_port|job_manager|fairino|Robot)\b",
    re.MULTILINE,
)
SAFE_IP =re.compile(r"WELDFLEX_ROBOT_IP\s*=\s*[\"']?(?:127\.0\.0\.1|192\.0\.2\.1)\b")
PYTHON = re.compile(r"(?:^|[\s/\\&;|(])(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?[\"']?(?=\s|$)", re.IGNORECASE)
ALWAYS_OK = re.compile(r"-m\s+(?:pytest|py_compile|pip)\b|stub_robot\.py|feed_continuity\.py\s+--help")
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\n|\|")


def ask(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def script_path(segment: str) -> str | None:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    for tok in tokens:
        if tok.lower().endswith(".py"):
            return tok
    return None


def check(command: str, cwd: str) -> None:
    if not PYTHON.search(command):
        return
    for segment in SEGMENT_SPLIT.split(command):
        if not PYTHON.search(segment) or ALWAYS_OK.search(segment):
            continue
        if re.search(r"backend[/\\]app\.py", segment):
            if not SAFE_IP.search(command):
                ask("Starts backend/app.py against the robot IP in .env (the real "
                    "controller). Set WELDFLEX_ROBOT_IP=192.0.2.1 (offline) or "
                    "127.0.0.1 (stub), or approve this live run.")
            continue
        # Inline (-c / heredoc / here-string): the source is in the command text.
        if INLINE_IMPORT.search(command):
            ask("Inline Python imports a robot-facing module (robot_link/"
                "robot_service/fairino/...). Ad-hoc robot probes crashed the "
                "controller on 2026-07-28 — approve only if this run is intended.")
        path = script_path(segment)
        if not path:
            continue
        full = path if os.path.isabs(path) else os.path.join(cwd, path)
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                source = fh.read()
        except OSError:
            continue
        if ROBOT_IMPORT.search(source):
            ask(f"{path} imports a robot-facing module and may talk to the live "
                "FR-16. Ad-hoc robot probes crashed the controller on 2026-07-28 "
                "— approve only if this run is intended.")


def main() -> None:
    try:
        data = json.load(sys.stdin)
        command = (data.get("tool_input") or {}).get("command") or ""
        cwd = data.get("cwd") or os.getcwd()
        check(command, cwd)
    except SystemExit:
        raise
    except Exception:  # fail open
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
