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
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PROGRAM_NAME = "WeldFlex.lua"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "programs" / PROGRAM_NAME

WELD_PROGRAM_NAME = "weld.lua"
WELD_PATH = TEMPLATE_PATH.parent / WELD_PROGRAM_NAME

WELD_TEST_PROGRAM_NAME = "weld_test.lua"

GATE_MODES = ("none", "pause", "di")

# WaitDI(id, status, maxtime_ms, opt). opt=0 means "stop the program and report a
# timeout" — the only safe choice here. opt=1 falls through on timeout, which would
# weld into a part the operator never swapped.
GATE_DI_OPT_ABORT = 0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def default_gate_di() -> int:
    """Controller DI the part-ready signal is wired to. Not yet commissioned."""
    return _env_int("WELDFLEX_GATE_DI", 0)


def default_gate_timeout_ms() -> int:
    return _env_int("WELDFLEX_GATE_TIMEOUT_MS", 300_000)


def default_boundary_ms(gate_mode: str) -> int:
    """Length of the cycle-boundary dwell, in ms.

    The dwell is the only thing that banks a cycle, so it has to be long enough
    that the manager's 250 ms poll cannot step over it. In `pause` mode it also
    has to be long enough for a host-issued ProgramPause to *land* — the manager
    only sends it after seeing the marker, so the round trip has to fit inside
    the dwell or the robot is already moving to the next part's first stud when
    the pause takes. The other modes hold in the program itself and don't pay for it.
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


def format_number(value: float | int) -> str:
    """Format for Lua while keeping integers compact — 373, not 373.0."""
    as_float = float(value)
    if as_float.is_integer():
        return str(int(as_float))
    return f"{as_float:.3f}".rstrip("0").rstrip(".")


def _stud_rows(studs: Sequence[dict], indent: str) -> list[str]:
    if not studs:
        return [f"{indent}-- (no studs defined for this part)"]
    return [
        f"{indent}{{x={format_number(s['x'])}, y={format_number(s['y'])}}},"
        for s in studs
    ]


def _gate_rows(gate_mode: str, indent: str, gate_di: int, gate_timeout_ms: int) -> list[str]:
    if gate_mode == "di":
        return [
            f"{indent}-- Inter-cycle gate: wait for the part-ready input.",
            f"{indent}WaitDI({int(gate_di)}, 1, {int(gate_timeout_ms)}, {GATE_DI_OPT_ABORT})",
        ]
    if gate_mode == "pause":
        # Nothing is emitted: the manager issues ProgramPause when it observes the
        # cycle edge. Kept as a comment so the uploaded file says which mode built it.
        return [f"{indent}-- Inter-cycle gate: host-driven pause (gate_mode=pause)."]
    return [f"{indent}-- Inter-cycle gate: none (gate_mode=none)."]


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def build_weldflex_lua(
    studs: Sequence[dict],
    cycles: int,
    gate_mode: str = "pause",
    template_path: str | os.PathLike | None = None,
    gate_di: int | None = None,
    gate_timeout_ms: int | None = None,
    boundary_ms: int | None = None,
) -> BuiltProgram:
    """Substitute the template's markers and report the generated line numbers."""
    if gate_mode not in GATE_MODES:
        raise ValueError(f"Unknown gate_mode {gate_mode!r}; expected one of {GATE_MODES}")
    cycles = int(cycles)
    if cycles < 1:
        raise ValueError(f"cycles must be >= 1, got {cycles}")

    path = Path(template_path) if template_path else TEMPLATE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Lua template not found: {path}")
    template_lines = path.read_text(encoding="utf-8").splitlines()

    dwell_ms = default_boundary_ms(gate_mode) if boundary_ms is None else int(boundary_ms)

    out: list[str] = []
    loop_start_line = cycle_marker_line = gate_line = 0
    boundary_seen = False

    for line in template_lines:
        indent = _indent_of(line)
        if "--{{STUDS}}" in line:
            out.extend(_stud_rows(studs, indent))
        elif "--{{CYCLE_COUNT}}" in line:
            out.append(f"{indent}cycleCount = {cycles}")
        elif "--{{BOUNDARY_MS}}" in line:
            out.append(f"{indent}BOUNDARY_MS = {dwell_ms}")
            boundary_seen = True
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
        boundary_ms=dwell_ms,
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
class BuiltWeldTest:
    """Generated harness text plus what it was built to do."""

    text: str
    frame: dict[str, str]
    weld_x: float
    weld_y: float
    armed: bool
    force_test: bool
    program_name: str = WELD_TEST_PROGRAM_NAME
    weld_program_name: str = WELD_PROGRAM_NAME


