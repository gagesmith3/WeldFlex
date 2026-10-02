import re

import pytest

from lua_builder import (
    ARM_MODES,
    DscCalibration,
    DynamicStudLeg,
    GATE_DI_OPT_ABORT,
    IO_MONITOR_DEFAULT_MS,
    IO_MONITOR_MAX_MS,
    IO_MONITOR_PATH,
    PAUSE_GATE_CODE,
    PRESS_LBF_MAX,
    SINGLE_SHOT_TEMPLATE_PATH,
    TEMPLATE_PATH,
    WELD_PATH,
    WELD_PROGRAM_NAME,
    RunMode,
    build_io_monitor_lua,
    build_single_shot_lua,
    build_weldflex_lua,
    check_travel_heights,
    dynamic_stud_legs,
    format_lua_string,
    format_number,
    strip_lua_comments,
)
from part_origin import CornerRef
from weld_tuning import (
    DEFAULT_PRESS_SPEED_MMS,
    DEFAULT_SEARCH_SPEED_MMS,
    PRESS_SPEED_MAX_MMS,
    PRESS_SPEED_MIN_MMS,
    SEARCH_SPEED_MAX_MMS,
    SEARCH_SPEED_MIN_MMS,
)

# Most tests here are about something other than the run mode; they build live.
LIVE = RunMode("live")
DRY = RunMode("dry")


def _lines(built):
    return built.text.splitlines()


def test_studs_and_cycle_count_are_substituted():
    built = build_weldflex_lua(
        [{"x": 10, "y": -20.5}, {"x": 0, "y": 0}, {"x": 373, "y": 1.25}], cycles=7, run_mode=LIVE
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
        cycles=1, run_mode=LIVE,
        safe_z=60.0,
        retract_z=30.5,
        search_z=15.5,
        part_z=2.0,
        pressure_setting="low",
        ft_sensor_num=7,
        stud_type="M6",
        substrate="Stainless Steel",
        speed=42,
    )
    assert "SAFE_Z = 60" in built.text
    assert "RETRACT_Z = 30.5" in built.text
    assert "SEARCH_Z = 15.5" in built.text
    assert "PART_Z = 2" in built.text
    assert "PRESS_LBF = 17" in built.text
    assert "FT_SENSOR_NUM = 7" in built.text
    assert "WELD_FT_SENSOR_NUM = FT_SENSOR_NUM" in built.text
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
        cycles=1, run_mode=LIVE,
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
        cycles=1, run_mode=LIVE,
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


def test_weldflex_lua_publishes_the_search_height_as_weld_lua_park_height():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}], cycles=1, run_mode=LIVE, part_z=63.5, safe_z=127, search_z=50.8
    )
    assert "PART_Z = 63.5" in built.text
    assert "SEARCH_Z = 50.8" in built.text
    assert "PARK_Z = PART_Z + SEARCH_Z" in built.text
    assert "Z_CLEARANCE = PARK_Z" in built.text
    # weld.lua reads Z_CLEARANCE and Z_RETRACT only; these were its old, now-dead inputs.
    assert "WELD_SAFE_Z" not in built.text
    assert "WELD_PART_Z" not in built.text


def test_weldflex_lua_publishes_retract_z_as_weld_lua_lift_height():
    """weld.lua lifts to Z_RETRACT after every stud and feeds there, so it must
    be the run's Retract Z on the same Part Z base, not the Search Height."""
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}], cycles=1, run_mode=LIVE,
        part_z=63.5, safe_z=127, retract_z=76.2, search_z=50.8,
    )
    assert "LIFT_Z = PART_Z + RETRACT_Z" in built.text
    assert "Z_RETRACT = LIFT_Z" in built.text
    approach = _stud_approach(built)
    assert approach["retract"] == pytest.approx(63.5 + 76.2)
    assert approach["clearance"] == pytest.approx(63.5 + 50.8)


def test_weldflex_lua_keeps_part_designer_x_y_order():
    built = build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=LIVE)

    assert "weldX = stud.x" in built.text
    assert "weldY = stud.y" in built.text
    assert "weldX = stud.y" not in built.text
    assert "weldY = stud.x" not in built.text


def test_weldflex_lua_travels_to_and_from_home_at_safe_height():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}, {"x": 30, "y": 40}],
        cycles=1, run_mode=LIVE,
        safe_z=60.0,
        retract_z=25.0,
        search_z=10.0,
        part_z=2.0,
    )
    code = strip_lua_comments(built.text)
    assert "HIGH_Z = PART_Z + SAFE_Z" in code
    # The traverse to a stud starts at Safe Z and only drops to Retract Z
    # once there is a previous stud to come from.
    assert "local travelZ = HIGH_Z" in code
    assert code.count("Lin(homewf, speed, -1, 0, 0)") == 2
    assert "PointsOffsetEnable(0, lastWeldX, lastWeldY, HIGH_Z, 0, 0, 0)" in code
    assert "WELD_RETRACT_Z" not in code


def _moves(code):
    """Every motion command in text order, with the (x, y, z) offset active for it."""
    moves, offset = [], None
    for line in code.splitlines():
        stmt = line.strip()
        if stmt.startswith("PointsOffsetEnable("):
            args = stmt[len("PointsOffsetEnable("):-1].split(",")
            offset = tuple(arg.strip() for arg in args[1:4])
        elif stmt.startswith("PointsOffsetDisable("):
            offset = None
        else:
            move = re.match(r"(?:Lin|PTP|MoveCart|MoveL|MoveJ)\((\w+)", stmt)
            if move:
                moves.append((move.group(1), offset))
    return moves


def test_a_run_never_moves_z_and_xy_together():
    """Owner, 2026-09-22: for safety a move is straight up, straight down, or
    level — never Z and X/Y at once. homewf is taught at Safe Z.
    Per stud: level to above it, straight down to the Search Height, weld.lua
    searches from there and lifts straight up to Retract Z. The first stud of a
    cycle is reached level at Safe Z out of home; later ones travel level at
    Retract Z from where weld.lua left the head (owner, 2026-10-01: the climb to
    Safe Z between every stud was wasted travel; 2026-10-02: the lift moved into
    weld.lua so the feed happens at Retract Z).
    """
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}, {"x": 30, "y": 40}],
        cycles=2, run_mode=DRY, safe_z=127.0, retract_z=76.2, search_z=50.8, part_z=2.54,
    )
    code = strip_lua_comments(built.text)

    assert _moves(code) == [
        ("homewf", None),                                    # start: home, at Safe Z
        ("zerozero", ("weldX", "weldY", "travelZ")),         # level to the next
        ("zerozero", ("weldX", "weldY", "PARK_Z")),          # down to Search Height
        ("zerozero", ("lastWeldX", "lastWeldY", "HIGH_Z")),  # end of cycle: up
        ("homewf", None),                                    # level into home
    ]
    assert "HIGH_Z = PART_Z + SAFE_Z" in code
    assert "LIFT_Z = PART_Z + RETRACT_Z" in code
    assert "PARK_Z = PART_Z + SEARCH_Z" in code
    # weld.lua parks and searches at the height the run descended to, and
    # lifts to the height the run then travels at.
    assert "Z_CLEARANCE = PARK_Z" in code
    assert "Z_RETRACT = LIFT_Z" in code

    # The level move runs at Safe Z out of home and at Retract Z after a stud,
    # where weld.lua's retract left the head.
    stud_branch = code.index("if lastWeldX ~= nil and lastWeldY ~= nil then")
    assert code.index("local travelZ = HIGH_Z") < stud_branch
    assert stud_branch < code.index("travelZ = LIFT_Z", stud_branch)
    assert code.index("lastWeldX = weldX") > code.index(
        "PointsOffsetEnable(0, weldX, weldY, PARK_Z, 0, 0, 0)"
    )


def _if_block(code, header):
    """The statements inside the one `if` block whose header line is `header`."""
    lines = code.splitlines()
    (start,) = [i for i, line in enumerate(lines) if line.strip() == header]
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].strip() == "end" and _indent(lines[i]) == _indent(lines[start]))
    return [line.strip() for line in lines[start + 1:end] if line.strip()]


