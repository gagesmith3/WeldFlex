"""Generate the single Lua program WeldFlex uploads to the controller.

`programs/WeldFlex.lua` is a template with `--{{MARKER}}` lines; this module
substitutes them and — crucially — reports the line numbers of the loop head and
the cycle-boundary dwell **in the generated text**. The job manager counts cycles
by watching `GetCurrentLine` cross those numbers, so they have to be produced by
the same pass that produces the file rather than searched for afterwards.
"""

from __future__ import annotations

import os
import re
from math import ceil, hypot
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PROGRAM_NAME = "WeldFlex.lua"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "programs" / PROGRAM_NAME

WELD_PROGRAM_NAME = "weld.lua"
WELD_PATH = TEMPLATE_PATH.parent / WELD_PROGRAM_NAME

# Single-point maintenance weld for shop fixture faceplates — see
# build_weld_faceplate_lua() below. Same template-with-markers shape as
# WeldFlex.lua, just one fixed target instead of a stud list.
FACEPLATE_PROGRAM_NAME = "weld_faceplate.lua"
FACEPLATE_TEMPLATE_PATH = TEMPLATE_PATH.parent / FACEPLATE_PROGRAM_NAME

# The no-motion DI monitor and its harness. Same two-file shape as weld.lua: the
# monitor is upload-gated so the controller's post-upload check (which executes
# top-level Lua) cannot start its loop, and the generated harness is the caller
# that sets the sentinel.
IO_MONITOR_PROGRAM_NAME = "io_monitor.lua"
IO_MONITOR_PATH = TEMPLATE_PATH.parent / IO_MONITOR_PROGRAM_NAME
IO_MONITOR_RUN_PROGRAM_NAME = "io_monitor_run.lua"

# Monitor window bounds in ms. Mirrors MONITOR_MAX_MS in programs/io_monitor.lua,
# which clamps the same value controller-side; asserted by tests.
IO_MONITOR_MAX_MS = 300000
IO_MONITOR_DEFAULT_MS = 45000

# Ceiling on the force-ladder rung a caller may ask for, in lbf. This leaves
# margin below FT_LinInsertion's documented 100 N maximum. It mirrors
# PRESS_TARGET_MAX_LBF in programs/weld.lua, which clamps the same value on the
# controller side — this copy exists so a bad number is refused before it is
# uploaded, with a message, instead of silently pressing at the 20 lbf default.
# Nothing enforces the two copies agree across the language boundary, so
# tests/test_lua_builder.py asserts it.
PRESS_LBF_MAX = 22.0

GATE_MODES = ("none", "pause", "di")
ARM_MODES = ("live", "dry")

# Dynamic speed compensation stays opt-in until measurements from the actual
# controller motion path have established a conservative timing model. The
# default rate is deliberately not used while calibration is disabled.
DSC_CALIBRATED_ENV = "WELDFLEX_DSC_CALIBRATED"
DSC_RATE_100_PCT_MMS_ENV = "WELDFLEX_DSC_RATE_100_PCT_MMS"
DSC_FIXED_OVERHEAD_MS_ENV = "WELDFLEX_DSC_FIXED_OVERHEAD_MS"
DSC_SAFETY_MARGIN_MS_ENV = "WELDFLEX_DSC_SAFETY_MARGIN_MS"
FEED_PULSE_MS_ENV = "WELDFLEX_FEED_PULSE_MS"

DSC_DEFAULT_RATE_100_PCT_MMS = 180.0
DSC_DEFAULT_FIXED_OVERHEAD_MS = 0.0
DSC_DEFAULT_SAFETY_MARGIN_MS = 50
FEED_PULSE_MS_DEFAULT = 250
STUD_RELOAD_MS_DEFAULT = 600
STUD_RELOAD_MS_MIN = 1
STUD_RELOAD_MS_MAX = 10_000

