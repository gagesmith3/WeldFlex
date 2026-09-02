import re

import pytest

from lua_builder import (
    DscCalibration,
    DynamicStudLeg,
    FACEPLATE_TEMPLATE_PATH,
    GATE_DI_OPT_ABORT,
    IO_MONITOR_DEFAULT_MS,
    IO_MONITOR_MAX_MS,
    IO_MONITOR_PATH,
    PAUSE_GATE_CODE,
    PRESS_LBF_MAX,
    TEMPLATE_PATH,
    WELD_PATH,
    WELD_PROGRAM_NAME,
    build_io_monitor_lua,
    build_weld_faceplate_lua,
    build_weldflex_lua,
    dynamic_stud_legs,
    format_lua_string,
    format_number,
    strip_lua_comments,
)


def _lines(built):
    return built.text.splitlines()


def test_studs_and_cycle_count_are_substituted():
    built = build_weldflex_lua(
        [{"x": 10, "y": -20.5}, {"x": 0, "y": 0}, {"x": 373, "y": 1.25}], cycles=7
    )
    assert "{x=10, y=-20.5}," in built.text
    assert "{x=0, y=0}," in built.text
    # Integers stay compact — 373, not 373.0.
    assert "{x=373, y=1.25}," in built.text
    assert "cycleCount = 7" in built.text
    assert built.stud_count == 3
    assert built.cycles == 7
    # Nothing unsubstituted may reach the controller.
    assert "--{{" not in built.text


def test_weldflex_lua_substitutes_recipe_parameters():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}],
        cycles=1,
        safe_z=60.0,
        retract_z=15.5,
        part_z=2.0,
        pressure_setting="low",
        stud_type="M6",
        substrate="Stainless Steel",
        speed=42,
    )
    assert "SAFE_Z = 60" in built.text
    assert "RETRACT_Z = 15.5" in built.text
    assert "PART_Z = 2" in built.text
    assert "PRESS_LBF = 17" in built.text
    assert 'STUD_TYPE = "M6"' in built.text
    assert 'SUBSTRATE = "Stainless Steel"' in built.text
    assert "speed = 42" in built.text
    assert "--{{" not in built.text


def test_dynamic_stud_legs_choose_fastest_speed_that_meets_reload_window():
    calibration = DscCalibration(
        rate_100_pct_mms=200.0,
        fixed_overhead_ms=150.0,
        safety_margin_ms=0,
    )
    legs = dynamic_stud_legs(
        [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 120, "y": 0}],
        stud_reload_ms=600,
        feed_pulse_ms=250,
        calibration=calibration,
    )

    assert legs == [
        None,
        # 20 mm at 50% is 350 ms; 51% would arrive too early.
        DynamicStudLeg(50, 0),
        # Even 100% takes 650 ms, so this longest leg stays at full speed.
        DynamicStudLeg(100, 0),
    ]


def test_dynamic_stud_legs_wait_after_a_very_short_move():
    calibration = DscCalibration(
        rate_100_pct_mms=200.0,
        fixed_overhead_ms=0.0,
        safety_margin_ms=0,
    )
    legs = dynamic_stud_legs(
        [{"x": 0, "y": 0}, {"x": 0.1, "y": 0}],
        stud_reload_ms=600,
        feed_pulse_ms=250,
        calibration=calibration,
    )

    assert legs[1].speed_pct == 1
    assert legs[1].wait_ms == 300


def test_dynamic_stud_legs_require_an_accepted_machine_calibration(monkeypatch):
    monkeypatch.delenv("WELDFLEX_DSC_CALIBRATED", raising=False)

    with pytest.raises(ValueError, match="WELDFLEX_DSC_CALIBRATED=1"):
        dynamic_stud_legs([{"x": 0, "y": 0}, {"x": 20, "y": 0}])


def test_weldflex_lua_emits_dynamic_stud_to_stud_travel(monkeypatch):
    monkeypatch.setenv("WELDFLEX_DSC_CALIBRATED", "1")
    monkeypatch.setenv("WELDFLEX_DSC_RATE_100_PCT_MMS", "200")
    monkeypatch.setenv("WELDFLEX_DSC_FIXED_OVERHEAD_MS", "150")
    monkeypatch.setenv("WELDFLEX_DSC_SAFETY_MARGIN_MS", "0")
    monkeypatch.setenv("WELDFLEX_FEED_PULSE_MS", "250")

    built = build_weldflex_lua(
        [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 120, "y": 0}],
        cycles=1,
        dsc_enabled=True,
        stud_reload_ms=600,
    )

    assert "{x=0, y=0}," in built.text
    assert "{x=20, y=0, s2sSpeed=50, s2sWaitMs=0}," in built.text
    assert "{x=120, y=0, s2sSpeed=100, s2sWaitMs=0}," in built.text
    assert "FEED_PULSE_MS = 250" in built.text
    assert "Lin(zerozero, travelSpeed, -1, 0, 0)" in built.text
    assert "WELD_FEED_PULSE_MS = FEED_PULSE_MS" in built.text


def test_weldflex_lua_keeps_legacy_travel_when_dsc_is_disabled(monkeypatch):
    monkeypatch.delenv("WELDFLEX_DSC_CALIBRATED", raising=False)

    built = build_weldflex_lua(
        [{"x": 0, "y": 0}, {"x": 20, "y": 0}],
        cycles=1,
        dsc_enabled=False,
    )

    assert ", s2sSpeed=" not in built.text
    assert ", s2sWaitMs=" not in built.text