def test_the_run_leaves_the_lift_off_a_stud_to_weld_lua():
    """Until 2026-10-02 the run lifted from the Search Height to Retract Z after
    weld.lua returned, so the feed inside weld.lua fired at the Search Height.
    weld.lua now lifts to Retract Z itself, before the feed, and the run only
    travels level from there. A Retract Z equal to the Search Height leaves
    nothing to lower, so the descent is skipped rather than run as a
    zero-length move."""
    code = strip_lua_comments(build_weldflex_lua(
        [{"x": 10, "y": 20}, {"x": 30, "y": 40}],
        cycles=1, run_mode=LIVE, safe_z=127.0, retract_z=25.4, search_z=25.4,
    ).text)

    assert "LIFT_Z ~= PARK_Z" not in code
    assert "PointsOffsetEnable(0, lastWeldX, lastWeldY, LIFT_Z" not in code
    assert _if_block(code, "if travelZ ~= PARK_Z then") == [
        "PointsOffsetEnable(0, weldX, weldY, PARK_Z, 0, 0, 0)",
        "Lin(zerozero, speed, -1, 0, 0)",
        "PointsOffsetDisable()",
    ]
    assert code.count("PointsOffsetEnable(0, weldX, weldY, PARK_Z") == 1


def test_retract_z_left_out_keeps_the_travel_between_studs_at_safe_z():
    """A caller that predates Retract Z gets the travel it always had."""
    built = build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=LIVE,
                               safe_z=127.0, search_z=25.4)
    assert "RETRACT_Z = 127" in _lines(built)
    assert "SEARCH_Z = 25.4" in _lines(built)


@pytest.mark.parametrize("retract_z, search_z", [(25.3, 25.4), (0, 10)])
def test_a_retract_z_below_the_search_height_is_refused(retract_z, search_z):
    """Lifting "up" to it from the Search Height would drive the head down
    toward the part before the move sideways."""
    with pytest.raises(ValueError, match="Retract Z"):
        check_travel_heights(retract_z, search_z)
    with pytest.raises(ValueError, match="Retract Z"):
        build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=LIVE,
                           safe_z=127.0, retract_z=retract_z, search_z=search_z)


def test_a_retract_z_at_or_above_the_search_height_is_accepted():
    check_travel_heights(25.4, 25.4)
    check_travel_heights(200, 25.4)  # no Safe Z given: only the Search Height bounds it


@pytest.mark.parametrize("safe_z, retract_z", [(76.1, 76.2), (10, 25.4)])
def test_a_safe_z_below_retract_z_is_refused(safe_z, retract_z):
    """The end of a cycle lifts from Retract Z to Safe Z. Below it, that "lift"
    would drive the head down toward the part before the level move home."""
    with pytest.raises(ValueError, match="Safe Z"):
        check_travel_heights(retract_z, 10.0, safe_z)
    with pytest.raises(ValueError, match="Safe Z"):
        build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=LIVE,
                           safe_z=safe_z, retract_z=retract_z, search_z=10.0)


def test_heights_in_order_with_equal_neighbours_are_accepted():
    check_travel_heights(25.4, 25.4, 25.4)
    check_travel_heights(76.2, 25.4, 127.0)
    build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=LIVE,
                       safe_z=76.2, retract_z=76.2, search_z=25.4)


def test_weldflex_lua_never_offsets_homewf_by_the_safe_height():
    """homewf is taught AT the safe height (owner, 2026-09-22). Offsetting it
    by HIGH_Z stacked the safe height on top of itself — the head rose 5 in
    off home before dropping back to the first stud, and rose again before
    settling into home at the end. Home legs are level moves straight
    to/from the stud's safe-plane pose.
    """
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}, {"x": 30, "y": 40}],
        cycles=2, run_mode=DRY, safe_z=127.0, part_z=2.54,
    )
    code = strip_lua_comments(built.text)
    assert "PointsOffsetEnable(0, 0, 0," not in code
    for i, line in enumerate(code.splitlines()):
        if "Lin(homewf" in line:
            # No offset may be active when homewf is the target.
            before = "\n".join(code.splitlines()[:i])
            assert before.rfind("PointsOffsetDisable()") >= before.rfind(
                "PointsOffsetEnable("
            ), line


def test_weldflex_lua_uses_global_point_offsets_without_inline_lin_offsets():
    built = build_weldflex_lua([{"x": 10, "y": 20}, {"x": 30, "y": 40}], cycles=1, run_mode=LIVE)

    assert built.text.count("Lin(homewf, speed, -1, 0, 0)") == 2
    # The level traverse rides DSC's travelSpeed (e12e699); the vertical moves
    # (descent to Search Height, lift at cycle end) use plain speed. The lift
    # off each stud is weld.lua's, at its RETRACT_SPEED.
    assert built.text.count("Lin(zerozero, travelSpeed, -1, 0, 0)") == 1
    assert built.text.count("Lin(zerozero, speed, -1, 0, 0)") == 2
    assert "Lin(zerozero, travelSpeed, -1, 0, 1)" not in built.text


def test_weldflex_lua_returns_home_every_cycle_before_the_gate():
    """The operator needs the head clear of the part to swap it, on every
    cycle boundary — not just once after the whole run finishes. The Lua
    loop body is only emitted once in the text (the runtime `for` loop
    repeats it), so the home-return block must sit *inside* the loop, before
    the boundary dwell/gate, rather than appearing once after it.
    """
    built = build_weldflex_lua([{"x": 10, "y": 20}], cycles=3, run_mode=LIVE)
    lines = built.text.splitlines()
    # Initial home, then the level return from the last stud every cycle.
    assert built.text.count("Lin(homewf, speed, -1, 0, 0)") == 2
    home_idxs = [i for i, l in enumerate(lines, 1) if "Lin(homewf" in l]
    # The safe-height lift and return home precede the boundary gate.
    assert home_idxs[-1] < built.cycle_marker_line < built.gate_line
    # Nothing homes after the loop any more — it is done every cycle instead.
    assert not any(i > built.gate_line for i in home_idxs)


def _line_of(lines, statement):
    """1-based line number of the one line that is exactly `statement`."""
    (found,) = [i for i, line in enumerate(lines, 1) if line.strip() == statement]
    return found


def _indent(line):
    return len(line) - len(line.lstrip())


@pytest.mark.parametrize("start", [1, "1", " 1 ", None, "", 0, "0"])
def test_a_run_starts_at_the_first_stud_unless_told_otherwise(start):
    """The run modal's Starting Stud defaults to 1. A cleared field and 0 are
    not stud numbers and can only mean the same thing, so all of them have to
    build the same whole-part program."""
    studs = [{"x": i, "y": i} for i in range(4)]
    built = build_weldflex_lua(studs, cycles=1, run_mode=LIVE, start_stud=start)
    assert "START_STUD = 1" in _lines(built)
    assert built.start_stud == 1
    assert built.text == build_weldflex_lua(studs, cycles=1, run_mode=LIVE).text


def test_a_resumed_run_skips_the_earlier_studs_on_the_first_cycle_only():
    """A fault cancels the program, and the only way to finish the part was to
    delete the studs already welded from the recipe (owner, 2026-09-30). The
    starting stud skips them instead — for the part on the bed, not for the
    parts that follow it, so it has to reset inside the cycle loop."""
    built = build_weldflex_lua(
        [{"x": i, "y": i} for i in range(6)], cycles=3, run_mode=LIVE, start_stud=4
    )
    lines = _lines(built)

    assert built.start_stud == 4
    # Every stud is still in the program: later cycles weld all of them.
    assert built.stud_count == 6
    assert sum(line.strip().startswith("{x=") for line in lines) == 6
    assert "--{{" not in built.text

    declared = _line_of(lines, "START_STUD = 4")
    seeded = _line_of(lines, "local firstStud = START_STUD")
    stud_loop = _line_of(lines, "for studIndex = firstStud, #studs do")
    reset = _line_of(lines, "firstStud = 1")
    assert declared < seeded < built.loop_start_line < stud_loop < reset < built.cycle_marker_line
    assert lines[stud_loop].strip() == "local stud = studs[studIndex]"
    # The reset belongs to the cycle loop, after the stud loop has closed — at the
    # stud loop's own depth, not inside its body, where it would undo the skip
    # after the first stud.
    assert _indent(lines[reset - 1]) == _indent(lines[stud_loop - 1])
    assert lines[reset - 2].strip() == "end"


def test_the_last_stud_is_a_valid_starting_stud():
    built = build_weldflex_lua([{"x": i, "y": i} for i in range(4)], cycles=1, run_mode=LIVE,
                               start_stud="4")
    assert "START_STUD = 4" in _lines(built)


