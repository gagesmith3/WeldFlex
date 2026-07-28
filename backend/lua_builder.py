"""Generate the single Lua program WeldFlex uploads to the controller.

`programs/WeldFlex.lua` is a template with `--{{MARKER}}` lines; this module
substitutes them and — crucially — reports the line numbers of the loop head and
the cycle-boundary dwell **in the generated text**. The job manager counts cycles
by watching `GetCurrentLine` cross those numbers, so they have to be produced by
the same pass that produces the file rather than searched for afterwards.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PROGRAM_NAME = "WeldFlex.lua"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "programs" / PROGRAM_NAME

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

    out: list[str] = []
    loop_start_line = cycle_marker_line = gate_line = 0

    for line in template_lines:
        indent = _indent_of(line)
        if "--{{STUDS}}" in line:
            out.extend(_stud_rows(studs, indent))
        elif "--{{CYCLE_COUNT}}" in line:
            out.append(f"{indent}cycleCount = {cycles}")
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
    )