def test_weld_lua_uses_the_builder_feed_pulse_when_supplied():
    weld = WELD_PATH.read_text(encoding="utf-8")

    assert "type(WELD_FEED_PULSE_MS) == \"number\"" in weld
    assert "WELD_FEED_PULSE_MS >= 1" in weld
    assert "WELD_FEED_PULSE_MS <= 10000" in weld
    assert "WaitMs(FEED_PULSE_MS)" in weld


def test_weldflex_lua_publishes_part_z_for_weld_retraction():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}], cycles=1, part_z=63.5, retract_z=25.4
    )
    assert "PART_Z = 63.5" in built.text
    assert "WELD_RETRACT_Z = RETRACT_Z" in built.text
    assert "WELD_PART_Z = PART_Z" in built.text


def test_weldflex_lua_keeps_part_designer_x_y_order():
    built = build_weldflex_lua([{"x": 10, "y": 20}], cycles=1)

    assert "weldX = stud.x" in built.text
    assert "weldY = stud.y" in built.text
    assert "weldX = stud.y" not in built.text
    assert "weldY = stud.x" not in built.text


def test_weldflex_lua_uses_independent_retract_and_safe_heights():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}, {"x": 30, "y": 40}],
        cycles=1,
        safe_z=60.0,
        retract_z=10.0,
        part_z=2.0,
    )
    assert "APPROACH_Z = PART_Z + RETRACT_Z" in built.text
    assert "HIGH_Z = PART_Z + SAFE_Z" in built.text
    assert "local travelZ = APPROACH_Z" in built.text
    assert "if lastWeldX == nil or lastWeldY == nil then" in built.text
    assert "travelZ = HIGH_Z" in built.text
    assert "PointsOffsetEnable(0, weldX, weldY, travelZ, 0, 0, 0)" in built.text
    assert built.text.count("Lin(homewf, speed, -1, 0, 0)") == 2
    assert "PointsOffsetEnable(0, 0, 0, APPROACH_Z, 0, 0, 0)" not in built.text


def test_weldflex_lua_uses_global_point_offsets_without_inline_lin_offsets():
    built = build_weldflex_lua([{"x": 10, "y": 20}, {"x": 30, "y": 40}], cycles=1)

    assert built.text.count("Lin(homewf, speed, -1, 0, 0)") == 2
    assert "Lin(zerozero, travelSpeed, -1, 0, 0)" in built.text
    assert built.text.count("Lin(zerozero, speed, -1, 0, 0)") == 2
    assert "Lin(zerozero, travelSpeed, -1, 0, 1)" not in built.text


def test_weldflex_lua_returns_home_every_cycle_before_the_gate():
    """The operator needs the head clear of the part to swap it, on every
    cycle boundary — not just once after the whole run finishes. The Lua
    loop body is only emitted once in the text (the runtime `for` loop
    repeats it), so the home-return block must sit *inside* the loop, before
    the boundary dwell/gate, rather than appearing once after it.
    """
    built = build_weldflex_lua([{"x": 10, "y": 20}], cycles=3)
    lines = built.text.splitlines()
    # One initial move to home before the loop, one return-home block inside it.
    assert built.text.count("Lin(homewf, speed, -1, 0, 0)") == 2
    home_idxs = [i for i, l in enumerate(lines, 1) if "Lin(homewf" in l]
    # The in-loop home return precedes that cycle's boundary dwell and gate.
    assert home_idxs[-1] < built.cycle_marker_line < built.gate_line
    # Nothing homes after the loop any more — it is done every cycle instead.
    assert not any(i > built.gate_line for i in home_idxs)




def test_weldflex_lua_substitutes_numeric_pressure_setting():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}],
        cycles=1,
        pressure_setting="22.5",
    )
    assert "PRESS_LBF = 22.5" in built.text


def test_weldflex_lua_supports_live_and_dry_run_arming():
    live = build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, arm_mode="live")
    dry = build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, arm_mode="dry", speed=42)
    assert 'ARM_MODE = "live"' in live.text
    assert 'ARM_MODE = "dry"' in dry.text
    assert "WELD_ARMED = 1" in live.text
    assert "WELD_ARMED = 0" in dry.text
    assert "speed = 42" in dry.text


def test_faceplate_lua_uses_the_goto_coordinate_and_arming_contract():
    built = build_weld_faceplate_lua(150, 381, cycles=1, arm_mode="live", part_z=5, safe_z=10)

    assert "weldX = faceplateX" in built.text
    assert "weldY = faceplateY" in built.text
    assert "WELD_ARMED = 0" in built.text
    assert 'if ARM_MODE == "live" then' in built.text
    assert "Z_CLEARANCE = PART_Z + SAFE_Z" in built.text
    assert "PointsOffsetEnable(0, faceplateX, faceplateY, APPROACH_Z, 0, 0, 0)" in built.text
    assert "PointsOffsetEnable(1," not in built.text
    assert "PTP(zerozero, speed, -1, 0)" in built.text
    assert "Lin(zerozero" not in built.text


def test_marker_line_really_is_the_boundary_dwell():
    """The assertion that catches a template edit shifting the marker."""
    built = build_weldflex_lua([{"x": 1, "y": 2}] * 5, cycles=3)
    lines = _lines(built)
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert "for cycleIndex = 1, cycleCount do" in lines[built.loop_start_line - 1]
    assert built.loop_start_line < built.cycle_marker_line < built.gate_line