@pytest.mark.parametrize("start, error", [
    (5, r"past the part's last stud \(4\)"),
    (-1, "negative"),
    ("2.5", "whole number"),
    (2.0, "whole number"),
    (True, "whole number"),
    ("four", "whole number"),
])
def test_the_builder_refuses_a_starting_stud_that_is_not_on_the_part(start, error):
    """Refused, never clamped: a resume that quietly started at some other stud
    would weld over studs that are already on the part."""
    with pytest.raises(ValueError, match=error):
        build_weldflex_lua([{"x": i, "y": i} for i in range(4)], cycles=1, run_mode=LIVE,
                           start_stud=start)


def test_dropping_the_start_stud_marker_fails_loudly(tmp_path):
    """Without it the template's literal 1 would stand, and a resume would start
    over at the first stud with nothing saying so."""
    stripped = [line for line in TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
                if "--{{START_STUD}}" not in line]
    bad = tmp_path / TEMPLATE_PATH.name
    bad.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing required marker.*START_STUD"):
        build_weldflex_lua([{"x": 1, "y": 2}], cycles=1, run_mode=LIVE, template_path=bad)


def test_a_resumed_cycle_does_not_wait_out_a_reload_that_never_happened(monkeypatch):
    """DSC gives every stud after the first a reload dwell, timed from the feed
    that follows the previous weld. A cycle resumed at one of those studs reaches
    it from home, so both of its DSC settings are gated on a previous stud."""
    monkeypatch.setenv("WELDFLEX_DSC_CALIBRATED", "1")
    built = build_weldflex_lua(
        [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}],
        cycles=1, run_mode=LIVE, dsc_enabled=True, stud_reload_ms=600, start_stud=2,
    )
    code = strip_lua_comments(built.text)
    lines = code.splitlines()
    branch = next(i for i, line in enumerate(lines)
                  if line.strip() == "if lastWeldX ~= nil and lastWeldY ~= nil then")
    block_end = next(i for i in range(branch + 1, len(lines))
                     if lines[i].strip() == "end" and _indent(lines[i]) == _indent(lines[branch]))
    assert "travelSpeed = stud.s2sSpeed" in "\n".join(lines[branch:block_end])
    assert code.count("travelSpeed = stud.s2sSpeed") == 1
    assert "if lastWeldX ~= nil and stud.s2sWaitMs ~= nil and stud.s2sWaitMs > 0 then" in code
    assert code.count("WaitMs(stud.s2sWaitMs)") == 1




def test_weldflex_lua_substitutes_numeric_pressure_setting():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}],
        cycles=1, run_mode=LIVE,
        pressure_setting="22.5",
    )
    assert "PRESS_LBF = 22.5" in built.text


def test_weldflex_lua_supports_live_and_dry_run_arming():
    live = build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=LIVE)
    dry = build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=DRY, speed=42)
    assert "WELD_ARMED = 1" in live.text
    assert "WELD_ARMED = 0" in dry.text
    assert "speed = 42" in dry.text


def test_dry_run_uses_the_same_safe_plane_force_motion_path():
    built = build_weldflex_lua(
        [{"x": 10, "y": 20}],
        cycles=1,
        run_mode=DRY,
        safe_z=50,
        search_z=25,
        part_z=50,
    )
    text = built.text

    # Dry only disarms the arc; motion remains the production safe-plane path:
    # home (at safe height) -> stud XY at safe height -> down to search -> F/T descent.
    assert "WELD_ARMED = 0" in text
    assert "HIGH_Z = PART_Z + SAFE_Z" in text
    home = text.index("Lin(homewf, speed, -1, 0, 0)")
    stud_safe = text.index("PointsOffsetEnable(0, weldX, weldY, travelZ, 0, 0, 0)")
    search = text.index("PointsOffsetEnable(0, weldX, weldY, PARK_Z, 0, 0, 0)")
    assert home < stud_safe < search < text.index('NewDofile("/fruser/weld.lua"')
    assert text.rindex("PointsOffsetEnable(0, lastWeldX, lastWeldY, HIGH_Z, 0, 0, 0)") > stud_safe


def test_run_mode_has_no_default_arm_mode():
    """Live or Dry is picked for every run. A default of "live" is how the old
    faceplate page loaded live jobs nobody had chosen."""
    with pytest.raises(ValueError, match="arm_mode"):
        RunMode("armed")
    with pytest.raises(TypeError):
        RunMode()
    with pytest.raises(TypeError):
        build_weldflex_lua([{"x": 10, "y": 20}], cycles=1)
    with pytest.raises(TypeError):
        build_single_shot_lua(10, 20, cycles=1)


def test_run_mode_rejects_a_di_check_that_is_not_a_bool():
    """Form values arrive as strings, and "0" is truthy — accepting one would
    silently publish the opposite of what was sent."""
    with pytest.raises(ValueError, match="di_check"):
        RunMode("dry", di_check="0")


@pytest.mark.parametrize("arm_mode", ARM_MODES)
@pytest.mark.parametrize("di_check", [True, False])
def test_every_run_mode_builds_and_is_published_verbatim(arm_mode, di_check):
    """DI check off builds live as well as dry: the owner removed the Liberty
    dry-only guard on 2026-09-14."""
    mode = RunMode(arm_mode, di_check=di_check)
    for built in (
        build_weldflex_lua([{"x": 10, "y": 20}], cycles=1, run_mode=mode),
        build_single_shot_lua(10, 20, cycles=1, run_mode=mode),
    ):
        assert f"WELD_ARMED = {1 if arm_mode == 'live' else 0}" in built.text
        assert f"WELD_DI_CHECK = {1 if di_check else 0}" in built.text


@pytest.mark.parametrize(
    "build",
    [
        lambda: build_weldflex_lua([{"x": 1, "y": 2}] * 3, cycles=2, run_mode=LIVE),
        lambda: build_single_shot_lua(1, 2, cycles=1, run_mode=LIVE),
    ],
    ids=["weldflex", "single_shot"],
)
def test_run_mode_is_published_once_above_the_loop_and_never_derived(build):
    """The callers only publish what Python resolved. Deriving the switches in
    Lua is how the two templates came to disagree about what a dry run skips."""
    built = build()
    lines = _lines(built)
    for name in ("WELD_ARMED", "WELD_DI_CHECK"):
        assigned = [i for i, line in enumerate(lines, 1) if re.match(rf"^\s*{name}\s*=", line)]
        assert len(assigned) == 1, f"{name} is assigned {len(assigned)} times"
        assert assigned[0] < built.loop_start_line
    for retired in ("ARM_MODE", "WELDER_PROFILE", "LIBERTY", "WELD_SKIP_INTERLOCKS",
                    "WELD_SKIP_FEED", "WELD_TRIGGER_DO", "WELD_TRIGGER_PULSE_MS"):
        assert retired not in built.text


@pytest.mark.parametrize(
    "template,build",
    [
        (TEMPLATE_PATH,
         lambda path: build_weldflex_lua([], cycles=1, run_mode=LIVE, template_path=path)),
        (SINGLE_SHOT_TEMPLATE_PATH,
         lambda path: build_single_shot_lua(0, 0, cycles=1, run_mode=LIVE, template_path=path)),
    ],
    ids=["weldflex", "single_shot"],
)
def test_dropping_the_run_mode_marker_fails_loudly(tmp_path, template, build):
    """Without it weld.lua would quietly fall back to its own defaults."""
    stripped = [line for line in template.read_text(encoding="utf-8").splitlines()
                if "--{{RUN_MODE}}" not in line]
    bad = tmp_path / template.name
    bad.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing required marker.*RUN_MODE"):
        build(bad)


def test_single_shot_lua_uses_the_goto_coordinate_and_run_mode():
    built = build_single_shot_lua(150, 381, cycles=1, run_mode=LIVE, part_z=5, safe_z=10)

    assert "weldX = targetX" in built.text
    assert "weldY = targetY" in built.text
    assert "WELD_ARMED = 1" in built.text
    assert "Z_CLEARANCE = PART_Z + SAFE_Z" in built.text
    assert "PointsOffsetEnable(0, targetX, targetY, APPROACH_Z, 0, 0, 0)" in built.text
    assert "PointsOffsetEnable(1," not in built.text
    assert "PTP(zerozero, speed, -1, 0)" in built.text
    assert "Lin(zerozero" not in built.text


def test_marker_line_really_is_the_boundary_dwell():
    """The assertion that catches a template edit shifting the marker."""
    built = build_weldflex_lua([{"x": 1, "y": 2}] * 5, cycles=3, run_mode=LIVE)
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
    built = build_weldflex_lua([{"x": 1, "y": 2}] * 5, cycles=3, run_mode=LIVE)
    assert built.program_line_count == len(_lines(built))
    weld_lua_lines = len(WELD_PATH.read_text(encoding="utf-8").splitlines())
    assert built.program_line_count < weld_lua_lines