# WaitDI(id, status, maxtime_ms, opt). opt=0 means "stop the program and report a
# timeout" — the only safe choice here. opt=1 falls through on timeout, which would
# weld into a part the operator never swapped.
GATE_DI_OPT_ABORT = 0

# `pause` mode emits the controller's own `Pause(num)` instruction (FR Lua manual
# §3.1.3) at the gate, so the *program* holds itself at the cycle boundary. `num`
# is a free-form reason code the manual only ever uses for shop-specific messages
# ("cylinder not in place"); 0 is its documented "no function" value — the pause
# with nothing attached, which is what the vendor's own multi-pass welding example
# (Code 3-49) uses between passes.
#
# This replaced a host-issued ProgramPause fired after the manager saw the cycle
# marker. That could only ever land *near* the boundary, inside the dwell, and on
# 2026-08-06 it did not land at all — the faceplate run welded straight through
# every gate. The host still issues one as a backstop (job_manager._gate), but the
# program no longer depends on it.
PAUSE_GATE_CODE = 0

# The cycle loop's variable and bound, identical in both templates. The pause gate
# is emitted as `if <var> < <count> then` so the last cycle does *not* hold — there
# is no next part to swap in, and a pause nothing releases would strand the run.
# (weld_faceplate.lua still has its home return to do after the last cycle;
# WeldFlex.lua now homes every cycle, including the last, before this gate runs.)
# tests/test_lua_builder.py pins both names.
GATE_LOOP_VAR = "cycleIndex"
GATE_COUNT_VAR = "cycleCount"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _env_bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, _env_int(name, default)))


@dataclass(frozen=True)
class DscCalibration:
    """Conservative time model for a blocking `Lin` move at a speed percent.

    The model is `fixed_overhead_ms + 100000 * distance / (rate * percent)`.
    Calibration must make it a lower bound on actual travel time, so DSC never
    lets a move reach its target before the feeder's reload window closes.
    """

    rate_100_pct_mms: float
    fixed_overhead_ms: float
    safety_margin_ms: int


@dataclass(frozen=True)
class DynamicStudLeg:
    """Generated settings for travel into one non-first stud."""

    speed_pct: int
    wait_ms: int


def default_feed_pulse_ms() -> int:
    """Electrical feeder trigger duration, configured per machine."""
    return _env_bounded_int(FEED_PULSE_MS_ENV, FEED_PULSE_MS_DEFAULT, 1, 10_000)


def default_dsc_calibration() -> DscCalibration | None:
    """Return accepted machine calibration, or None while DSC is uncommissioned."""
    if os.getenv(DSC_CALIBRATED_ENV, "0") != "1":
        return None
    return DscCalibration(
        rate_100_pct_mms=_env_bounded_float(
            DSC_RATE_100_PCT_MMS_ENV,
            DSC_DEFAULT_RATE_100_PCT_MMS,
            1.0,
            5_000.0,
        ),
        fixed_overhead_ms=_env_bounded_float(
            DSC_FIXED_OVERHEAD_MS_ENV,
            DSC_DEFAULT_FIXED_OVERHEAD_MS,
            0.0,
            10_000.0,
        ),
        safety_margin_ms=_env_bounded_int(
            DSC_SAFETY_MARGIN_MS_ENV,
            DSC_DEFAULT_SAFETY_MARGIN_MS,
            0,
            10_000,
        ),
    )


def _dsc_move_time_ms(distance_mm: float, speed_pct: int, calibration: DscCalibration) -> float:
    return calibration.fixed_overhead_ms + (
        100_000.0 * distance_mm / (calibration.rate_100_pct_mms * speed_pct)
    )