def test_program_line_count_matches_the_built_text_and_stays_under_weld_lua():
    """job_manager's CycleTracker uses program_line_count as a ceiling so a

    NewDofile-aliased sample from inside weld.lua (which reports its own line
    numbers, ~500 of them) can never be mistaken for a caller-file line. That
    only works as long as weld.lua stays longer than any caller program — pin
    both halves of the invariant here.
    """
    built = build_weldflex_lua([{"x": 1, "y": 2}] * 5, cycles=3)
    assert built.program_line_count == len(_lines(built))
    weld_lua_lines = len(WELD_PATH.read_text(encoding="utf-8").splitlines())
    assert built.program_line_count < weld_lua_lines


@pytest.mark.parametrize("n_studs,cycles", [(0, 1), (1, 1), (5, 20), (40, 999)])
def test_marker_lines_track_stud_count(n_studs, cycles):
    built = build_weldflex_lua([{"x": i, "y": i} for i in range(n_studs)], cycles=cycles)
    lines = _lines(built)
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert f"cycleCount = {cycles}" in built.text


def test_pause_mode_gets_a_longer_boundary_dwell():
    """The cycle banks at the marker — the start of the dwell — and `Pause()` is
    the line after it, so the dwell is the window job_manager._gate waits out
    before deciding the program did not hold and sending a ProgramPause itself."""
    paused = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, gate_mode="pause")
    for mode in ("none", "di"):
        other = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, gate_mode=mode)
        assert paused.boundary_ms > other.boundary_ms

    assert f"BOUNDARY_MS = {paused.boundary_ms}" in paused.text
    # The declaration has to precede the loop that uses it.
    lines = _lines(paused)
    decl = next(i for i, l in enumerate(lines, 1) if l.startswith("BOUNDARY_MS ="))
    assert decl < paused.loop_start_line


def test_boundary_dwell_can_be_overridden_explicitly():
    built = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, gate_mode="pause", boundary_ms=8000)
    assert built.boundary_ms == 8000
    assert "BOUNDARY_MS = 8000" in built.text


def test_di_gate_emits_waitdi_that_aborts_on_timeout():
    built = build_weldflex_lua(
        [{"x": 1, "y": 1}], cycles=2, gate_mode="di", gate_di=6, gate_timeout_ms=45000
    )
    gate = _lines(built)[built.gate_line - 1:built.gate_line + 1]
    joined = "\n".join(gate)
    assert f"WaitDI(6, 1, 45000, {GATE_DI_OPT_ABORT})" in joined
    # opt=0 stops the program on timeout. Falling through would weld into a part
    # the operator never swapped.
    assert GATE_DI_OPT_ABORT == 0


@pytest.mark.parametrize(
    "builder", [
        lambda mode: build_weldflex_lua([{"x": 1, "y": 1}], cycles=4, gate_mode=mode),
        lambda mode: build_weld_faceplate_lua(1, 1, cycles=4, gate_mode=mode),
    ],
    ids=["weldflex", "faceplate"],
)
def test_pause_gate_holds_in_the_program_and_skips_the_last_cycle(builder):
    """2026-08-06. The gate used to be nothing but a comment — the manager saw
    the marker and *then* sent a ProgramPause, which on hardware never landed:
    the faceplate run welded, retracted, opened DO1 and drove straight back down
    into the next cycle. The hold has to be an instruction the program executes.

    The last cycle is deliberately exempt. Nothing releases a pause the manager
    never gates on (`done < target`). weld_faceplate.lua still has its home
    return to do after the loop; WeldFlex.lua now homes every cycle, including
    the last, before this gate ever runs.
    """
    built = builder("pause")
    lines = built.text.splitlines()
    gate = "\n".join(lines[built.gate_line - 1:built.gate_line + 8])

    assert "if cycleIndex < cycleCount then" in gate
    assert f"Pause({PAUSE_GATE_CODE})" in gate
    # Exactly one pause in the whole program, sitting past the boundary dwell.
    pause_lines = [i for i, l in enumerate(lines, 1) if re.match(r"^\s*Pause\(", l)]
    assert len(pause_lines) == 1
    assert built.cycle_marker_line < pause_lines[0]

    # ...and none of it survives into a mode that isn't gating that way.
    for mode in ("none", "di"):
        assert "Pause(" not in builder(mode).text


@pytest.mark.parametrize("mode", ["none", "pause"])
def test_non_di_gates_emit_no_motion(mode):
    built = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, gate_mode=mode)
    assert "WaitDI" not in built.text
    assert built.gate_mode == mode


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        build_weldflex_lua([], cycles=1, gate_mode="nope")
    with pytest.raises(ValueError):
        build_weldflex_lua([], cycles=0)


def test_missing_marker_is_a_hard_error(tmp_path):
    bad = tmp_path / "WeldFlex.lua"
    bad.write_text("studs = {\n--{{STUDS}}\n}\n--{{CYCLE_COUNT}}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing required marker"):
        build_weldflex_lua([], cycles=1, template_path=bad)


def test_dropping_only_the_boundary_marker_still_fails_loudly(tmp_path):
    """Otherwise the dwell silently reverts to the template's literal and the gate
    window quietly shrinks — the kind of thing that is invisible until hardware."""
    source = TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
    stripped = [l for l in source if "--{{BOUNDARY_MS}}" not in l]
    bad = tmp_path / "WeldFlex.lua"
    bad.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing required marker.*BOUNDARY_MS"):
        build_weldflex_lua([], cycles=1, template_path=bad)