@pytest.mark.parametrize("n_studs,cycles", [(0, 1), (1, 1), (5, 20), (40, 999)])
def test_marker_lines_track_stud_count(n_studs, cycles):
    built = build_weldflex_lua([{"x": i, "y": i} for i in range(n_studs)], cycles=cycles, run_mode=LIVE)
    lines = _lines(built)
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert f"cycleCount = {cycles}" in built.text


def test_pause_mode_gets_a_longer_boundary_dwell():
    """The cycle banks at the marker — the start of the dwell — and `Pause()` is
    the line after it, so the dwell is the window job_manager._gate waits out
    before deciding the program did not hold and sending a ProgramPause itself."""
    paused = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, run_mode=LIVE, gate_mode="pause")
    for mode in ("none", "di"):
        other = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, run_mode=LIVE, gate_mode=mode)
        assert paused.boundary_ms > other.boundary_ms

    assert f"BOUNDARY_MS = {paused.boundary_ms}" in paused.text
    # The declaration has to precede the loop that uses it.
    lines = _lines(paused)
    decl = next(i for i, l in enumerate(lines, 1) if l.startswith("BOUNDARY_MS ="))
    assert decl < paused.loop_start_line


def test_boundary_dwell_can_be_overridden_explicitly():
    built = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, run_mode=LIVE, gate_mode="pause", boundary_ms=8000)
    assert built.boundary_ms == 8000
    assert "BOUNDARY_MS = 8000" in built.text


def test_di_gate_emits_waitdi_that_aborts_on_timeout():
    built = build_weldflex_lua(
        [{"x": 1, "y": 1}], cycles=2, run_mode=LIVE, gate_mode="di", gate_di=6, gate_timeout_ms=45000
    )
    gate = _lines(built)[built.gate_line - 1:built.gate_line + 1]
    joined = "\n".join(gate)
    assert f"WaitDI(6, 1, 45000, {GATE_DI_OPT_ABORT})" in joined
    # opt=0 stops the program on timeout. Falling through would weld into a part
    # the operator never swapped.
    assert GATE_DI_OPT_ABORT == 0


@pytest.mark.parametrize(
    "builder", [
        lambda mode: build_weldflex_lua([{"x": 1, "y": 1}], cycles=4, run_mode=LIVE, gate_mode=mode),
        lambda mode: build_single_shot_lua(1, 1, cycles=4, run_mode=LIVE, gate_mode=mode),
    ],
    ids=["weldflex", "single_shot"],
)
def test_pause_gate_holds_in_the_program_and_skips_the_last_cycle(builder):
    """2026-08-06. The gate used to be nothing but a comment — the manager saw
    the marker and *then* sent a ProgramPause, which on hardware never landed:
    the faceplate run welded, retracted, opened DO1 and drove straight back down
    into the next cycle. The hold has to be an instruction the program executes.

    The last cycle is deliberately exempt. Nothing releases a pause the manager
    never gates on (`done < target`). WeldFlex.lua homes every cycle, including
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
    built = build_weldflex_lua([{"x": 1, "y": 1}], cycles=2, run_mode=LIVE, gate_mode=mode)
    assert "WaitDI" not in built.text
    assert built.gate_mode == mode


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        build_weldflex_lua([], cycles=1, run_mode=LIVE, gate_mode="nope")
    with pytest.raises(ValueError):
        build_weldflex_lua([], cycles=0, run_mode=LIVE)


def test_missing_marker_is_a_hard_error(tmp_path):
    bad = tmp_path / "WeldFlex.lua"
    bad.write_text("studs = {\n--{{STUDS}}\n}\n--{{CYCLE_COUNT}}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing required marker"):
        build_weldflex_lua([], cycles=1, run_mode=LIVE, template_path=bad)


def test_dropping_only_the_boundary_marker_still_fails_loudly(tmp_path):
    """Otherwise the dwell silently reverts to the template's literal and the gate
    window quietly shrinks — the kind of thing that is invisible until hardware."""
    source = TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
    stripped = [l for l in source if "--{{BOUNDARY_MS}}" not in l]
    bad = tmp_path / "WeldFlex.lua"
    bad.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing required marker.*BOUNDARY_MS"):
        build_weldflex_lua([], cycles=1, run_mode=LIVE, template_path=bad)


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
        cycles=1, run_mode=LIVE,
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
    assert PRESS_LBF_MAX * 4.448222 < 100.0, \
        "FT_LinInsertion accepts at most a 100 N force threshold"


def test_the_press_guard_declines_below_its_own_threshold():
    """weld.lua only widens collision detection for a press that needs it."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    assert re.search(r"^local PRESS_GUARD_MIN_N\s*=\s*([\d.]+)", weld, re.M), \
        "weld.lua no longer gates the collision guard on the press target"
    assert re.search(r"^local pressNeedsGuard\s*=\s*PRESS_TARGET_N\s*>=\s*PRESS_GUARD_MIN_N",
                     weld, re.M), "the guard is no longer gated on the press target"


def test_force_control_uses_negative_fz_for_compression():
    """The regulator needs a signed target and must finish enabling before insertion."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    control = re.search(r"local function ftControlPress\(flag\)(.*?)\nend", weld, re.S)
    assert control, "weld.lua no longer defines the FT_Control helper"
    assert "0.0, 0.0, -PRESS_TARGET_N, 0.0, 0.0, 0.0" in control.group(1)
    assert control.group(1).strip().endswith("0)"), \
        "FT_Control must use blocking mode before FT_LinInsertion starts"


def test_force_control_uses_the_conservative_proportional_gain():
    weld = WELD_PATH.read_text(encoding="utf-8")
    assert "local FTC_GAIN_P = 0.0001" in weld
    assert "FTC_GAIN_P = 0.0005" not in weld


def test_surface_search_uses_the_commissioned_gentle_speed():
    weld = WELD_PATH.read_text(encoding="utf-8")
    match = re.search(r"^local SEARCH_SPEED_MMS\s*=\s*([\d.]+)", weld, re.M)
    assert match, "weld.lua no longer declares SEARCH_SPEED_MMS"
    assert float(match.group(1)) == 7.5


def test_force_press_uses_the_commissioned_slow_speed():
    """A 10 mm/s search with the press at 1.0, then 0.5 mm/s, left the press stuck
    short of force live on 2026-09-14. At 0.25 a 10 lbf press overshot to 14-15 lbf
    once the gun bottomed out near 9 lbf (2026-09-28), so the press went to 0.10.
    0.15 (with a 7.5 mm/s search) is a trial from 2026-10-02."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    match = re.search(r"^local PRESS_SPEED_MMS\s*=\s*([\d.]+)", weld, re.M)
    assert match, "weld.lua no longer declares PRESS_SPEED_MMS"
    assert float(match.group(1)) == 0.15


