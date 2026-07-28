import pytest

from lua_builder import GATE_DI_OPT_ABORT, build_weldflex_lua, format_number


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
    assert "WaitMs(1500)" in lines[built.cycle_marker_line - 1]
    assert "for cycleIndex = 1, cycleCount do" in lines[built.loop_start_line - 1]
    assert built.loop_start_line < built.cycle_marker_line < built.gate_line


@pytest.mark.parametrize("n_studs,cycles", [(0, 1), (1, 1), (5, 20), (40, 999)])
def test_marker_lines_track_stud_count(n_studs, cycles):
    built = build_weldflex_lua([{"x": i, "y": i} for i in range(n_studs)], cycles=cycles)
    lines = _lines(built)
    assert "WaitMs(1500)" in lines[built.cycle_marker_line - 1]
    assert f"cycleCount = {cycles}" in built.text


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


def test_format_number():
    assert format_number(373) == "373"
    assert format_number(373.0) == "373"
    assert format_number(-20.5) == "-20.5"
    assert format_number(1.250) == "1.25"