def dynamic_stud_legs(
    studs: Sequence[dict],
    stud_reload_ms: int | float | None = None,
    feed_pulse_ms: int | None = None,
    calibration: DscCalibration | None = None,
) -> list[DynamicStudLeg | None]:
    """Choose the fastest safe compensation for each destination stud.

    The reload deadline starts when the feeder pulse starts. The pulse completes
    before this outer program begins its next `Lin`, leaving reload minus pulse
    duration plus a calibration margin for the travel and any residual wait.
    """
    try:
        reload_ms = int(float(STUD_RELOAD_MS_DEFAULT if stud_reload_ms is None else stud_reload_ms))
    except (TypeError, ValueError):
        raise ValueError(f"stud_reload_ms must be an integer, got {stud_reload_ms!r}") from None
    if not STUD_RELOAD_MS_MIN <= reload_ms <= STUD_RELOAD_MS_MAX:
        raise ValueError(
            f"stud_reload_ms must be {STUD_RELOAD_MS_MIN}-{STUD_RELOAD_MS_MAX}, got {reload_ms}"
        )
    if calibration is None:
        calibration = default_dsc_calibration()
    if calibration is None:
        raise ValueError(
            f"Dynamic speed compensation requires {DSC_CALIBRATED_ENV}=1 after hardware calibration"
        )

    pulse_ms = default_feed_pulse_ms() if feed_pulse_ms is None else int(feed_pulse_ms)
    if pulse_ms < 1:
        raise ValueError(f"feed_pulse_ms must be positive, got {pulse_ms}")
    target_move_ms = max(0, reload_ms - pulse_ms + calibration.safety_margin_ms)
    legs: list[DynamicStudLeg | None] = [None]

    for previous_stud, current_stud in zip(studs, studs[1:]):
        distance_mm = hypot(
            float(current_stud["x"]) - float(previous_stud["x"]),
            float(current_stud["y"]) - float(previous_stud["y"]),
        )
        selected_speed = 1
        selected_wait_ms = 0
        for speed_pct in range(100, 0, -1):
            move_time_ms = _dsc_move_time_ms(distance_mm, speed_pct, calibration)
            if move_time_ms >= target_move_ms:
                selected_speed = speed_pct
                break
        else:
            move_time_ms = _dsc_move_time_ms(distance_mm, selected_speed, calibration)
            selected_wait_ms = max(0, ceil(target_move_ms - move_time_ms))
        legs.append(DynamicStudLeg(speed_pct=selected_speed, wait_ms=selected_wait_ms))

    return legs


def default_gate_di() -> int:
    """Controller DI the part-ready signal is wired to. Not yet commissioned."""
    return _env_int("WELDFLEX_GATE_DI", 0)


def default_gate_timeout_ms() -> int:
    return _env_int("WELDFLEX_GATE_TIMEOUT_MS", 300_000)


def default_boundary_ms(gate_mode: str) -> int:
    """Length of the cycle-boundary dwell, in ms.

    The dwell is the only thing that banks a cycle, so it has to be long enough
    that the manager's 250 ms poll cannot step over it.

    `pause` mode keeps a longer one for a different reason than it used to. The
    gate itself is now in the program (`Pause()` — see PAUSE_GATE_CODE), so the
    host no longer has to land a ProgramPause inside the dwell. What the dwell
    buys instead is the *backstop*: the marker banks the cycle at the start of
    the dwell but `Pause()` does not run until the end of it, so this is the
    window `job_manager._gate` waits out before deciding the program did not
    hold and sending a ProgramPause itself.
    """
    if gate_mode == "pause":
        return _env_int("WELDFLEX_BOUNDARY_PAUSE_MS", 3000)
    return _env_int("WELDFLEX_BOUNDARY_MS", 1500)


@dataclass(frozen=True)
class BuiltProgram:
    """Generated program text plus the line numbers the cycle detector needs.

    Line numbers are 1-based indices into `text.splitlines()`.
    """

    text: str
    loop_start_line: int
    cycle_marker_line: int
    gate_line: int
    stud_count: int
    cycles: int
    gate_mode: str
    boundary_ms: int
    program_name: str = PROGRAM_NAME
    # Total line count of this program's own text. `GetCurrentLine` reports
    # weld.lua's *own* line numbers while it runs under NewDofile (weld.lua is
    # ~500 lines; this program is ~100), so CycleTracker uses this as a ceiling
    # to tell a real caller-file sample from an aliased sub-file one. See
    # CycleTracker's docstring.
    program_line_count: int = 0