def test_weld_lua_falls_back_to_the_same_speeds_and_bounds_as_weld_tuning():
    """Since 2026-10-02 the Admin page's Weld Tuning sets both speeds, and
    weld.lua's own copies are only the fallback. Nothing ties the two files
    together across the language boundary, so pin them here."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    for name, value in (
        ("SEARCH_SPEED_MMS", DEFAULT_SEARCH_SPEED_MMS),
        ("PRESS_SPEED_MMS", DEFAULT_PRESS_SPEED_MMS),
        ("SEARCH_SPEED_MIN_MMS", SEARCH_SPEED_MIN_MMS),
        ("SEARCH_SPEED_MAX_MMS", SEARCH_SPEED_MAX_MMS),
        ("PRESS_SPEED_MIN_MMS", PRESS_SPEED_MIN_MMS),
        ("PRESS_SPEED_MAX_MMS", PRESS_SPEED_MAX_MMS),
    ):
        match = re.search(rf"^local {name}\s*=\s*([\d.]+)", weld, re.M)
        assert match, f"weld.lua no longer declares {name}"
        assert float(match.group(1)) == value, name


@pytest.mark.parametrize("published,local,lo,hi,call_site", [
    ("WELD_SEARCH_SPEED_MMS", "SEARCH_SPEED_MMS", "SEARCH_SPEED_MIN_MMS",
     "SEARCH_SPEED_MAX_MMS", "SEARCH_SPEED_MMS, FIND_ACC, SEARCH_MAX_MM"),
    ("WELD_PRESS_SPEED_MMS", "PRESS_SPEED_MMS", "PRESS_SPEED_MIN_MMS",
     "PRESS_SPEED_MAX_MMS", "PRESS_SPEED_MMS, 0.0, PRESS_MAX_MM"),
], ids=["search", "press"])
def test_weld_lua_takes_a_published_speed_only_within_its_bounds(published, local, lo, hi, call_site):
    weld = WELD_PATH.read_text(encoding="utf-8")
    guard = (f'if type({published}) == "number"\n'
             f"   and {published} >= {lo}\n"
             f"   and {published} <= {hi} then\n"
             f"    {local} = {published}\n"
             "end")
    assert guard in weld
    assert weld.index(guard) < weld.index(call_site)


_TUNED_BUILDS = pytest.mark.parametrize(
    "build",
    [
        lambda **kw: build_weldflex_lua([{"x": 1, "y": 2}], cycles=1, run_mode=LIVE, **kw),
        lambda **kw: build_single_shot_lua(1, 2, cycles=1, run_mode=LIVE, **kw),
    ],
    ids=["weldflex", "single_shot"],
)


@_TUNED_BUILDS
def test_the_weld_tuning_speeds_are_published_to_weld_lua(build):
    built = build(search_speed_mms=9, press_speed_mms=0.3)
    lines = _lines(built)
    assert "SEARCH_SPEED = 9" in lines
    assert "PRESS_SPEED = 0.3" in lines
    dofile = next(i for i, line in enumerate(lines, 1)
                  if "NewDofile(" in line and not line.strip().startswith("--"))
    for row in ("WELD_SEARCH_SPEED_MMS = SEARCH_SPEED", "WELD_PRESS_SPEED_MMS = PRESS_SPEED"):
        at = next(i for i, line in enumerate(lines, 1) if line.strip() == row)
        assert built.loop_start_line < at < dofile, row


@_TUNED_BUILDS
def test_left_out_the_weld_tuning_speeds_are_the_defaults(build):
    lines = _lines(build())
    assert f"SEARCH_SPEED = {format_number(DEFAULT_SEARCH_SPEED_MMS)}" in lines
    assert f"PRESS_SPEED = {format_number(DEFAULT_PRESS_SPEED_MMS)}" in lines


@_TUNED_BUILDS
@pytest.mark.parametrize("speeds,error", [
    ({"search_speed_mms": 10.5}, "Search speed"),
    ({"search_speed_mms": 0.4}, "Search speed"),
    ({"press_speed_mms": 1.01}, "Press speed"),
    ({"press_speed_mms": 0}, "Press speed"),
])
def test_the_builders_refuse_a_speed_out_of_bounds(build, speeds, error):
    """Refused here, with a message, rather than uploaded for weld.lua to
    quietly swap for its fallback."""
    with pytest.raises(ValueError, match=error):
        build(**speeds)


@pytest.mark.parametrize("marker", ["--{{SEARCH_SPEED}}", "--{{PRESS_SPEED}}"])
@pytest.mark.parametrize(
    "template,build",
    [
        (TEMPLATE_PATH,
         lambda path: build_weldflex_lua([], cycles=1, run_mode=LIVE, template_path=path)),
        (SINGLE_SHOT_TEMPLATE_PATH,
         lambda path: build_single_shot_lua(0, 0, cycles=1, run_mode=LIVE, template_path=path)),
    ],
    ids=["weldflex", "single_shot"],
)
def test_dropping_a_weld_tuning_marker_fails_loudly(tmp_path, template, build, marker):
    """Without it the Admin page's speed would never reach weld.lua."""
    stripped = [line for line in template.read_text(encoding="utf-8").splitlines()
                if marker not in line]
    bad = tmp_path / template.name
    bad.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing required marker.*" + re.escape(marker)):
        build(bad)


def test_retract_uses_the_trial_pull_off_speed():
    """At 10 a welded stud sometimes stayed in the chuck, lifted the plate and
    tripped "Force sensor range threshold reached", while a manual jog straight up
    pulled the same stud off clean (2026-09-30). 25 is a trial of a quicker
    pull-off. Resistance dropped on 2026-10-01, but zerozero was re-taught at the
    same time, so don't read that as proof the speed did it. It still tripped at
    25 later that day, so 5 is the next trial: slower than both speeds that
    tripped."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    match = re.search(r"^local RETRACT_SPEED\s*=\s*([\d.]+)", weld, re.M)
    assert match, "weld.lua no longer declares RETRACT_SPEED"
    assert float(match.group(1)) == 5.0


def test_retract_lifts_straight_up_from_where_the_head_is():
    """The retract used to Lin back to the park pose, retracing the tool-Z descent,
    so a head out of square with the bed pulled the chuck off at a tilt. About
    3 lbf sideways on the chuck trips the sensor (owner, 2026-10-01). It now lifts
    straight up the workpiece Z from the head's measured X/Y, as the Points page
    does, using the drift from a reading taken at the park pose before anything
    moves."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    depart = code.split("local function departFromStud()", 1)[1].split("local function ", 1)[0]
    assert "now[1] - parkPose[1]" in depart and "now[2] - parkPose[2]" in depart
    assert "liftX = weldX + dx" in depart and "liftY = weldY + dy" in depart
    assert "PointsOffsetEnable(0, liftX, liftY, liftHeight(), 0, 0, 0)" in depart
    assert "Lin(zerozero, RETRACT_SPEED, -1, 0, 0)" in depart
    # Past the cap it falls back to the park pose, not to an unchecked reading.
    assert "<= LIFT_MAX_DRIFT_MM * LIFT_MAX_DRIFT_MM" in depart

    run = code.split("local function weldOneStud()", 1)[1]
    assert run.index("parkPose = readPose()") < run.index("waitForWeldReady()")


def test_the_retract_lifts_to_the_retract_height_before_the_feed():
    """Owner, 2026-10-02: the retract only went back up to the Search Height,
    and the next stud fed there, just above the stud that was just welded.
    The caller lifted to Retract Z only afterwards. The lift now targets the
    caller's Z_RETRACT, never lower than the park height it searched from, and
    the feed still comes after it."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))

    height = code.split("local function liftHeight()", 1)[1].split("local function ", 1)[0]
    assert "if Z_RETRACT > Z_CLEARANCE then return Z_RETRACT end" in height
    assert "return Z_CLEARANCE" in height
    assert "math." not in height, "the controller's Lua library is not assumed"
    assert code.index("local function liftHeight()") < code.index("local function departFromStud()")

    depart = code.split("local function departFromStud()", 1)[1].split("local function ", 1)[0]
    assert "Z_CLEARANCE" not in depart, "the lift must not return to the park height"

    contract = code.split("local function requireContract()", 1)[1].split("local function ", 1)[0]
    assert 'type(Z_RETRACT) ~= "number"' in contract

    run = code.split("local function weldOneStud()", 1)[1]
    assert run.index("retract()") < run.index("feedNextStud()")


def test_the_lift_turns_the_ft_collision_guard_off_first():
    """Owner's call, 2026-10-02: turn FT_Guard off before every lift as a test
    against the retract trip. weld.lua never turns the guard on, so this only
    matters if something else left it on. It must not depend on USE_FT_GUARD
    (which is 0, so ftGuardPress(0) never calls FT_Guard), and it must use the
    sensor number read from the controller."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    guard_off = code.split("local function ftGuardOff()", 1)[1].split("local function ", 1)[0]
    assert "USE_FT_GUARD" not in guard_off
    assert "FT_Guard(0, FTC_SENSOR_NUM," in guard_off

    depart = code.split("local function departFromStud()", 1)[1].split("local function ", 1)[0]
    assert depart.index("ftGuardOff()") < depart.index("Lin(zerozero, RETRACT_SPEED"), \
        "the guard must be off before the lift starts"
    # Defined above departFromStud(), or the call would look up a nil global.
    assert code.index("local function ftGuardOff()") < code.index("local function departFromStud()")