def test_format_number():
    assert format_number(373) == "373"
    assert format_number(373.0) == "373"
    assert format_number(-20.5) == "-20.5"
    assert format_number(1.250) == "1.25"
    assert format_number(100.001) == "100.001"


def test_format_lua_string():
    assert format_lua_string("M4") == '"M4"'
    assert format_lua_string('1/4"') == r'"1/4\""'
    assert format_lua_string(r"C:\test") == r'"C:\\test"'
    assert format_lua_string("line1\nline2") == r'"line1\nline2"'


def test_weldflex_lua_escapes_quotes_in_stud_type_and_substrate():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}],
        cycles=1,
        stud_type='1/4"',
        substrate='Stainless "316"',
    )
    assert r'STUD_TYPE = "1/4\""' in built.text
    assert r'SUBSTRATE = "Stainless \"316\""' in built.text


# --- weld test harness ------------------------------------------------------


def test_the_press_force_ceiling_agrees_across_the_language_boundary():
    """lua_builder refuses what weld.lua would clamp. If the two drift apart the
    host starts accepting a rung the controller quietly presses at 20 lbf
    instead — the operator asks for one force and gets another, with nothing
    reporting the substitution.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    m = re.search(r"^local PRESS_TARGET_MAX_LBF\s*=\s*([\d.]+)", weld, re.M)
    assert m, "weld.lua no longer declares PRESS_TARGET_MAX_LBF"
    assert float(m.group(1)) == PRESS_LBF_MAX


def test_the_press_guard_declines_below_its_own_threshold():
    """weld.lua only widens collision detection for a press that needs it."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    assert re.search(r"^local PRESS_GUARD_MIN_N\s*=\s*([\d.]+)", weld, re.M), \
        "weld.lua no longer gates the collision guard on the press target"
    assert re.search(r"^local pressNeedsGuard\s*=\s*PRESS_TARGET_N\s*>=\s*PRESS_GUARD_MIN_N",
                     weld, re.M), "the guard is no longer gated on the press target"


def test_force_control_uses_negative_fz_for_compression():
    """The regulator needs a signed target; insertion only needs a magnitude."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    control = re.search(r"local function ftControlPress\(flag\)(.*?)\nend", weld, re.S)
    assert control, "weld.lua no longer defines the FT_Control helper"
    assert "0.0, 0.0, -PRESS_TARGET_N, 0.0, 0.0, 0.0" in control.group(1)


def test_a_fault_does_not_erase_which_collision_lever_took():
    """fault() runs forceControlOff() on its way out, which releases the collision
    guard. The release used to publish GUARD_RELEASED unconditionally, so every
    faulted press reported "released" for the one slot that says whether the fix
    applied at all — destroying the evidence on the only runs anyone reads it on.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    assert re.search(r"^local faulting = false", weld, re.M), \
        "weld.lua no longer tracks whether a fault is unwinding"

    code = strip_lua_comments(weld)
    body = code.split("local function fault(msg, site)", 1)[1].split("\nend", 1)[0]
    assert body.index("faulting = true") < body.index("forceControlOff()"), \
        "fault() releases the guard before freezing its telemetry"


def test_weld_lua_upload_gate_matches_weldflex():
    weld = WELD_PATH.read_text(encoding="utf-8")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "if WELD_RUN == 1 then" in weld
    assert "WELD_RUN = 1" in template


def test_dry_run_executes_every_phase_except_the_arc_pulse():
    """A dry run must handle a real stud exactly like production, except DO0 stays off."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))

    def function_body(name, next_name):
        return code.split(f"local function {name}()", 1)[1].split(
            f"local function {next_name}()", 1
        )[0]

    readiness = function_body("waitForWeldReady", "requireContract")
    search = function_body("searchForStud", "pressToForce")
    press = function_body("pressToForce", "fireWeld")
    fire = function_body("fireWeld", "holdAfterWeld")
    feed = function_body("feedNextStud", "weldOneStud")

    assert "while readDI(DI_WELD_READY) ~= 1 do" in readiness
    assert "FT_FindSurface" in search
    assert "FT_Control" in press and "FT_LinInsertion" in press
    assert "writeDO(DO_FEED, 1)" in feed
    for phase in (readiness, search, press, feed):
        assert "WELD_ARMED" not in phase

    assert fire.index("if WELD_ARMED ~= 1 then") < fire.index("writeDO(DO_WELD, 1)")
    assert fire.index("return") < fire.index("writeDO(DO_WELD, 1)")


def test_weld_di_map_agrees_across_the_language_boundary():
    """DI1 is stud-on-work — continuity welder -> work surface -> gun. DI0 is the
    welder's caps-at-charge ready line. They are not interchangeable, and weld.lua
    and app.py each hold their own copy of the numbers, so drift between them is
    silent: the page would report one input while the program gated on the other.

    app.py is read as text rather than imported — importing it starts the robot
    link, and this assertion does not need a robot.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    app_src = (WELD_PATH.parents[1] / "backend" / "app.py").read_text(encoding="utf-8")

    def lua_const(name):
        m = re.search(rf"^local {name}\s*=\s*(\d+)", weld, re.M)
        assert m, f"weld.lua no longer declares {name}"
        return m.group(1)

    def py_default(name, env):
        m = re.search(rf'^{name} = int\(os\.getenv\("{env}", "(\d+)"\)\)', app_src, re.M)
        assert m, f"app.py no longer declares {name}"
        return m.group(1)

    assert lua_const("DI_STUD_ON_WORK") == "1"
    assert lua_const("DI_WELD_READY") == "0"
    assert py_default("WELD_STUD_DI", "WELDFLEX_WELD_STUD_DI") == lua_const("DI_STUD_ON_WORK")
    assert py_default("WELD_READY_DI", "WELDFLEX_WELD_READY_DI") == lua_const("DI_WELD_READY")

    # Third copy: io_monitor.lua reads the same two inputs into the same two
    # slots. It is the file an operator uses to decide which physical wire is
    # which, so it drifting from weld.lua would answer that question wrongly —
    # the worst possible failure for this particular file.
    monitor = IO_MONITOR_PATH.read_text(encoding="utf-8")

    def monitor_const(name):
        m = re.search(rf"^local {name}\s*=\s*(\d+)", monitor, re.M)
        assert m, f"io_monitor.lua no longer declares {name}"
        return m.group(1)

    for name in ("DI_STUD_ON_WORK", "DI_WELD_READY", "SV_STUD_ON_WORK", "SV_WELD_READY"):
        assert monitor_const(name) == lua_const(name), \
            f"{name} drifted between weld.lua and io_monitor.lua"


