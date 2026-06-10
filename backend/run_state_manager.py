from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable


CYCLE_LOST_TIMEOUT_S = 30.0
CYCLE_UNKNOWN_ONLINE_TIMEOUT_S = 6.0


class RunStateManager:
    def __init__(self) -> None:
        self._state: dict[str, Any] = {
            "part_name": "No part selected",
            "requested_run_count": 0,
            "completed_runs": 0,
            "active": False,
            "cycle_running": False,
            "cycle_seen_running": False,
            "cycle_stopped_since": 0.0,
            "cycle_unknown_since": 0.0,
            "started_at": "",
            "last_command": "No command sent yet",
            "last_command_status": "idle",
            "last_command_at": "",
        }
        self._studs: list[dict[str, float]] = []
        self._lock = threading.Lock()

    @staticmethod
    def _utc_now_str() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def stamp_command_state(self, command: str, status: str, detail: str = "") -> None:
        timestamp = self._utc_now_str()
        with self._lock:
            self._state["last_command"] = f"{command}: {detail}" if detail else command
            self._state["last_command_status"] = status
            self._state["last_command_at"] = timestamp

    def has_remaining_runs(self) -> bool:
        with self._lock:
            requested = int(self._state.get("requested_run_count", 0) or 0)
            completed = int(self._state.get("completed_runs", 0) or 0)
            active = bool(self._state.get("active", False))
        return active and completed < requested

    def stage_batch(self, part_name: str, run_count: int, studs: list[dict[str, float]]) -> None:
        with self._lock:
            self._state.update(
                {
                    "part_name": part_name,
                    "requested_run_count": run_count,
                    "completed_runs": 0,
                    "active": True,
                    "cycle_running": False,
                    "cycle_seen_running": False,
                    "cycle_stopped_since": 0.0,
                    "cycle_unknown_since": 0.0,
                    "started_at": self._utc_now_str(),
                }
            )
            self._studs = list(studs)

    def start_designer_cycle(self) -> None:
        with self._lock:
            self._state.update(
                {
                    "part_name": "Unsaved Designer Job",
                    "requested_run_count": 1,
                    "completed_runs": 0,
                    "active": True,
                    "cycle_running": True,
                    "cycle_seen_running": False,
                    "cycle_stopped_since": 0.0,
                    "cycle_unknown_since": 0.0,
                    "started_at": self._utc_now_str(),
                }
            )

    def stop_batch(self) -> None:
        with self._lock:
            self._studs = []
            self._state.update(
                {
                    "active": False,
                    "cycle_running": False,
                    "cycle_seen_running": False,
                    "cycle_stopped_since": 0.0,
                    "cycle_unknown_since": 0.0,
                }
            )

    def start_next_batch_cycle(
        self,
        launch_cycle: Callable[[list[dict[str, float]], str], Any],
        program_path: str,
    ) -> None:
        with self._lock:
            if not self._state.get("active", False):
                raise ValueError("No active batch. Start one from Part Library.")
            if self._state.get("cycle_running", False):
                raise ValueError("A cycle is currently running.")

            requested = int(self._state.get("requested_run_count", 0) or 0)
            completed = int(self._state.get("completed_runs", 0) or 0)
            part_name = self._state.get("part_name", "Part")

            if completed >= requested:
                self._state["active"] = False
                raise ValueError("Batch is already complete.")
            if not self._studs:
                raise ValueError("Missing batch stud data. Start the batch again from Part Library.")

            cycle_number = completed + 1
            self._state["cycle_running"] = True
            self._state["cycle_seen_running"] = False
            self._state["cycle_stopped_since"] = 0.0
            self._state["cycle_unknown_since"] = 0.0
            self._state["last_command"] = f"Run: Starting cycle {cycle_number}/{requested} for {part_name}"
            self._state["last_command_status"] = "ok"
            self._state["last_command_at"] = self._utc_now_str()
            studs = list(self._studs)

        try:
            launch_result = launch_cycle(studs, program_path)
        except Exception:
            with self._lock:
                self._state["cycle_running"] = False
                self._state["cycle_seen_running"] = False
                self._state["cycle_stopped_since"] = 0.0
            raise

        if isinstance(launch_result, dict) and launch_result.get("direct_cycle_completed"):
            with self._lock:
                completed = int(self._state.get("completed_runs", 0) or 0) + 1
                requested = int(self._state.get("requested_run_count", 0) or 0)
                self._state["completed_runs"] = completed
                self._state["cycle_running"] = False
                self._state["cycle_seen_running"] = False
                self._state["cycle_stopped_since"] = 0.0
                self._state["cycle_unknown_since"] = 0.0
                if completed >= requested:
                    self._state["active"] = False
                    self._state["last_command"] = f"Run: Batch complete ({completed}/{requested})"
                else:
                    self._state["last_command"] = f"Run: Cycle complete ({completed}/{requested})"
                self._state["last_command_status"] = "ok"
                self._state["last_command_at"] = self._utc_now_str()
            return

        with self._lock:
            self._state["last_command"] = f"Run: Cycle {cycle_number}/{requested} started for {part_name}"
            self._state["last_command_status"] = "ok"
            self._state["last_command_at"] = self._utc_now_str()

    def _refresh_cycle_completion(self, program_state: str, current_line: Any, status_connected: bool | None = None) -> None:
        now = time.monotonic()

        line_number: int | None = None
        try:
            line_number = int(current_line)
        except (TypeError, ValueError):
            line_number = None

        if not self._state.get("cycle_running", False):
            return

        if program_state in {"running", "paused"}:
            self._state["cycle_seen_running"] = True
            self._state["cycle_stopped_since"] = 0.0
            self._state["cycle_unknown_since"] = 0.0
            return

        if program_state != "stopped":
            # Unknown states can occur from transient status-read hiccups.
            # Only start/advance lost-connection timeout when we have an explicit offline signal.
            self._state["cycle_stopped_since"] = 0.0
            unknown_since = float(self._state.get("cycle_unknown_since", 0.0) or 0.0)
            if unknown_since <= 0.0:
                self._state["cycle_unknown_since"] = now
                return

            unknown_duration = now - unknown_since
            if status_connected is False:
                if unknown_duration > CYCLE_LOST_TIMEOUT_S:
                    self._state["cycle_running"] = False
                    self._state["cycle_seen_running"] = False
                    self._state["cycle_stopped_since"] = 0.0
                    self._state["cycle_unknown_since"] = 0.0
                    self._state["active"] = False
                    self._state["last_command"] = "Run: Aborted — robot connection lost during cycle"
                    self._state["last_command_status"] = "error"
                    self._state["last_command_at"] = self._utc_now_str()
            elif status_connected is True:
                # Controller is reachable but state is not running/paused/stopped.
                # If this persists right after launch, treat as failed start instead of hanging forever.
                if (not self._state.get("cycle_seen_running", False)) and unknown_duration > CYCLE_UNKNOWN_ONLINE_TIMEOUT_S:
                    self._state["cycle_running"] = False
                    self._state["cycle_seen_running"] = False
                    self._state["cycle_stopped_since"] = 0.0
                    self._state["cycle_unknown_since"] = 0.0
                    self._state["active"] = False
                    self._state["last_command"] = "Run: Failed to start - controller state unknown while online"
                    self._state["last_command_status"] = "error"
                    self._state["last_command_at"] = self._utc_now_str()
            else:
                # Connection certainty unknown (e.g. transient status endpoint error).
                # Keep waiting without forcing a stop.
                pass
            return

        self._state["cycle_unknown_since"] = 0.0

        if not self._state.get("cycle_seen_running", False):
            stopped_since = float(self._state.get("cycle_stopped_since", 0.0) or 0.0)
            if stopped_since <= 0.0:
                self._state["cycle_stopped_since"] = now
                return
            # If program never leaves "stopped" shortly after launch, mark the cycle as failed to start.
            if now - stopped_since > 4.0:
                self._state["cycle_running"] = False
                self._state["cycle_seen_running"] = False
                self._state["cycle_stopped_since"] = 0.0
                self._state["cycle_unknown_since"] = 0.0
                self._state["active"] = False
                self._state["last_command"] = "Run: Failed to start - program remained stopped"
                self._state["last_command_status"] = "error"
                self._state["last_command_at"] = self._utc_now_str()
            return

        stopped_since = float(self._state.get("cycle_stopped_since", 0.0) or 0.0)
        if stopped_since <= 0.0:
            self._state["cycle_stopped_since"] = now
            return

        stopped_duration = now - stopped_since
        line_reset = line_number is not None and line_number <= 1
        if not line_reset and stopped_duration < 4.0:
            return

        completed = int(self._state.get("completed_runs", 0) or 0) + 1
        requested = int(self._state.get("requested_run_count", 0) or 0)
        self._state["completed_runs"] = completed
        self._state["cycle_running"] = False
        self._state["cycle_seen_running"] = False
        self._state["cycle_stopped_since"] = 0.0

        if completed >= requested:
            self._state["active"] = False
            self._state["last_command"] = f"Run: Batch complete ({completed}/{requested})"
        else:
            self._state["last_command"] = f"Run: Cycle complete ({completed}/{requested})"
        self._state["last_command_status"] = "ok"
        self._state["last_command_at"] = self._utc_now_str()

    def current_summary(self, program_state: str, current_line: Any, status_connected: bool | None = None) -> dict[str, Any]:
        with self._lock:
            self._refresh_cycle_completion(program_state, current_line, status_connected)

            can_pause = program_state in {"running", "paused"}
            if not can_pause and not self._state.get("active", False):
                self._state["active"] = False

            requested = int(self._state.get("requested_run_count", 0) or 0)
            completed = int(self._state.get("completed_runs", 0) or 0)
            active = bool(self._state.get("active", False))
            cycle_running = bool(self._state.get("cycle_running", False))
            started_at = self._state.get("started_at") or "-"
            last_command = self._state.get("last_command") or "No command sent yet"
            last_command_status = self._state.get("last_command_status") or "idle"
            last_command_at = self._state.get("last_command_at") or "-"
            current_part = self._state.get("part_name") or "No part selected"

        progress_pct = int((completed / requested) * 100) if requested > 0 else 0
        progress_pct = max(0, min(progress_pct, 100))
        can_stop = active or can_pause
        can_run_next = active and not cycle_running and completed < requested
        start_button_label = "Run" if active else "Start From Library"

        return {
            "current_part": current_part,
            "requested_run_count": requested,
            "completed_runs": completed,
            "progress_percent": progress_pct,
            "progress_label": f"{completed}/{requested} completed - {progress_pct}%" if requested > 0 else "0/0 completed - 0%",
            "program_state": program_state,
            "can_pause": can_pause,
            "can_stop": can_stop,
            "can_run_next": can_run_next,
            "start_button_label": start_button_label,
            "cycle_running": cycle_running,
            "started_at": started_at,
            "last_command": last_command,
            "last_command_status": last_command_status,
            "last_command_at": last_command_at,
        }