def test_the_travel_guard_is_on_between_studs_and_off_for_the_weld():
    """Owner's call, 2026-10-02: turn the F/T guard back on once a stud is
    done, so travel to the next stud stops on a hit. It must be off again before
    the search and press, which load the sensor far past the travel window, and
    it is armed only after the feed and only on a clean finish: a fault leaves it
    off for the recovery."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    travel = code.split("local function ftGuardTravel()", 1)[1].split("local function ", 1)[0]
    assert "FT_Guard(1, FTC_SENSOR_NUM," in travel
    assert "TRAVEL_GUARD_N, TRAVEL_GUARD_N, TRAVEL_GUARD_N, 0.0, 0.0, 0.0" in travel
    assert "USE_TRAVEL_GUARD" in travel

    run = code.split("local function weldOneStud()", 1)[1]
    assert run.index("ftGuardOff()") < run.index("searchForStud()"), \
        "the press would trip the travel guard"
    assert run.index("feedNextStud()") < run.index("ftGuardTravel()"), \
        "a stud blown into the chuck can spike the reading"
    assert run.count("ftGuardTravel()") == 1

    for name in ("local function fault(msg, site)", "local function departFromStud()",
                 "local function retract()"):
        body = code.split(name, 1)[1].split("local function ", 1)[0]
        assert "ftGuardTravel()" not in body, f"{name} must not arm the travel guard"


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


def test_di_check_off_skips_only_the_input_checks_live_or_dry():
    """WELD_DI_CHECK = 0 skips the DI0 wait and both DI1 checks, and nothing
    else: the search still runs, and an armed run still fires. The guard that
    refused a live arc without the checks went on 2026-09-14 by owner decision,
    together with the Liberty profile that needed it."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))

    def function_body(name, next_name):
        return code.split(f"local function {name}()", 1)[1].split(
            f"local function {next_name}()", 1
        )[0]

    readiness = function_body("waitForWeldReady", "requireContract")
    search = function_body("searchForStud", "pressToForce")
    fire = function_body("fireWeld", "holdAfterWeld")

    assert "return WELD_DI_CHECK ~= 0" in code
    assert readiness.index("if not diCheckEnabled() then") < readiness.index("readDI(DI_WELD_READY)")
    assert (search.index("FT_FindSurface")
            < search.index("if not diCheckEnabled() then")
            < search.index("readDI(DI_STUD_ON_WORK)"))
    assert (fire.index("if WELD_ARMED ~= 1 then")
            < fire.index("if diCheckEnabled() then")
            < fire.index("readDI(DI_STUD_ON_WORK)")
            < fire.index("writeDO(DO_WELD, 1)"))
    # Only the two dropped-input faults remain; nothing refuses a DI-off arc.
    assert fire.count("fault(") == 2
    for retired in ("WELD_SKIP_INTERLOCKS", "WELD_LIBERTY_COMMISSIONING", "interlocksRequired"):
        assert retired not in code