def test_feed_do_agrees_across_the_language_boundary():
    """/ui/faceplate/feed writes the stud-feeder output from the host, so app.py
    now holds a copy of a number weld.lua owns as DO_FEED — the same silent-drift
    hazard the DI map has. A wrong number here does not error; it energizes some
    other output the operator never asked to actuate.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    app_src = (WELD_PATH.parents[1] / "backend" / "app.py").read_text(encoding="utf-8")

    lua = re.search(r"^local DO_FEED\s*=\s*(\d+)", weld, re.M)
    assert lua, "weld.lua no longer declares DO_FEED"
    py = re.search(r'^FEED_DO = int\(os\.getenv\("WELDFLEX_FEED_DO", "(\d+)"\)\)', app_src, re.M)
    assert py, "app.py no longer declares FEED_DO"
    assert lua.group(1) == "1"
    assert py.group(1) == lua.group(1)


def test_the_io_monitor_issues_no_motion():
    """The monitor exists so a wiring question does not require moving the arm.

    Asserted rather than trusted to review: this file is reached from a button an
    operator presses to check a wire, and a move sneaking into it would be a
    surprise arm motion from a control that promises none. Covers both the
    monitor and the generated caller that runs it.
    """
    monitor = strip_lua_comments(IO_MONITOR_PATH.read_text(encoding="utf-8"))
    caller = build_io_monitor_lua().text

    forbidden = ("PTP(", "Lin(", "Arc(", "Circle(", "Spline", "MoveL", "MoveJ",
                 "PointsOffsetEnable", "FT_Control", "FT_LinInsertion",
                 "FT_FindSurface", "SetDO", "SPLCSetDO", "SetToolDO")
    for token in forbidden:
        assert token not in monitor, f"io_monitor.lua issues {token} — it must not move or actuate"
        assert token not in caller, f"the monitor caller issues {token} — it must not move or actuate"


def test_the_io_monitor_window_is_bounded_on_both_sides():
    """An unbounded loop would be the nicer control, but the controller's
    post-upload check executes top-level Lua and it is not established whether
    that follows a NewDofile. If it does, an unbounded monitor would hang the
    upload of its own caller with nothing able to interrupt it.
    """
    monitor = IO_MONITOR_PATH.read_text(encoding="utf-8")
    m = re.search(r"^local MONITOR_MAX_MS\s*=\s*(\d+)", monitor, re.M)
    assert m, "io_monitor.lua no longer clamps the monitor window"
    assert int(m.group(1)) == IO_MONITOR_MAX_MS, "monitor ceiling drifted from lua_builder"

    for bad in (0, -1, IO_MONITOR_MAX_MS + 1):
        with pytest.raises(ValueError):
            build_io_monitor_lua(bad)

    assert f"IO_MONITOR_MS = {IO_MONITOR_DEFAULT_MS}" in build_io_monitor_lua().text
    assert "IO_MONITOR_RUN = 1" in build_io_monitor_lua().text


def test_weld_phase_codes_agree_across_the_language_boundary():
    """weld.lua publishes a phase code to a controller system variable and app.py
    decodes it to a label. Same silent-drift risk as the DI map: nothing connects
    the two tables, and a mismatch would render the Weld Test page's most
    important tile as "unknown (31)" exactly when someone is debugging a stalled
    press.

    Only the codes are compared. The wording of the labels is presentation and is
    free to change.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    app_src = (WELD_PATH.parents[1] / "backend" / "app.py").read_text(encoding="utf-8")

    lua_codes = {
        int(m.group(2))
        for m in re.finditer(r"^local (PH_\w+)\s*=\s*(\d+)", weld, re.M)
        if m.group(1) != "PH_FAULT_BASE"
    }
    assert lua_codes, "weld.lua no longer declares PH_* phase codes"

    table = app_src.split("_WELD_PHASES = {", 1)[1].split("}", 1)[0]
    py_codes = {int(m.group(1)) for m in re.finditer(r"^\s*(\d+):", table, re.M)}

    assert lua_codes == py_codes, (
        f"phase codes drifted — only in weld.lua: {sorted(lua_codes - py_codes)}, "
        f"only in app.py: {sorted(py_codes - lua_codes)}"
    )

    # The fault codes are 90 + beacon site, so the offset has to agree too.
    lua_base = re.search(r"^local PH_FAULT_BASE\s*=\s*(\d+)", weld, re.M)
    py_base = re.search(r"^_WELD_FAULT_BASE = (\d+)", app_src, re.M)
    assert lua_base and py_base, "the fault-code base is no longer declared on both sides"
    assert lua_base.group(1) == py_base.group(1)