def format_number(value: float | int) -> str:
    """Format for Lua while keeping integers compact — 373, not 373.0."""
    as_float = float(value)
    if as_float.is_integer():
        return str(int(as_float))
    return f"{as_float:.3f}".rstrip("0").rstrip(".")


def format_lua_string(value: str) -> str:
    """Format a string for Lua with proper double-quote and backslash escaping."""
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _stud_rows(
    studs: Sequence[dict],
    indent: str,
    dynamic_legs: Sequence[DynamicStudLeg | None] | None = None,
) -> list[str]:
    if not studs:
        return [f"{indent}-- (no studs defined for this part)"]
    if dynamic_legs is not None and len(dynamic_legs) != len(studs):
        raise ValueError("dynamic stud leg count must match stud count")

    rows = []
    for index, stud in enumerate(studs):
        row = f"{indent}{{x={format_number(stud['x'])}, y={format_number(stud['y'])}"
        leg = dynamic_legs[index] if dynamic_legs is not None else None
        if leg is not None:
            row += f", s2sSpeed={leg.speed_pct}, s2sWaitMs={leg.wait_ms}"
        rows.append(row + "},")
    return rows


def _gate_rows(gate_mode: str, indent: str, gate_di: int, gate_timeout_ms: int) -> list[str]:
    if gate_mode == "di":
        return [
            f"{indent}-- Inter-cycle gate: wait for the part-ready input.",
            f"{indent}WaitDI({int(gate_di)}, 1, {int(gate_timeout_ms)}, {GATE_DI_OPT_ABORT})",
        ]
    if gate_mode == "pause":
        body = f"{indent}    "
        return [
            f"{indent}-- Inter-cycle gate: the program pauses itself here (gate_mode=pause).",
            f"{indent}-- The host only watches for the paused state and shows Continue; it does",
            f"{indent}-- not have to land a ProgramPause inside the dwell any more.",
            f"{indent}-- Skipped after the last cycle: nothing releases it, and there is no",
            f"{indent}-- next part to swap in.",
            f"{indent}if {GATE_LOOP_VAR} < {GATE_COUNT_VAR} then",
            f"{body}Pause({PAUSE_GATE_CODE})",
            f"{indent}end",
        ]
    return [f"{indent}-- Inter-cycle gate: none (gate_mode=none)."]


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


PRESSURE_LBF_MAP = {
    "low": 17.0,
    "mid": 18.5,
    "high": 20.0,
}


def _parse_pressure(val: float | int | str | None) -> float:
    if val is None or str(val).strip() == "":
        return 20.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return PRESSURE_LBF_MAP.get(str(val).lower().strip(), 20.0)


