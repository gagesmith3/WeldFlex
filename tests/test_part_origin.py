"""Which bed corner a part's studs are measured from, and how that becomes an
offset from zerozero through the corner's taught point.

Pure functions, plus one check across the language boundary: the designer's
corner keys must be these, or a corner it saves is refused by the server.
"""

import re
from pathlib import Path

import pytest

from part_origin import (
    BED_NOMINAL_MM,
    CORNER_POINTS,
    CORNERS,
    DEFAULT_CORNER,
    ZERO_REF,
    CornerRef,
    corner_ref_from_poses,
    nominal_ref,
    parse_corner,
    read_corner_ref,
    resolve_point,
    resolve_studs,
    to_bed,
)

DESIGNER_JS = Path(__file__).resolve().parents[1] / "backend" / "static" / "js" / "part_designer.js"

# Deliberately not square and not nominal, so a swapped axis or a hard-coded 762
# shows up.
REFS = {
    "front_left": ZERO_REF,
    "front_right": CornerRef(760.0, 0.0),
    "back_left": CornerRef(0.0, 750.0),
    "back_right": CornerRef(760.0, 750.0),
}
ZEROZERO = [400.0, -200.0, 50.0, 180.0, 0.0, 90.0]


def _pose(dx, dy, dz=0.0):
    return [ZEROZERO[0] + dx, ZEROZERO[1] + dy, ZEROZERO[2] + dz] + ZEROZERO[3:]


@pytest.mark.parametrize("corner, expected", [
    ("front_left", (100, 50)),
    ("front_right", (660, 50)),
    ("back_left", (100, 700)),
    ("back_right", (660, 700)),
])
def test_each_corner_measures_inward_from_its_own_stops(corner, expected):
    assert to_bed(100, 50, corner, REFS[corner]) == pytest.approx(expected)


@pytest.mark.parametrize("corner", CORNERS)
def test_a_parts_0_0_is_its_own_corner_of_the_bed(corner):
    ref = REFS[corner]
    assert to_bed(0, 0, corner, ref) == (ref.x_mm, ref.y_mm)


def test_a_skewed_corner_point_carries_its_skew_to_every_stud():
    """zerozero_fr taught 1.5 mm behind zerozero: the part sits where it was taught."""
    assert to_bed(100, 50, "front_right", CornerRef(736.6, 1.5)) == pytest.approx((636.6, 51.5))


def test_front_left_is_zerozero_and_passes_through_untouched():
    """Every part saved before corners existed reads as front-left, so its studs
    must reach the robot exactly as before — including values off the bed, which
    were never checked, and without a ref, which it never needs."""
    studs = [{"x": 10, "y": -20.5, "s2sSpeed": 50}, {"x": 800, "y": 0}]
    assert DEFAULT_CORNER == "front_left"
    assert resolve_studs(studs, "front_left") == studs


def test_resolving_keeps_every_other_stud_key():
    assert resolve_studs([{"x": 10, "y": 20, "extra": 1}], "back_right", REFS["back_right"]) == [
        {"x": 750.0, "y": 730.0, "extra": 1}
    ]


@pytest.mark.parametrize("corner", ["front_right", "back_left", "back_right"])
def test_another_corner_without_its_ref_is_refused(corner):
    with pytest.raises(ValueError, match=CORNER_POINTS[corner]):
        resolve_studs([{"x": 1, "y": 1}], corner)


@pytest.mark.parametrize("corner, stud, axis", [
    ("front_right", {"x": 760.5, "y": 0}, "X"),
    ("front_right", {"x": -1, "y": 0}, "X"),
    ("back_left", {"x": 0, "y": 751}, "Y"),
    ("back_right", {"x": 10, "y": -0.1}, "Y"),
])
def test_a_mirrored_stud_past_the_far_stop_is_refused(corner, stud, axis):
    """Past zerozero's side a mirrored value doesn't land just past it: it becomes
    a negative offset, on the far side of the bed behind zerozero."""
    with pytest.raises(ValueError, match=rf"Stud 2 .* along {axis}"):
        resolve_studs([{"x": 1, "y": 1}, stud], corner, REFS[corner])