def test_weld_telemetry_slots_and_press_budget_agree_across_the_language_boundary():
    """weld.lua publishes to numbered controller system variables and app.py reads
    those numbers back. Same silent-drift risk as the DI map and the phase codes,
    with a nastier failure: a slot mismatch does not error, it reads whatever the
    other slot holds, so the page would show a confident wrong number.

    PRESS_MAX_MM is checked for the same reason. app.py compares the measured
    travel against its own copy to decide whether the press stopped on force or
    ran to its budget — and that verdict is what separates a working regulator
    from a press driving blind on a wrong FTC_SENSOR_NUM. If the two copies
    drift, the page says the press had room to spare while it was pinned.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    app_src = (WELD_PATH.parents[1] / "backend" / "app.py").read_text(encoding="utf-8")

    def lua_number(name):
        m = re.search(rf"^local {name}\s*=\s*([\d.]+)", weld, re.M)
        assert m, f"weld.lua no longer declares {name}"
        return float(m.group(1))

    def py_number(name):
        m = re.search(rf"^{name} = ([\d.]+)", app_src, re.M)
        assert m, f"app.py no longer declares {name}"
        return float(m.group(1))

    for lua_name, py_name in (
        ("SV_PHASE", "WELD_SV_PHASE"),
        ("SV_LAST_RET", "WELD_SV_LAST_RET"),
        ("SV_PRESS_Z0", "WELD_SV_PRESS_Z0"),
        ("SV_PRESS_TRAVEL", "WELD_SV_PRESS_TRAVEL"),
        ("SV_PRESS_GUARD", "WELD_SV_PRESS_GUARD"),
        ("SV_STUD_ON_WORK", "WELD_SV_STUD_ON_WORK"),
        ("SV_WELD_READY", "WELD_SV_WELD_READY"),
        ("SV_PRESS_LBF", "WELD_SV_PRESS_LBF"),
    ):
        assert lua_number(lua_name) == py_number(py_name), f"{lua_name} slot drifted"

    # SetSysVarValue takes id in [1..20] (Robot.py:4689). A slot outside that is
    # not a drift bug but it is still a silently dead telemetry channel.
    slots = [lua_number(n) for n in
             ("SV_PHASE", "SV_LAST_RET", "SV_PRESS_Z0", "SV_PRESS_TRAVEL",
              "SV_PRESS_GUARD", "SV_STUD_ON_WORK", "SV_WELD_READY", "SV_PRESS_LBF")]
    assert all(1 <= s <= 20 for s in slots), f"system variable slot out of range: {slots}"
    assert len(set(slots)) == len(slots), f"two telemetry values share a slot: {slots}"

    assert lua_number("PRESS_MAX_MM") == py_number("WELD_PRESS_MAX_MM")


def test_the_collision_guard_codes_agree_across_the_language_boundary():
    """weld.lua's pressCollisionGuard() publishes a code saying which
    collision-threshold instruction the press actually got, and app.py turns that
    number into the words on the page.

    This one earns a test more than the others do. Neither instruction has ever
    been called on this cell, so the code is the only evidence of whether the fix
    applied — and the two readings it distinguishes point in opposite directions.
    "not applied" means the firmware has no such instruction and the press never
    got its headroom; anything else means it did and 300 N of TCP threshold was
    still not enough. Mislabel that and the next run is debugged backwards.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    app_src = (WELD_PATH.parents[1] / "backend" / "app.py").read_text(encoding="utf-8")

    lua_codes = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^local GUARD_(\w+)\s*=\s*(\d+)", weld, re.M)
    }
    assert lua_codes, "weld.lua no longer declares the GUARD_* codes"

    table = re.search(r"_WELD_GUARD_CODES = \{(.*?)\}", app_src, re.S)
    assert table, "app.py no longer declares _WELD_GUARD_CODES"
    py_codes = {int(n) for n in re.findall(r"^\s*(\d+):", table.group(1), re.M)}

    assert set(lua_codes.values()) == py_codes, (
        f"guard codes drifted — weld.lua publishes {sorted(lua_codes.values())}, "
        f"app.py decodes {sorted(py_codes)}"
    )

    # The "it never ran" code is singled out on the page, so app.py holds a second
    # copy of that number. Two copies of the same constant is two chances to drift.
    py_not_applied = re.search(r"^_WELD_GUARD_NOT_APPLIED = (\d+)", app_src, re.M)
    assert py_not_applied, "app.py no longer names the not-applied code"
    assert lua_codes["NONE"] == int(py_not_applied.group(1))


