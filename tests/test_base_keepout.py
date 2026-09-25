"""The robot base no-go circle: settings, stud and travel checks, and where it is enforced."""

import pytest

import base_keepout
import lua_builder
from base_keepout import Keepout, check_move, check_point, check_studs, keepout_from_env

# Roughly the real cell (2026-09-25): J1 off the bed's left edge, level with
# the middle of the 24 in plate.
ZONE = Keepout(-130.0, 320.0, 250.0)


@pytest.fixture
def zone_env(monkeypatch):
    def set_env(x="-130", y="320", radius="250"):
        for name, value in ((base_keepout.BASE_X_ENV, x), (base_keepout.BASE_Y_ENV, y),
                            (base_keepout.RADIUS_ENV, radius)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
    return set_env


def test_unset_means_no_zone(zone_env):
    zone_env(None, None, None)
    assert keepout_from_env() is None
    check_point(-130, 320, "Stud 1")  # the base itself: nothing to check against


def test_partly_set_refuses_rather_than_switching_off(zone_env):
    zone_env(radius=None)
    with pytest.raises(ValueError, match="only partly set"):
        keepout_from_env()


@pytest.mark.parametrize("radius", ["abc", "0", "-5"])
def test_bad_radius_refuses(zone_env, radius):
    zone_env(radius=radius)
    with pytest.raises(ValueError, match=base_keepout.RADIUS_ENV):
        keepout_from_env()


def test_the_stud_that_nearly_hit_the_arm_is_refused():
    # fabtech polygon's stud 5, 175 mm from J1.
    with pytest.raises(ValueError, match="Stud 5 comes within 175 mm"):
        check_point(44.7, 304.8, "Stud 5", ZONE)


def test_a_stud_clear_of_the_base_passes():
    check_point(304.8, 304.8, "Stud 41", ZONE)


def test_a_level_move_through_the_zone_is_refused_even_between_clear_ends():
    # Both ends are well clear; the straight line between them passes the base.
    with pytest.raises(ValueError, match="move"):
        check_move((0.0, 700.0), (0.0, -60.0), "The move", ZONE)


def test_a_move_heading_away_from_the_base_passes_from_inside_the_zone():
    check_move((44.7, 304.8), (304.8, 304.8), "The level move", ZONE)


def test_a_move_heading_toward_the_base_is_refused():
    with pytest.raises(ValueError):
        check_move((304.8, 304.8), (200.0, 304.8), "The level move", Keepout(-130, 320, 340))


def test_studs_check_names_the_leg_between_studs():
    studs = [{"x": 0.0, "y": 700.0}, {"x": 0.0, "y": -60.0}]
    with pytest.raises(ValueError, match="from stud 1 to stud 2"):
        check_studs(studs, ZONE)


def test_program_build_refuses_a_part_inside_the_zone(zone_env):
    zone_env()
    with pytest.raises(ValueError, match="Stud 1"):
        lua_builder.build_weldflex_lua(
            studs=[{"x": 44.7, "y": 304.8}], cycles=1, gate_mode="none",
            run_mode=lua_builder.RunMode("dry"),
        )