def strip_lua_comments(text: str) -> str:
    """Blank out whole-line comments, keeping the line count identical.

    weld.lua is 13 KB, and 57% of that is a header the controller has no use for.
    Every other Lua file this app uploads is under 2 KB, so size is the first
    thing worth ruling out when a transfer is refused.

    Blanked, not deleted, on purpose: the weld-test trace reads GetCurrentLine
    against weld.lua's line numbers, so a stripped copy that renumbered the file
    would make the one signal this page exists to collect unreadable.

    Only lines whose first token is `--` are touched. Trailing comments are left
    alone rather than parsed, because telling a real comment from `--` inside a
    string literal needs a lexer, and being wrong corrupts the program.
    """
    return "\n".join("" if line.lstrip().startswith("--") else line
                     for line in text.splitlines()) + "\n"


def read_frame_globals(template_path: str | os.PathLike | None = None) -> dict[str, str]:
    """Pull the frame/motion globals out of the WeldFlex.lua template.

    Values are returned as source text, not numbers, so `Z_CLEARANCE = 10` stays
    `10` in the generated file instead of becoming `10.0`.
    """
    path = Path(template_path) if template_path else TEMPLATE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Lua template not found: {path}")

    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        # The loop is the end of the declaration block; anything after it is body.
        if line.startswith("for "):
            break
        match = _ASSIGNMENT_RE.match(line)
        if match and match.group(1) in FRAME_GLOBALS:
            found[match.group(1)] = match.group(2)

    missing = [name for name in FRAME_GLOBALS if name not in found]
    if missing:
        raise RuntimeError(
            f"{path.name} is missing frame global(s) the weld test harness needs: "
            f"{', '.join(missing)}"
        )
    return found


def build_weld_test_lua(
    weld_x: float,
    weld_y: float,
    armed: bool = False,
    force_test: bool = False,
    template_path: str | os.PathLike | None = None,
) -> BuiltWeldTest:
    """Build a harness that welds exactly one stud at (weld_x, weld_y).

    `armed=False` publishes WELD_ARMED = 0, which runs the whole search / press /
    hold / retract / feed sequence with weld.lua's DO0 pulse suppressed. That is
    the default because an unset or mistyped arm flag must never be the thing
    that strikes an arc.

    `force_test=True` publishes WELD_FORCE_TEST = 1: search, press to weld force,
    hold it long enough to watch, retract — DI1 reported but not enforced, WELD
    and FEED never reached. For proving the press before the welder is in the
    loop. weld.lua forces DO0 off in this mode regardless of WELD_ARMED, and an
    armed force test is refused here too rather than silently disarmed.
    """
    if armed and force_test:
        raise ValueError("a force test cannot be armed — drop one of the two flags")

    frame = read_frame_globals(template_path)
    x = format_number(weld_x)
    y = format_number(weld_y)
    armed_flag = 1 if armed else 0

    lines = [
        f"-- Auto-generated by WeldFlex — single-stud weld test harness.",
        f"-- Uploaded fresh on every run and replaced in place. Never edit on the",
        f"-- controller; edit backend/lua_builder.py instead.",
        "--",
        f"-- Runs one pass of /fruser/{WELD_PROGRAM_NAME} at a single point, so the",
        "-- weld sub-process can be exercised without loading a part or a cycle count.",
        "",
        "-- Frame selection, copied from WeldFlex.lua so the test approaches in the",
        "-- same frame a production cycle does.",
    ]
    lines += [f"{name} = {frame[name]}" for name in FRAME_GLOBALS]
    lines += [
        "",
        "-- weld.lua's input contract. weldX/weldY are globals because a NewDofile'd",
        "-- chunk cannot see the caller's locals.",
        f"weldX = {x}",
        f"weldY = {y}",
        "",
        (
            "-- 1 = fire DO0 for real. 0 = run everything else and suppress the pulse."
            if armed_flag
            else "-- 0 = run everything else and suppress the weld pulse (dry run)."
        ),
        f"WELD_ARMED = {armed_flag}",
        "",
        "-- 1 = press verification only: hold weld force, then retract; the weld",
        "-- pulse and feed are never reached and DO0 is forced off.",
        f"WELD_FORCE_TEST = {1 if force_test else 0}",
        "",
        "-- Upload gate: the controller's post-upload check executes top-level Lua,",
        "-- so weld.lua stays define-only unless its caller publishes WELD_RUN = 1.",
        "WELD_RUN = 1",
        "",
        "-- Park over the test point at the safe Z, exactly as the cycle loop does.",
        "PointsOffsetEnable(1, weldX, weldY, Z_CLEARANCE, 0, 0, 0)",
        "PTP(zerozero, speed, -1, 0)",
        "PointsOffsetDisable()",
        "",
        f'NewDofile("/fruser/{WELD_PROGRAM_NAME}", 1, 1)',
        "DofileEnd()",
    ]

    return BuiltWeldTest(
        text="\n".join(lines) + "\n",
        frame=frame,
        weld_x=float(weld_x),
        weld_y=float(weld_y),
        armed=bool(armed),
        force_test=bool(force_test),
    )