def test_the_press_travel_budgets_are_separate_constants():
    """FT_Control's max_dis and FT_LinInsertion's dismax meter different things —
    the regulator starts spending max_dis when force control is enabled, before
    the insertion move begins — and they were one shared constant until the press
    was suspected of running both to their cap (live 2026-07-29). That theory was
    wrong — tripling the budget changed nothing, and the fault turned out to be
    the collision monitor — but the two budgets still meter different things.

    Aliasing them again would make either one untunable: raising the insertion's
    budget would silently raise the regulator's too. Asserted structurally rather
    than by value so the numbers stay free to be tightened after bring-up.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    assert re.search(r"^local PRESS_ADJUST_MM\s*=", weld, re.M), \
        "FT_Control's max_dis no longer has its own constant"
    assert "PRESS_ADJUST_MM, 0.0,                      -- max_dis (mm), max_ang" in weld, \
        "FT_Control is no longer given PRESS_ADJUST_MM"
    assert "PRESS_SPEED_MMS, 0.0, PRESS_MAX_MM, PRESS_DIR" in weld, \
        "FT_LinInsertion is no longer given PRESS_MAX_MM"


def test_the_controller_never_receives_a_protected_call():
    """The controller refuses the whole file if `pcall` reaches it — live 2026-07-29:
    "lua_name:weld.lua---line_num:297---error_info:pcall is not allowed in lua file".
    It is a whole-file rejection, so one defensive wrapper anywhere makes the
    program unuploadable. Checked against what is actually sent (comments blanked),
    which is why the header may still discuss the ban by name."""
    sent = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    assert "pcall" not in sent, "the controller will refuse this upload outright"


def test_weld_lua_never_lets_telemetry_fault_a_run():
    """pub() writes to a controller system variable whose argument form is
    unverified (the Lua manual's own example is OCR-mangled). Diagnostics must
    never be the reason a torch stops mid-press — and the usual guard is banned,
    so the safety has to be in the call itself: a bare number is accepted whether
    the binding wants an id (Robot.py's SetSysVarValue) or a name (Table 3-12),
    whereas the string form throws against the former."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    body = weld.split("local function pub(", 1)[1].split("\nend", 1)[0]
    # It must give up rather than raise when the instruction is absent entirely.
    assert "if setter == nil then return end" in body
    assert "setter(slot, value)" in body
    assert "s_var_" not in body

    # The no-throw argument only holds if every call site passes (number, number).
    code = strip_lua_comments(weld)
    for call in re.findall(r"(?<!function )\bpub\(([^)]*)\)", code):
        slot, _, value = call.partition(",")
        assert slot.strip().startswith("SV_"), f"pub() slot is not a numeric constant: {call}"
        assert '"' not in value, f"pub() value is not numeric: {call}"


def test_ft_returns_outside_the_vendor_error_space_are_not_refusals():
    """FAIRINO's error-code table (SDK manual 2.5) is -7..-1, then 0 = "Successful
    call", then 3..207. It contains no 1 and no 2. This firmware nonetheless
    returns 1 from an FT_FindSurface that physically succeeded (live 2026-07-29),
    and every earlier "found the surface and immediately retracted" was weld.lua
    faulting on that 1 and running its own retract. So the refusal rule has to be
    the vendor's error space — negative or >= 3 — not "nonzero"."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    body = strip_lua_comments(weld).split("local function ftRefused(", 1)[1].split("\nend", 1)[0]

    assert "ret ~= 0" not in body, (
        "ftRefused is back to treating any nonzero return as a refusal — that "
        "faults on the 1 FT_FindSurface returns on success"
    )
    assert "ret < 0" in body and "ret >= 3" in body, (
        "ftRefused no longer bounds refusals to the vendor's error-code space"
    )


def test_weld_lua_reads_no_force_from_lua():
    """Controller Lua has no force-read instruction — FT_GetForceTorqueRCS appears
    nowhere in the FR Lua manual; it is Python-SDK-only. Calling it is what made
    the press unrunnable, so it must not come back. The host reads force over RPC.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    code = strip_lua_comments(weld)
    assert "FT_GetForceTorqueRCS" not in code


def test_weld_ready_is_gated_before_search_and_press():
    """The caps-at-charge wait sits ahead of searchForStud in weldOneStud."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    body = weld.split("local function weldOneStud()", 1)[1]
    wait = body.index("waitForWeldReady()")
    search = body.index("searchForStud()")
    assert wait < search


def test_stripping_comments_never_changes_the_line_count():
    """The weld-test trace reads GetCurrentLine against weld.lua's line numbers.
    A stripped copy that renumbered the file would make that unreadable."""
    source = WELD_PATH.read_text(encoding="utf-8")
    stripped = strip_lua_comments(source)
    assert len(stripped.splitlines()) == len(source.splitlines())
    assert len(stripped.encode()) < len(source.encode())
    # Every executable line survives verbatim, at the same index.
    for before, after in zip(source.splitlines(), stripped.splitlines()):
        assert after == ("" if before.lstrip().startswith("--") else before)


def test_stripping_leaves_trailing_comments_alone():
    """Telling a real trailing comment from `--` inside a string needs a lexer.
    Getting that wrong corrupts the program, so it is not attempted."""
    src = 'local DO_FEED = 1        -- feeder\nprint("a -- b")\n-- gone\n'
    out = strip_lua_comments(src)
    assert "local DO_FEED = 1        -- feeder" in out
    assert 'print("a -- b")' in out
    assert "-- gone" not in out


# --- weld_faceplate.lua -----------------------------------------------------


def test_faceplate_target_and_cycle_count_are_substituted():
    built = build_weld_faceplate_lua(120.5, -45.0, cycles=7)
    assert "faceplateX = 120.5" in built.text
    assert "faceplateY = -45" in built.text
    assert "cycleCount = 7" in built.text
    assert built.stud_count == 1
    assert built.cycles == 7
    assert built.program_name == "weld_faceplate.lua"
    # Nothing unsubstituted may reach the controller.
    assert "--{{" not in built.text


def test_faceplate_lua_substitutes_recipe_parameters():
    built = build_weld_faceplate_lua(
        0, 0, cycles=1,
        safe_z=15.5, part_z=2.0, pressure_setting="low",
        stud_type="M6", substrate="Stainless Steel",
    )
    assert "SAFE_Z = 15.5" in built.text
    assert "PART_Z = 2" in built.text
    assert "PRESS_LBF = 17" in built.text
    assert 'STUD_TYPE = "M6"' in built.text
    assert 'SUBSTRATE = "Stainless Steel"' in built.text


def test_faceplate_marker_line_really_is_the_boundary_dwell():
    """Same load-bearing assertion as WeldFlex.lua's — the job manager counts
    cycles by watching GetCurrentLine cross these lines."""
    built = build_weld_faceplate_lua(1, 2, cycles=3)
    lines = built.text.splitlines()
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert "for cycleIndex = 1, cycleCount do" in lines[built.loop_start_line - 1]
    assert built.loop_start_line < built.cycle_marker_line < built.gate_line


def test_faceplate_program_line_count_stays_under_weld_lua():
    """Same invariant as WeldFlex.lua's — see
    test_program_line_count_matches_the_built_text_and_stays_under_weld_lua.
    weld_faceplate.lua has no inner stud loop, so it is even shorter, and the
    margin against weld.lua's ~500 lines is even wider."""
    built = build_weld_faceplate_lua(1, 2, cycles=3)
    assert built.program_line_count == len(built.text.splitlines())
    weld_lua_lines = len(WELD_PATH.read_text(encoding="utf-8").splitlines())
    assert built.program_line_count < weld_lua_lines


@pytest.mark.parametrize("cycles", [1, 5, 999])
def test_faceplate_marker_lines_track_cycle_count(cycles):
    built = build_weld_faceplate_lua(1, 1, cycles=cycles)
    lines = built.text.splitlines()
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert f"cycleCount = {cycles}" in built.text


def test_faceplate_lua_returns_home_once_after_the_last_cycle_only():
    """weld_faceplate.lua goes straight to the fixture and stays there for
    every cycle — no home approach before the loop starts — but returns home
    exactly once, after the loop closes, not between cycles. Checked against
    what's actually sent (comments blanked), since the file's own header
    discusses the design by name."""
    built = build_weld_faceplate_lua(10, 20, cycles=4)
    code = strip_lua_comments(built.text)
    lines = code.splitlines()

    home_idxs = [i for i, l in enumerate(lines) if "homewf" in l]
    assert home_idxs, "weld_faceplate.lua no longer returns home at all"
    # No home reference anywhere before the loop starts...
    assert all(i >= built.loop_start_line for i in home_idxs)
    # ...and none inside the loop body either — only after its gate/boundary
    # machinery, i.e. after every cycle (successful or fault-broken) is done.
    assert all(i > (built.gate_line - 1) for i in home_idxs)


def test_faceplate_lua_builds_cleanly():
    """Faceplate maintenance runs build without DO1 hold logic."""
    built = build_weld_faceplate_lua(10, 20, cycles=2)
    text = built.text
    assert "SetDO(1, 1, 0, 0)" not in text
    assert "WELD_SKIP_FEED = 1" not in text
    assert 'NewDofile("/fruser/weld.lua", 1, 1)' in text



def test_weld_lua_honours_the_skip_feed_sentinel():
    """weld.lua's own timed DO1 pulse must not also fire on a faceplate run —
    that would advance the real automated stud feeder in addition to the held
    DO1 signal the faceplate program manages itself."""
    weld = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    assert "if WELD_SKIP_FEED ~= 1 then" in weld
    assert "feedNextStud()" in weld


def test_weldflex_lua_never_sets_the_skip_feed_sentinel():
    """WELD_SKIP_FEED is faceplate-only. If WeldFlex.lua ever started setting
    it, real part runs would silently stop feeding studs."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "WELD_SKIP_FEED" not in template


def test_faceplate_lua_upload_gate_matches_weldflex():
    weld = WELD_PATH.read_text(encoding="utf-8")
    template = FACEPLATE_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "if WELD_RUN == 1 then" in weld
    assert "WELD_RUN = 1" in template


def test_faceplate_lua_never_receives_a_protected_call():
    sent = strip_lua_comments(FACEPLATE_TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert "pcall" not in sent, "the controller will refuse this upload outright"


def test_faceplate_missing_marker_is_a_hard_error(tmp_path):
    bad = tmp_path / "weld_faceplate.lua"
    bad.write_text("faceplateX = 0 --{{FACEPLATE_X}}\n--{{CYCLE_COUNT}}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing required marker"):
        build_weld_faceplate_lua(0, 0, cycles=1, template_path=bad)


def test_faceplate_rejects_bad_gate_mode_or_cycles():
    with pytest.raises(ValueError):
        build_weld_faceplate_lua(0, 0, cycles=1, gate_mode="nope")
    with pytest.raises(ValueError):
        build_weld_faceplate_lua(0, 0, cycles=0)