def test_weld_trigger_is_a_fixed_machine_output():
    """DO0 for 250 ms. The configurable trigger existed only for the Liberty
    endurance page, removed 2026-09-14."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    do_weld = re.search(r"^local DO_WELD\s*=\s*(\d+)", weld, re.M)
    pulse = re.search(r"^local WELD_PULSE_MS\s*=\s*(\d+)", weld, re.M)
    assert do_weld and do_weld.group(1) == "0"
    assert pulse and pulse.group(1) == "250"
    assert "WELD_TRIGGER_DO" not in weld
    assert "WELD_TRIGGER_PULSE_MS" not in weld


def test_weld_lua_always_feeds_after_a_completed_stud():
    """Single Shot feeds like a production stud (owner decision 2026-09-14), so
    nothing suppresses the feeder any more."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    master = code.split("local function weldOneStud()", 1)[1]
    assert master.index("retract()") < master.index("feedNextStud()") < master.index("PH_DONE")
    assert "WELD_SKIP_FEED" not in code


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
    """/ui/single-shot/feed writes the stud-feeder output from the host, so app.py
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


def test_linear_insertion_uses_the_standard_force_error_bounds():
    """Insertion must reject the same vendor error range as surface search."""
    weld = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    press = weld.split("local function pressToForce()", 1)[1].split(
        "local function fireWeld()", 1
    )[0]
    insertion = press.split("ret = ftCall(FT_LinInsertion", 1)[1].split(
        "local zNow", 1
    )[0]
    assert "if ftRefused(ret) then" in insertion


def test_linear_insertion_finishes_at_the_requested_force_tolerance_bound():
    """An in-tolerance force must not leave the press parked in insertion."""
    weld = WELD_PATH.read_text(encoding="utf-8")
    assert "local PRESS_TOLERANCE_LBF = 0.5" in weld
    assert "PRESS_TARGET_LBF - PRESS_TOLERANCE_LBF" in weld
    assert "PRESS_INSERT_THRESHOLD_LBF * N_PER_LBF" in weld

    press = strip_lua_comments(weld).split("local function pressToForce()", 1)[1].split(
        "local function fireWeld()", 1
    )[0]
    assert "FT_LinInsertion, FIND_RCS, PRESS_INSERT_THRESHOLD_N" in press
    control = strip_lua_comments(weld).split("local function ftControlPress(flag)", 1)[1].split(
        "local function ftGuardPress(flag)", 1
    )[0]
    assert "-PRESS_TARGET_N" in control


def test_the_press_hold_never_re_runs_the_insertion():
    """FT_LinInsertion stops on the first reading past its threshold, and seating
    the stud can spike past it for an instant and then sag (live 2026-09-14).
    Re-running the insertion through the hold to push that force back was tried
    the same day, and the dry run faulted with "Cartesian space command speed
    exceeded limit". Lua cannot read force, so a re-run cannot know whether it
    starts past its threshold."""
    weld = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    assert weld.count("ftCall(FT_LinInsertion") == 1, \
        "a second FT_LinInsertion faulted live; hold the force some other way"
    assert re.search(r"^local PRESS_HOLD_MS\s*=\s*[1-9]", weld, re.M)

    press = weld.split("local function pressToForce()", 1)[1].split(
        "local function fireWeld()", 1
    )[0]
    assert ("pub(SV_PHASE, PH_PRESS_HOLD) WaitMs(holdMs) "
            "pub(SV_PHASE, PH_PRESS_HELD)") in " ".join(press.split())


def test_the_hold_travel_reading_comes_after_the_hold_not_during_it():
    """s_var_9 (SV_PRESS_HOLD_TRAVEL) exists to show whether FT_Control kept
    driving in after FT_LinInsertion stopped short on a spike (see
    test_the_press_hold_never_re_runs_the_insertion) — a Z re-read, never a
    second force move. It must sample strictly after PH_PRESS_HELD publishes,
    so the pinned PH_PRESS_HOLD -> WaitMs -> PH_PRESS_HELD sequence above stays
    a pure passive wait with nothing timing-sensitive added to it.
    """
    weld = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    assert re.search(r"^local SV_PRESS_HOLD_TRAVEL\s*=\s*\d+", weld, re.M), \
        "weld.lua no longer declares SV_PRESS_HOLD_TRAVEL"

    press = weld.split("local function pressToForce()", 1)[1].split(
        "local function fireWeld()", 1
    )[0]
    held_at = press.index("pub(SV_PHASE, PH_PRESS_HELD)")
    hold_travel_at = press.index("pub(SV_PRESS_HOLD_TRAVEL")
    assert hold_travel_at > held_at, \
        "SV_PRESS_HOLD_TRAVEL must publish after PH_PRESS_HELD, not during the hold"

    # Zeroed at the top of every stud like the other press telemetry, so a
    # fault before the press phase can't leave a previous stud's number behind.
    reset = weld.split("local function weldOneStud()", 1)[1]
    assert "pub(SV_PRESS_HOLD_TRAVEL, 0)" in reset


def test_the_weld_jolt_reading_does_not_touch_the_arc_pulse_timing():
    """s_var_10 (SV_WELD_JOLT_TRAVEL) exists to show how far the tool moves
    from just before the arc to the end of the post-weld hold, while
    FT_Control is still regulating force through both (fireWeld's own comment
    explains why that matters: the tip flashes off in milliseconds, and the
    F/T sensor's RS-485 link sits next to that current pulse, so a real
    force-loss step and an EMI-glitched sample look identical to Lua). It must
    never interleave with the arc pulse's own literal DO0-high -> WaitMs ->
    DO0-low sequence, the one piece of this file with real electrical
    consequences if its timing changes.
    """
    weld = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))
    assert re.search(r"^local SV_WELD_JOLT_TRAVEL\s*=\s*\d+", weld, re.M), \
        "weld.lua no longer declares SV_WELD_JOLT_TRAVEL"

    fire = weld.split("local function fireWeld()", 1)[1].split(
        "local function holdAfterWeld()", 1
    )[0]
    assert ("writeDO(DO_WELD, 1) WaitMs(WELD_PULSE_MS) "
            "writeDO(DO_WELD, 0)") in " ".join(fire.split()), \
        "the arc pulse's own timing must stay a literal, uninterrupted sequence"
    assert "SV_WELD_JOLT_TRAVEL" not in fire, \
        "the jolt reading must not be taken inside fireWeld(), before the hold completes"

    before_arc_at = fire.index("weldZ0 = readToolZ()")
    fire_at = fire.index("writeDO(DO_WELD, 1)")
    assert before_arc_at < fire_at, \
        "the before-arc snapshot must be taken before the pulse fires"

    hold = weld.split("local function holdAfterWeld()", 1)[1].split(
        "local function retract()", 1
    )[0]
    hold_wait_at = hold.index("WaitMs(POST_WELD_HOLD_MS)")
    jolt_travel_at = hold.index("pub(SV_WELD_JOLT_TRAVEL")
    assert jolt_travel_at > hold_wait_at, \
        "SV_WELD_JOLT_TRAVEL must publish after the post-weld hold, not during it"

    # Zeroed at the top of every stud like the other press/weld telemetry, so a
    # fault before the arc fires can't leave a previous stud's number behind.
    reset = weld.split("local function weldOneStud()", 1)[1]
    assert "pub(SV_WELD_JOLT_TRAVEL, 0)" in reset


def test_the_retract_is_a_straight_line():
    """The lift is a Lin: PTP and MoveCart interpolate in joint space, which bows
    the path sideways while the collet is still on the stud. Until 2026-10-01 it
    was a Lin back to the park pose, retracing the tool-Z descent; it now lifts
    straight up the workpiece Z from the head's measured X/Y (see
    test_retract_lifts_straight_up_from_where_the_head_is), keeping the park pose
    as the fallback.
    """
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))

    depart = code.split("local function departFromStud()", 1)[1].split(
        "local FAULT_BEACON_MS", 1
    )[0]
    assert "local liftX, liftY = weldX, weldY" in depart, \
        "without a usable reading the lift must end over the X/Y the caller parked at"
    assert "PointsOffsetEnable(0, liftX, liftY, liftHeight(), 0, 0, 0)" in depart
    assert "Lin(zerozero, RETRACT_SPEED, -1, 0, 0)" in depart
    assert "PointsOffsetDisable()" in depart
    assert "PTP(" not in code and "MoveCart(" not in code, \
        "a joint-space move bows sideways while the collet is still on the stud"

    # Both paths off a stud go through the same departure.
    retract = code.split("local function retract()", 1)[1].split(
        "local function feedNextStud()", 1
    )[0]
    assert "departFromStud()" in retract
    assert retract.index("forceControlOff()") < retract.index("departFromStud()"), \
        "force control must be released before the lift, not during it"

    fault = code.split("local function fault(msg, site)", 1)[1].split(
        "local function waitForWeldReady()", 1
    )[0]
    assert "departFromStud()" in fault, \
        "a fault leaves the collet on the stud too — it needs the same departure"


def test_a_weld_fault_stops_the_current_phase():
    """Fault cleanup must not fall through to later motion or outputs."""
    code = strip_lua_comments(WELD_PATH.read_text(encoding="utf-8"))

    assert "return true" in code.split("local function fault(msg, site)", 1)[1].split(
        "local function waitForWeldReady()", 1
    )[0]
    assert not re.search(r"^\s*(?!return )fault\(", code, re.M), \
        "every fault path must return after cleanup"


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


# --- single_shot.lua --------------------------------------------------------


def test_single_shot_target_and_cycle_count_are_substituted():
    built = build_single_shot_lua(120.5, -45.0, cycles=7, run_mode=LIVE)
    assert "targetX = 120.5" in built.text
    assert "targetY = -45" in built.text
    assert "cycleCount = 7" in built.text
    assert built.stud_count == 1
    assert built.cycles == 7
    assert built.program_name == "single_shot.lua"
    # Nothing unsubstituted may reach the controller.
    assert "--{{" not in built.text


def test_single_shot_lua_substitutes_recipe_parameters():
    built = build_single_shot_lua(
        0, 0, cycles=1, run_mode=LIVE,
        safe_z=15.5, part_z=2.0, pressure_setting="low",
        ft_sensor_num=7,
        stud_type="M6", substrate="Stainless Steel",
    )
    assert "SAFE_Z = 15.5" in built.text
    assert "PART_Z = 2" in built.text
    assert "PRESS_LBF = 17" in built.text
    assert "FT_SENSOR_NUM = 7" in built.text
    assert "WELD_FT_SENSOR_NUM = FT_SENSOR_NUM" in built.text
    assert 'STUD_TYPE = "M6"' in built.text
    assert 'SUBSTRATE = "Stainless Steel"' in built.text


_ASSIGNMENT_LINE = re.compile(r"^\s*(\w+)\s*=(?!=)\s*(.+?)\s*(?:--.*)?$")
_STUD_ROW = re.compile(r"\{x=(-?[\d.]+), y=(-?[\d.]+)")


def _resolve_lua(expr, assignments, stud):
    """Reduce a generated-program expression to a number.

    Covers what the two approach moves use: literals, `a + b`, a variable
    assigned exactly once, and WeldFlex.lua's `stud.x`/`stud.y`.
    """
    expr = expr.strip()
    try:
        return float(expr)
    except ValueError:
        pass
    if "+" in expr:
        return sum(_resolve_lua(term, assignments, stud) for term in expr.split("+"))
    field = re.fullmatch(r"stud\.([xy])", expr)
    if field:
        return stud[field.group(1)]
    values = assignments.get(expr, [])
    assert len(values) == 1, f"{expr} is assigned {len(values)} times before weld.lua runs"
    return _resolve_lua(values[0], assignments, stud)


def _stud_approach(built):
    """Where a caller parks the torch before handing the stud to weld.lua: the
    last PointsOffsetEnable before the NewDofile, resolved to numbers, the point
    the move after it targets, the weldX/weldY weld.lua receives, and the frame."""
    lines = _lines(built)
    dofile = next(i for i, line in enumerate(lines) if 'NewDofile("/fruser/weld.lua"' in line)
    enable = max(i for i in range(dofile) if lines[i].lstrip().startswith("PointsOffsetEnable("))
    args = re.search(r"PointsOffsetEnable\((.*)\)", lines[enable]).group(1).split(",")
    move = re.match(r"\s*(?:Lin|PTP)\((\w+),", lines[enable + 1])
    assert move, f"no move follows the offset: {lines[enable + 1]!r}"

    assignments = {}
    for line in lines[:dofile]:
        match = _ASSIGNMENT_LINE.match(line)
        if match:
            assignments.setdefault(match.group(1), []).append(match.group(2))
    row = _STUD_ROW.search(built.text)
    stud = {"x": float(row.group(1)), "y": float(row.group(2))} if row else None

    def resolve(expr):
        return _resolve_lua(expr, assignments, stud)

    return {
        "offset": [resolve(arg) for arg in args],
        "point": move.group(1),
        "weld_xy": [resolve("weldX"), resolve("weldY")],
        "frame": [resolve("tool"), resolve("wobj")],
        # The park height weld.lua is told, which its search starts from.
        "clearance": resolve("Z_CLEARANCE"),
        # The height weld.lua's retract lifts to, where the next stud feeds.
        "retract": resolve("Z_RETRACT"),
    }


def _over_the_same_point(a, b):
    """Two parks share base point, frame, offset flag and X/Y; Z may differ."""
    return (
        a["point"] == b["point"]
        and a["frame"] == b["frame"]
        and a["weld_xy"] == b["weld_xy"]
        and a["offset"][:3] == b["offset"][:3]
    )


@pytest.mark.parametrize("x, y", [(10, 20), (215.265, 304.79999999999995), (0, 762)])
def test_a_single_shot_goes_where_a_part_stud_at_the_same_xy_goes(x, y):
    """The same X/Y must park the torch over the same point in both programs.

    Each program's approach offset is resolved to numbers, so a change to
    either one's offset frame, argument order, base point, tool/wobj or
    published coordinate fails here. The move type is left out on purpose:
    WeldFlex.lua uses Lin and single_shot.lua PTP, and the offset applies to
    both the same way (FR Lua manual §3.2.12).

    The heights differ on purpose (2026-09-22): a run descends to its Search
    Height before the search, a Single Shot searches from its Safe Z. Either
    way weld.lua is told the height it was actually parked at.
    """
    heights = {"safe_z": 76.2, "search_z": 25.4, "part_z": 63.5}
    part = _stud_approach(build_weldflex_lua([{"x": x, "y": y}], cycles=1, run_mode=LIVE, **heights))
    shot = _stud_approach(build_single_shot_lua(
        x, y, cycles=1, run_mode=LIVE, safe_z=heights["safe_z"], part_z=heights["part_z"],
    ))

    assert _over_the_same_point(shot, part)
    # And that shared point is the designer's: X/Y from zerozero in the workpiece
    # frame (flag 0), handed to weld.lua unchanged.
    assert part["point"] == "zerozero"
    assert part["offset"] == pytest.approx([0, x, y, 63.5 + 25.4, 0, 0, 0], abs=5e-4)
    assert shot["offset"] == pytest.approx([0, x, y, 63.5 + 76.2, 0, 0, 0], abs=5e-4)
    assert part["weld_xy"] == part["offset"][1:3]
    assert part["clearance"] == pytest.approx(part["offset"][3])
    assert shot["clearance"] == pytest.approx(shot["offset"][3])


def test_a_single_shot_lifts_and_feeds_at_its_one_height():
    """A shot has no Retract Z: it searches from its Safe Z, and weld.lua lifts
    back to that same height and feeds there, as it did before Z_RETRACT."""
    shot = _stud_approach(build_single_shot_lua(
        10, 20, cycles=1, run_mode=LIVE, safe_z=50.0, part_z=2.0,
    ))
    assert shot["retract"] == pytest.approx(shot["clearance"])
    assert shot["retract"] == pytest.approx(52.0)


def test_a_back_right_part_parks_where_its_mirrored_bed_point_is():
    """X/Y measured inward from the back-right stops reach the robot as offsets
    from zerozero, and weld.lua's retract gets the same resolved numbers — it
    reuses weldX/weldY, so it follows without a change of its own."""
    heights = {"safe_z": 60.0, "search_z": 10.0, "part_z": 0.0}
    part = _stud_approach(build_weldflex_lua(
        [{"x": 100, "y": 50}], cycles=1, run_mode=LIVE,
        origin_corner="back_right", corner_ref=CornerRef(760.0, 750.0), **heights,
    ))
    assert part["point"] == "zerozero"
    assert part["offset"] == pytest.approx([0, 660, 700, 10, 0, 0, 0], abs=5e-4)
    assert part["weld_xy"] == part["offset"][1:3]
    # Over the same point a single shot aimed at those bed coordinates goes.
    assert _over_the_same_point(
        part, _stud_approach(build_single_shot_lua(
            660, 700, cycles=1, run_mode=LIVE, safe_z=60.0, part_z=0.0,
        ))
    )


def test_a_front_left_part_never_reads_the_corner_ref():
    studs = [{"x": 10, "y": -20.5}, {"x": 373, "y": 1.25}]
    assert (
        build_weldflex_lua(studs, cycles=2, run_mode=LIVE, corner_ref=CornerRef(1.0, 1.0)).text
        == build_weldflex_lua(studs, cycles=2, run_mode=LIVE).text
    )


def test_the_builder_refuses_another_corner_without_its_taught_point():
    with pytest.raises(ValueError, match="zerozero_fr"):
        build_weldflex_lua([{"x": 1, "y": 1}], cycles=1, run_mode=LIVE,
                           origin_corner="front_right")


def test_mirroring_a_part_keeps_its_dynamic_stud_legs(monkeypatch):
    """DSC times each leg from the distance between studs, which a mirror keeps."""
    monkeypatch.setenv("WELDFLEX_DSC_CALIBRATED", "1")
    monkeypatch.setenv("WELDFLEX_DSC_RATE_100_PCT_MMS", "200")
    monkeypatch.setenv("WELDFLEX_DSC_FIXED_OVERHEAD_MS", "150")
    monkeypatch.setenv("WELDFLEX_DSC_SAFETY_MARGIN_MS", "0")
    monkeypatch.setenv("WELDFLEX_FEED_PULSE_MS", "250")

    built = build_weldflex_lua(
        [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 120, "y": 0}],
        cycles=1, run_mode=LIVE, dsc_enabled=True, stud_reload_ms=600,
        origin_corner="back_right", corner_ref=CornerRef(762.0, 762.0),
    )

    assert "{x=762, y=762}," in built.text
    assert "{x=742, y=762, s2sSpeed=50, s2sWaitMs=0}," in built.text
    assert "{x=642, y=762, s2sSpeed=100, s2sWaitMs=0}," in built.text


def test_the_builder_refuses_a_stud_that_would_flip_across_the_bed():
    with pytest.raises(ValueError, match="Stud 2"):
        build_weldflex_lua(
            [{"x": 1, "y": 1}, {"x": 800, "y": 0}], cycles=1, run_mode=LIVE,
            origin_corner="front_right", corner_ref=CornerRef(762.0, 762.0),
        )


def test_single_shot_marker_line_really_is_the_boundary_dwell():
    """Same load-bearing assertion as WeldFlex.lua's — the job manager counts
    cycles by watching GetCurrentLine cross these lines."""
    built = build_single_shot_lua(1, 2, cycles=3, run_mode=LIVE)
    lines = built.text.splitlines()
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert "for cycleIndex = 1, cycleCount do" in lines[built.loop_start_line - 1]
    assert built.loop_start_line < built.cycle_marker_line < built.gate_line


def test_single_shot_program_line_count_stays_under_weld_lua():
    """Same invariant as WeldFlex.lua's — see
    test_program_line_count_matches_the_built_text_and_stays_under_weld_lua.
    single_shot.lua has no inner stud loop, so it is even shorter, and the
    margin against weld.lua's ~600 lines is even wider."""
    built = build_single_shot_lua(1, 2, cycles=3, run_mode=LIVE)
    assert built.program_line_count == len(built.text.splitlines())
    weld_lua_lines = len(WELD_PATH.read_text(encoding="utf-8").splitlines())
    assert built.program_line_count < weld_lua_lines