def test_the_unmirrored_axis_of_a_corner_passes_through_like_front_left():
    assert resolve_studs([{"x": 10, "y": -5}], "front_right", REFS["front_right"]) == [
        {"x": 750.0, "y": -5.0}
    ]


def test_an_edge_stud_is_not_refused_for_float_noise():
    """30 in typed in the designer is 762 mm give or take a last bit — and that
    bit must not reach the robot as a -1e-10 offset."""
    assert resolve_point(762.0000000001, 0, "front_right", CornerRef(762.0, 0.0)) == (0.0, 0.0)


def test_a_corner_ref_is_the_taught_point_less_zerozero_ignoring_height():
    ref = corner_ref_from_poses("back_right", ZEROZERO, _pose(736.6, 740.2, dz=4.0))
    assert ref == CornerRef(pytest.approx(736.6), pytest.approx(740.2))


def test_front_left_reads_no_taught_point():
    def read(name):
        raise AssertionError(f"read {name}")
    assert read_corner_ref("front_left", read) == ZERO_REF


def test_a_corner_ref_is_read_from_the_corners_own_point():
    poses = {"zerozero": ZEROZERO, "zerozero_fr": _pose(736.6, 1.5)}
    assert read_corner_ref("front_right", poses.__getitem__) == CornerRef(
        pytest.approx(736.6), pytest.approx(1.5))


def test_an_untaught_corner_point_says_which_point_to_teach():
    def read(name):
        if name != "zerozero":
            raise RuntimeError(f"Can't read taught point {name!r} (code -1)")
        return ZEROZERO
    with pytest.raises(ValueError, match="Teach zerozero_bl"):
        read_corner_ref("back_left", read)


@pytest.mark.parametrize("corner, offset, match", [
    ("front_right", (-736.6, 0.0), "should be right of zerozero"),
    ("back_left", (0.0, -740.0), "should be behind zerozero"),
    ("back_right", (736.6, 0.0), "should be behind zerozero"),
    ("front_right", (736.6, 40.0), "should line up with it"),
    ("back_left", (-30.0, 740.0), "should line up with it"),
])
def test_a_corner_point_in_the_wrong_place_is_refused(corner, offset, match):
    """Wrong side of zerozero, or well off its bed edge: a wrong frame or a
    mis-taught point, which would put every stud somewhere else."""
    with pytest.raises(ValueError, match=match):
        corner_ref_from_poses(corner, ZEROZERO, _pose(*offset))


def test_the_preview_draws_the_nominal_bed():
    assert nominal_ref("front_left") == ZERO_REF
    assert nominal_ref("back_right") == CornerRef(BED_NOMINAL_MM, BED_NOMINAL_MM)
    assert nominal_ref("front_right") == CornerRef(BED_NOMINAL_MM, 0.0)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_a_missing_corner_is_zerozero(value):
    assert parse_corner(value) == "front_left"
    assert parse_corner(value, strict=True) == "front_left"


def test_an_unknown_corner_is_refused_when_strict():
    assert parse_corner(" Back_Right ", strict=True) == "back_right"
    assert parse_corner("top_right") == "front_left"
    with pytest.raises(ValueError, match="top_right"):
        parse_corner("top_right", strict=True)
    with pytest.raises(ValueError, match="top_right"):
        to_bed(1, 1, "top_right", ZERO_REF)


def test_the_designer_uses_the_same_corner_keys():
    js = DESIGNER_JS.read_text(encoding="utf-8")
    keys = re.search(r"const PD_CORNERS = \[([^\]]*)\];", js)
    assert keys, "PD_CORNERS not found in part_designer.js"
    assert tuple(re.findall(r"'(\w+)'", keys.group(1))) == CORNERS
