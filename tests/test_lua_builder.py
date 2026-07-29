import re

import pytest

from lua_builder import (
    FRAME_GLOBALS,
    GATE_DI_OPT_ABORT,
    TEMPLATE_PATH,
    WELD_PATH,
    WELD_PROGRAM_NAME,
    build_weld_test_lua,
    build_weldflex_lua,
    format_number,
    read_frame_globals,
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


def test_marker_line_really_is_the_boundary_dwell():
    """The assertion that catches a template edit shifting the marker."""
    built = build_weldflex_lua([{"x": 1, "y": 2}] * 5, cycles=3)
    lines = _lines(built)
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert "for cycleIndex = 1, cycleCount do" in lines[built.loop_start_line - 1]
    assert built.loop_start_line < built.cycle_marker_line < built.gate_line


@pytest.mark.parametrize("n_studs,cycles", [(0, 1), (1, 1), (5, 20), (40, 999)])
def test_marker_lines_track_stud_count(n_studs, cycles):
    built = build_weldflex_lua([{"x": i, "y": i} for i in range(n_studs)], cycles=cycles)
    lines = _lines(built)
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert f"cycleCount = {cycles}" in built.text


def test_pause_mode_gets_a_longer_boundary_dwell():
    """`pause` gates by host-issued ProgramPause *after* seeing the marker, so the
    round trip has to fit inside the dwell. The in-program gates don't pay for it."""
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


# --- weld test harness ------------------------------------------------------


def test_frame_globals_come_from_the_template_not_a_copy():
    """The harness must approach in the frame a real cycle uses. If WeldFlex.lua's
    tool/wobj ever change, the test has to move with them or it is testing a
    different machine setup than production."""
    frame = read_frame_globals()
    assert set(frame) == set(FRAME_GLOBALS)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for name, value in frame.items():
        assert f"{name} = {value}" in template


def test_harness_publishes_welds_contract_and_calls_weld_lua():
    built = build_weld_test_lua(12.5, -3, armed=False)
    assert "weldX = 12.5" in built.text
    assert "weldY = -3" in built.text
    assert f'NewDofile("/fruser/{WELD_PROGRAM_NAME}", 1, 1)' in built.text
    assert "DofileEnd()" in built.text
    # weld.lua expects the torch already parked over the stud at the safe Z.
    assert "PointsOffsetEnable(1, weldX, weldY, Z_CLEARANCE, 0, 0, 0)" in built.text
    assert "PTP(zerozero, speed, -1, 0)" in built.text
    assert "--{{" not in built.text


def test_declarations_precede_the_move_that_uses_them():
    lines = build_weld_test_lua(1, 2).text.splitlines()
    move = next(i for i, l in enumerate(lines) if l.startswith("PointsOffsetEnable"))
    for name in ("weldX", "weldY", "Z_CLEARANCE", "speed"):
        decl = next(i for i, l in enumerate(lines) if l.startswith(f"{name} ="))
        assert decl < move, f"{name} is declared after the move that reads it"


def test_disarmed_is_the_default():
    assert build_weld_test_lua(0, 0).armed is False
    assert "WELD_ARMED = 0" in build_weld_test_lua(0, 0).text
    assert "WELD_ARMED = 1" in build_weld_test_lua(0, 0, armed=True).text


def test_weld_lua_defaults_to_disarmed_when_the_caller_sets_nothing():
    """The harness always publishes WELD_ARMED, but WeldFlex.lua does not call
    weld.lua yet. Whatever calls it next must not strike an arc by omission.

    Asserted as two properties rather than one exact expression: the gate has
    picked up extra conjuncts before (force-test mode added one) and pinning the
    whole line just made this test stale without ever testing the invariant.
    """
    weld = WELD_PATH.read_text(encoding="utf-8")
    line = next(l for l in weld.splitlines() if l.startswith("local DO_WELD_ENABLED"))
    # Requiring equality with 1 is what makes an unset global fall through to
    # disarmed; the `and 1 or 0` tail keeps the result a number rather than nil.
    assert "WELD_ARMED == 1" in line
    assert line.rstrip().endswith("and 1 or 0")


def test_weld_lua_upload_gate_matches_the_harness():
    """The controller's post-upload check executes the uploaded file's top level
    (observed live 2026-07-28: it refused weld.lua with requireContract's own
    error string). weld.lua therefore acts only when the caller publishes
    WELD_RUN = 1, and the harness is that caller. The sentinel name lives in both
    files with nothing enforcing agreement across the language boundary — drift
    would make every upload of weld.lua fail again."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    assert "if WELD_RUN == 1 then" in weld
    assert "WELD_RUN = 1" in build_weld_test_lua(0, 0).text


def test_force_test_mode_agrees_across_the_language_boundary_and_cannot_arm():
    """WELD_FORCE_TEST proves the 20 lbf press before the welder is in the loop.
    Like WELD_RUN, the sentinel lives in both files with nothing enforcing
    agreement, so assert both sides. And a force test must be impossible to arm
    at every layer: the builder refuses the combination outright, and weld.lua
    forces DO0 off even if a caller sets both flags by hand."""
    built = build_weld_test_lua(0, 0, force_test=True)
    assert "WELD_FORCE_TEST = 1" in built.text
    assert built.force_test is True
    assert built.armed is False
    # Default stays off — a plain dry run must exercise the full interlock path.
    assert "WELD_FORCE_TEST = 0" in build_weld_test_lua(0, 0).text

    weld = WELD_PATH.read_text(encoding="utf-8")
    assert "local FORCE_TEST_MODE = (WELD_FORCE_TEST == 1) and 1 or 0" in weld
    assert "(WELD_ARMED == 1 and FORCE_TEST_MODE ~= 1)" in weld

    with pytest.raises(ValueError):
        build_weld_test_lua(0, 0, armed=True, force_test=True)


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
    ):
        assert lua_number(lua_name) == py_number(py_name), f"{lua_name} slot drifted"

    # SetSysVarValue takes id in [1..20] (Robot.py:4689). A slot outside that is
    # not a drift bug but it is still a silently dead telemetry channel.
    slots = [lua_number(n) for n in
             ("SV_PHASE", "SV_LAST_RET", "SV_PRESS_Z0", "SV_PRESS_TRAVEL",
              "SV_PRESS_GUARD")]
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


def test_weld_ready_is_gated_before_the_arm_check_not_inside_it():
    """The caps-at-charge wait has to sit ahead of the WELD_ARMED branch. Behind
    it, a dry run would skip the interlock entirely and stop being a rehearsal of
    the real sequence."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    body = weld.split("local function fireWeld()", 1)[1]
    wait = body.index("waitForWeldReady()")
    arm = body.index("if DO_WELD_ENABLED ~= 1 then")
    assert wait < arm
    # And the seated check comes first — it is the cheaper failure of the two.
    assert body.index("readDI(DI_STUD_ON_WORK)") < wait


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


def test_missing_frame_global_is_a_hard_error(tmp_path):
    source = TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
    stripped = [l for l in source if not l.startswith("wobj =")]
    bad = tmp_path / "WeldFlex.lua"
    bad.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing frame global.*wobj"):
        build_weld_test_lua(0, 0, template_path=bad)