def build_weldflex_lua(
    studs: Sequence[dict],
    cycles: int,
    gate_mode: str = "pause",
    arm_mode: str = "live",
    template_path: str | os.PathLike | None = None,
    gate_di: int | None = None,
    gate_timeout_ms: int | None = None,
    boundary_ms: int | None = None,
    safe_z: float | int | None = None,
    retract_z: float | int | None = None,
    part_z: float | int | None = None,
    pressure_setting: str | float | int | None = None,
    ft_sensor_num: int = 1,
    stud_type: str | None = None,
    substrate: str | None = None,
    speed: float | int | None = None,
    dsc_enabled: bool = False,
    stud_reload_ms: int | float | None = None,
) -> BuiltProgram:
    """Substitute the template's markers and report the generated line numbers."""
    if gate_mode not in GATE_MODES:
        raise ValueError(f"Unknown gate_mode {gate_mode!r}; expected one of {GATE_MODES}")
    if arm_mode not in ARM_MODES:
        raise ValueError(f"Unknown arm_mode {arm_mode!r}; expected one of {ARM_MODES}")
    cycles = int(cycles)
    if cycles < 1:
        raise ValueError(f"cycles must be >= 1, got {cycles}")

    path = Path(template_path) if template_path else TEMPLATE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Lua template not found: {path}")
    template_lines = path.read_text(encoding="utf-8").splitlines()

    dwell_ms = default_boundary_ms(gate_mode) if boundary_ms is None else int(boundary_ms)
    safe_z_val = 60.0 if safe_z is None else float(safe_z)
    retract_z_val = 10.0 if retract_z is None else float(retract_z)
    part_z_val = 0.0 if part_z is None else float(part_z)
    press_lbf_val = _parse_pressure(pressure_setting)
    ft_sensor_num_val = int(ft_sensor_num)
    if not 1 <= ft_sensor_num_val <= 255:
        raise ValueError(f"ft_sensor_num must be in [1, 255], got {ft_sensor_num!r}")
    stud_type_val = stud_type or "M4"
    substrate_val = substrate or "Mild Steel"
    # Dry runs are for watching travel safely, not production cadence — default
    # them much slower unless the caller asks for a specific speed.
    speed_val = max(1, min(100, int(speed))) if speed is not None else (10 if arm_mode == "dry" else 25)
    feed_pulse_ms = default_feed_pulse_ms()
    dynamic_legs = (
        dynamic_stud_legs(studs, stud_reload_ms, feed_pulse_ms)
        if dsc_enabled
        else None
    )

    out: list[str] = []
    loop_start_line = cycle_marker_line = gate_line = 0
    boundary_seen = False
    feed_pulse_seen = False

    for line in template_lines:
        indent = _indent_of(line)
        if "--{{STUDS}}" in line:
            out.extend(_stud_rows(studs, indent, dynamic_legs))
        elif "--{{CYCLE_COUNT}}" in line:
            out.append(f"{indent}cycleCount = {cycles}")
        elif "--{{BOUNDARY_MS}}" in line:
            out.append(f"{indent}BOUNDARY_MS = {dwell_ms}")
            boundary_seen = True
        elif "--{{ARM_MODE}}" in line:
            out.append(f"{indent}ARM_MODE = {format_lua_string(arm_mode)}")
        elif "--{{SPEED}}" in line:
            out.append(f"{indent}speed = {speed_val}")
        elif "--{{FEED_PULSE_MS}}" in line:
            out.append(f"{indent}FEED_PULSE_MS = {feed_pulse_ms}")
            feed_pulse_seen = True
        elif "--{{SAFE_Z}}" in line:
            out.append(f"{indent}SAFE_Z = {format_number(safe_z_val)}")
        elif "--{{RETRACT_Z}}" in line:
            out.append(f"{indent}RETRACT_Z = {format_number(retract_z_val)}")
        elif "--{{PART_Z}}" in line:
            out.append(f"{indent}PART_Z = {format_number(part_z_val)}")
        elif "--{{PRESS_LBF}}" in line:
            out.append(f"{indent}PRESS_LBF = {format_number(press_lbf_val)}")
        elif "--{{FT_SENSOR_NUM}}" in line:
            out.append(f"{indent}FT_SENSOR_NUM = {ft_sensor_num_val}")
        elif "--{{STUD_TYPE}}" in line:
            out.append(f"{indent}STUD_TYPE = {format_lua_string(stud_type_val)}")
        elif "--{{SUBSTRATE}}" in line:
            out.append(f"{indent}SUBSTRATE = {format_lua_string(substrate_val)}")
        elif "--{{GATE}}" in line:
            gate_line = len(out) + 1
            out.extend(
                _gate_rows(
                    gate_mode,
                    indent,
                    default_gate_di() if gate_di is None else gate_di,
                    default_gate_timeout_ms() if gate_timeout_ms is None else gate_timeout_ms,
                )
            )
        elif "--{{LOOP_START}}" in line:
            # Position markers are consumed, not emitted: no --{{...}} token should
            # survive into the file that gets uploaded.
            out.append(line.replace("--{{LOOP_START}}", "-- cycle loop"))
            loop_start_line = len(out)
        elif "--{{CYCLE_MARKER}}" in line:
            out.append(line.replace("--{{CYCLE_MARKER}}", "-- cycle boundary"))
            cycle_marker_line = len(out)
        else:
            out.append(line)

    missing = [
        name
        for name, value in (
            ("--{{LOOP_START}}", loop_start_line),
            ("--{{CYCLE_MARKER}}", cycle_marker_line),
            ("--{{GATE}}", gate_line),
            ("--{{BOUNDARY_MS}}", boundary_seen),
            ("--{{FEED_PULSE_MS}}", feed_pulse_seen),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"{path.name} is missing required marker(s): {', '.join(missing)}")
    if not (loop_start_line < cycle_marker_line < gate_line):
        raise RuntimeError(
            f"{path.name} markers are out of order: loop_start={loop_start_line} "
            f"cycle_marker={cycle_marker_line} gate={gate_line}"
        )

    return BuiltProgram(
        text="\n".join(out) + "\n",
        loop_start_line=loop_start_line,
        cycle_marker_line=cycle_marker_line,
        gate_line=gate_line,
        stud_count=len(studs),
        cycles=cycles,
        gate_mode=gate_mode,
        program_line_count=len(out),
        boundary_ms=dwell_ms,
    )


def build_weld_faceplate_lua(
    x: float | int,
    y: float | int,
    cycles: int,
    gate_mode: str = "pause",
    arm_mode: str = "live",
    template_path: str | os.PathLike | None = None,
    gate_di: int | None = None,
    gate_timeout_ms: int | None = None,
    boundary_ms: int | None = None,
    safe_z: float | int | None = None,
    part_z: float | int | None = None,
    pressure_setting: str | float | int | None = None,
    ft_sensor_num: int = 1,
    stud_type: str | None = None,
    substrate: str | None = None,
    speed: float | int | None = None,
) -> BuiltProgram:
    """Substitute programs/weld_faceplate.lua's markers.

    Same template-with-markers/line-tracking contract as build_weldflex_lua —
    see that function's docstring and this module's docstring for why the
    line numbers matter. The differences are the single fixed target (two
    scalar markers instead of a stud list) and no HIGH_Z marker, since a
    single-point program has no stud-to-stud travel to clear.
    """
    if gate_mode not in GATE_MODES:
        raise ValueError(f"Unknown gate_mode {gate_mode!r}; expected one of {GATE_MODES}")
    if arm_mode not in ARM_MODES:
        raise ValueError(f"Unknown arm_mode {arm_mode!r}; expected one of {ARM_MODES}")
    cycles = int(cycles)
    if cycles < 1:
        raise ValueError(f"cycles must be >= 1, got {cycles}")

    path = Path(template_path) if template_path else FACEPLATE_TEMPLATE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Lua template not found: {path}")
    template_lines = path.read_text(encoding="utf-8").splitlines()

    dwell_ms = default_boundary_ms(gate_mode) if boundary_ms is None else int(boundary_ms)
    safe_z_val = 10.0 if safe_z is None else float(safe_z)
    part_z_val = 0.0 if part_z is None else float(part_z)
    press_lbf_val = _parse_pressure(pressure_setting)
    ft_sensor_num_val = int(ft_sensor_num)
    if not 1 <= ft_sensor_num_val <= 255:
        raise ValueError(f"ft_sensor_num must be in [1, 255], got {ft_sensor_num!r}")
    stud_type_val = stud_type or "M4"
    substrate_val = substrate or "Mild Steel"
    speed_val = max(1, min(100, int(speed))) if speed is not None else (10 if arm_mode == "dry" else 25)
    x_val = float(x)
    y_val = float(y)

    out: list[str] = []
    loop_start_line = cycle_marker_line = gate_line = 0
    boundary_seen = False

    for line in template_lines:
        indent = _indent_of(line)
        if "--{{FACEPLATE_X}}" in line:
            out.append(f"{indent}faceplateX = {format_number(x_val)}")
        elif "--{{FACEPLATE_Y}}" in line:
            out.append(f"{indent}faceplateY = {format_number(y_val)}")
        elif "--{{CYCLE_COUNT}}" in line:
            out.append(f"{indent}cycleCount = {cycles}")
        elif "--{{BOUNDARY_MS}}" in line:
            out.append(f"{indent}BOUNDARY_MS = {dwell_ms}")
            boundary_seen = True
        elif "--{{ARM_MODE}}" in line:
            out.append(f"{indent}ARM_MODE = {format_lua_string(arm_mode)}")
        elif "--{{SPEED}}" in line:
            out.append(f"{indent}speed = {speed_val}")
        elif "--{{SAFE_Z}}" in line:
            out.append(f"{indent}SAFE_Z = {format_number(safe_z_val)}")
        elif "--{{PART_Z}}" in line:
            out.append(f"{indent}PART_Z = {format_number(part_z_val)}")
        elif "--{{PRESS_LBF}}" in line:
            out.append(f"{indent}PRESS_LBF = {format_number(press_lbf_val)}")
        elif "--{{FT_SENSOR_NUM}}" in line:
            out.append(f"{indent}FT_SENSOR_NUM = {ft_sensor_num_val}")
        elif "--{{STUD_TYPE}}" in line:
            out.append(f"{indent}STUD_TYPE = {format_lua_string(stud_type_val)}")
        elif "--{{SUBSTRATE}}" in line:
            out.append(f"{indent}SUBSTRATE = {format_lua_string(substrate_val)}")
        elif "--{{GATE}}" in line:
            gate_line = len(out) + 1
            out.extend(
                _gate_rows(
                    gate_mode,
                    indent,
                    default_gate_di() if gate_di is None else gate_di,
                    default_gate_timeout_ms() if gate_timeout_ms is None else gate_timeout_ms,
                )
            )
        elif "--{{LOOP_START}}" in line:
            out.append(line.replace("--{{LOOP_START}}", "-- cycle loop"))
            loop_start_line = len(out)
        elif "--{{CYCLE_MARKER}}" in line:
            out.append(line.replace("--{{CYCLE_MARKER}}", "-- cycle boundary"))
            cycle_marker_line = len(out)
        else:
            out.append(line)

    missing = [
        name
        for name, value in (
            ("--{{LOOP_START}}", loop_start_line),
            ("--{{CYCLE_MARKER}}", cycle_marker_line),
            ("--{{GATE}}", gate_line),
            ("--{{BOUNDARY_MS}}", boundary_seen),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"{path.name} is missing required marker(s): {', '.join(missing)}")
    if not (loop_start_line < cycle_marker_line < gate_line):
        raise RuntimeError(
            f"{path.name} markers are out of order: loop_start={loop_start_line} "
            f"cycle_marker={cycle_marker_line} gate={gate_line}"
        )

    return BuiltProgram(
        text="\n".join(out) + "\n",
        loop_start_line=loop_start_line,
        cycle_marker_line=cycle_marker_line,
        gate_line=gate_line,
        stud_count=1,
        cycles=cycles,
        gate_mode=gate_mode,
        program_line_count=len(out),
        boundary_ms=dwell_ms,
        program_name=FACEPLATE_PROGRAM_NAME,
    )


# ---------------------------------------------------------------------------
# Single-stud weld test harness
#
# `programs/weld.lua` is a sub-process, not a program: it reads weldX/weldY/
# Z_CLEARANCE as globals and expects the torch to be parked over the stud
# already, so uploading it alone and pressing run just faults on its own input
# contract. The harness below is the minimum caller that makes one run of it
# observable from the operator UI — frame globals, one approach move, one
# NewDofile. It exists for bring-up; the production path is WeldFlex.lua's
# cycle loop, which does not call weld.lua yet.
# ---------------------------------------------------------------------------

# Reproduced in the harness so a test stud is approached in exactly the frame a
# real cycle uses. Read out of WeldFlex.lua rather than duplicated here — that
# file is the only place motion parameters live, and a silent divergence between
# the two would move the torch somewhere the real program never goes.
FRAME_GLOBALS = ("tool", "blend", "wobj", "offsetEnable", "speed", "Z_CLEARANCE")

# Matches a bare top-level `name = number` assignment. Deliberately anchored at
# column 0: the same names appearing indented inside the cycle loop are not
# declarations.
_ASSIGNMENT_RE = re.compile(r"^(\w+)\s*=\s*(-?\d+(?:\.\d+)?)\s*(?:--.*)?$")


@dataclass(frozen=True)
class BuiltIoMonitor:
    """Generated io_monitor.lua caller plus the window it was built for."""

    text: str
    duration_ms: int
    program_name: str = IO_MONITOR_RUN_PROGRAM_NAME
    monitor_program_name: str = IO_MONITOR_PROGRAM_NAME


def build_io_monitor_lua(duration_ms: int = IO_MONITOR_DEFAULT_MS) -> BuiltIoMonitor:
    """Build the caller that runs programs/io_monitor.lua once.

    Deliberately has no frame globals, no offsets and no motion instruction of
    any kind. This is the whole point of the file: the two weld interlocks were
    previously only observable by starting a weld test, which moves the arm to
    answer a wiring question. Anything that adds a move here gives that back.
    """
    if not (0 < int(duration_ms) <= IO_MONITOR_MAX_MS):
        raise ValueError(
            f"monitor window must be above 0 and no more than {IO_MONITOR_MAX_MS} ms, "
            f"got {duration_ms}"
        )

    lines = [
        "-- Auto-generated by WeldFlex — DI monitor caller.",
        "-- Uploaded fresh on every run and replaced in place. Never edit on the",
        "-- controller; edit backend/lua_builder.py instead.",
        "--",
        f"-- Runs one pass of /fruser/{IO_MONITOR_PROGRAM_NAME}, which reads the two weld",
        "-- interlock inputs into system variables 6 and 7 so the Weld Test page can",
        "-- show them live. NO MOTION: this file and the one it calls issue no move,",
        "-- no force instruction and no digital output.",
        "",
        "-- How long to watch, in ms. io_monitor.lua clamps this.",
        f"IO_MONITOR_MS = {int(duration_ms)}",
        "",
        "-- Upload gate: the controller's post-upload check executes top-level Lua,",
        "-- so io_monitor.lua stays define-only unless its caller publishes this.",
        "IO_MONITOR_RUN = 1",
        "",
        f'NewDofile("/fruser/{IO_MONITOR_PROGRAM_NAME}", 1, 1)',
        "DofileEnd()",
    ]

    return BuiltIoMonitor(text="\n".join(lines) + "\n", duration_ms=int(duration_ms))


def strip_lua_comments(text: str) -> str:
    """Blank out whole-line comments, keeping the line count identical.

    weld.lua is 13 KB, and 57% of that is a header the controller has no use for.
    Every other Lua file this app uploads is under 2 KB, so size is the first
    thing worth ruling out when a transfer is refused.

    Only lines whose first token is `--` are touched. Trailing comments are left
    alone rather than parsed, because telling a real comment from `--` inside a
    string literal needs a lexer, and being wrong corrupts the program.
    """
    return "\n".join("" if line.lstrip().startswith("--") else line
                     for line in text.splitlines()) + "\n"