@pytest.mark.parametrize("cycles", [1, 5, 999])
def test_single_shot_marker_lines_track_cycle_count(cycles):
    built = build_single_shot_lua(1, 1, cycles=cycles, run_mode=LIVE)
    lines = built.text.splitlines()
    assert "WaitMs(BOUNDARY_MS)" in lines[built.cycle_marker_line - 1]
    assert f"cycleCount = {cycles}" in built.text


def test_single_shot_lua_stays_over_the_target():
    """Owner decision 2026-09-14: a shot feeds and stays put. Nothing in what is
    sent moves to homewf; the page's Move Home button does that."""
    built = build_single_shot_lua(10, 20, cycles=1, run_mode=LIVE)
    assert "homewf" not in strip_lua_comments(built.text)


def test_single_shot_lua_publishes_the_machine_feed_pulse(monkeypatch):
    """weld.lua feeds after the shot, so the caller hands it the same
    machine-configured pulse WeldFlex.lua does."""
    monkeypatch.setenv("WELDFLEX_FEED_PULSE_MS", "300")
    built = build_single_shot_lua(10, 20, cycles=1, run_mode=LIVE)
    assert "FEED_PULSE_MS = 300" in built.text
    assert "WELD_FEED_PULSE_MS = FEED_PULSE_MS" in built.text


def test_single_shot_lua_builds_cleanly():
    built = build_single_shot_lua(10, 20, cycles=1, run_mode=LIVE)
    text = built.text
    assert "SetDO(1, 1, 0, 0)" not in text
    assert 'NewDofile("/fruser/weld.lua", 1, 1)' in text


def test_single_shot_lua_upload_gate_matches_weldflex():
    weld = WELD_PATH.read_text(encoding="utf-8")
    template = SINGLE_SHOT_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "if WELD_RUN == 1 then" in weld
    assert "WELD_RUN = 1" in template


def test_single_shot_lua_never_receives_a_protected_call():
    sent = strip_lua_comments(SINGLE_SHOT_TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert "pcall" not in sent, "the controller will refuse this upload outright"


def test_single_shot_missing_marker_is_a_hard_error(tmp_path):
    bad = tmp_path / "single_shot.lua"
    bad.write_text("targetX = 0 --{{TARGET_X}}\n--{{CYCLE_COUNT}}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing required marker"):
        build_single_shot_lua(0, 0, cycles=1, run_mode=LIVE, template_path=bad)


def test_single_shot_rejects_bad_gate_mode_or_cycles():
    with pytest.raises(ValueError):
        build_single_shot_lua(0, 0, cycles=1, run_mode=LIVE, gate_mode="nope")
    with pytest.raises(ValueError):
        build_single_shot_lua(0, 0, cycles=0, run_mode=LIVE)


