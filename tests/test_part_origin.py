"""Which bed corner a part's studs are measured from, and how that becomes an
offset from zerozero.

Pure functions, plus one check across the language boundary: the designer's
corner keys must be these, or a corner it saves is refused by the server.
"""

import re
from pathlib import Path

import pytest

from part_origin import (
    BED_NOMINAL_MM,
    BED_X_MM_ENV,
    BED_Y_MM_ENV,
    CORNERS,
    DEFAULT_CORNER,
    BedSpan,
    bed_span_from_env,
    parse_corner,
    resolve_point,
    resolve_studs,
    to_bed,
)

DESIGNER_JS = Path(__file__).resolve().parents[1] / "backend" / "static" / "js" / "part_designer.js"

# Deliberately not square and not nominal, so a swapped axis or a hard-coded 762
# shows up.
SPAN = BedSpan(760.0, 750.0)


@pytest.fixture(autouse=True)
def _nominal_bed(monkeypatch):
    monkeypatch.delenv(BED_X_MM_ENV, raising=False)
    monkeypatch.delenv(BED_Y_MM_ENV, raising=False)


@pytest.mark.parametrize("corner, expected", [
    ("front_left", (100, 50)),
    ("front_right", (660, 50)),
    ("back_left", (100, 700)),
    ("back_right", (660, 700)),
])
def test_each_corner_measures_inward_from_its_own_stops(corner, expected):
    assert to_bed(100, 50, corner, SPAN) == pytest.approx(expected)


@pytest.mark.parametrize("corner, expected", [
    ("front_left", (0, 0)),
    ("front_right", (760, 0)),
    ("back_left", (0, 750)),
    ("back_right", (760, 750)),
])
def test_a_parts_0_0_is_its_own_corner_of_the_bed(corner, expected):
    assert to_bed(0, 0, corner, SPAN) == expected


def test_front_left_is_zerozero_and_passes_through_untouched(monkeypatch):
    """Every part saved before corners existed reads as front-left, so its studs
    must reach the robot exactly as before — including values off the bed, which
    were never checked, and whatever the span is set to, which it never reads."""
    monkeypatch.setenv(BED_X_MM_ENV, "not a number")
    studs = [{"x": 10, "y": -20.5, "s2sSpeed": 50}, {"x": 800, "y": 0}]
    assert DEFAULT_CORNER == "front_left"
    assert resolve_studs(studs, "front_left") == studs


def test_resolving_keeps_every_other_stud_key():
    assert resolve_studs([{"x": 10, "y": 20, "extra": 1}], "back_right", SPAN) == [
        {"x": 750.0, "y": 730.0, "extra": 1}
    ]


@pytest.mark.parametrize("corner, stud, axis", [
    ("front_right", {"x": 760.5, "y": 0}, "X"),
    ("front_right", {"x": -1, "y": 0}, "X"),
    ("back_left", {"x": 0, "y": 751}, "Y"),
    ("back_right", {"x": 10, "y": -0.1}, "Y"),
])
def test_a_mirrored_stud_past_the_far_stop_is_refused(corner, stud, axis):
    """Past the far stop a mirrored value doesn't land just past it: it becomes
    a negative offset, on the far side of the bed behind zerozero."""
    with pytest.raises(ValueError, match=rf"Stud 2 .* along {axis}"):
        resolve_studs([{"x": 1, "y": 1}, stud], corner, SPAN)


def test_the_unmirrored_axis_of_a_corner_passes_through_like_front_left():
    assert resolve_studs([{"x": 10, "y": -5}], "front_right", SPAN) == [{"x": 750.0, "y": -5.0}]


def test_an_edge_stud_is_not_refused_for_float_noise():
    """30 in typed in the designer is 762 mm give or take a last bit — and that
    bit must not reach the robot as a -1e-10 offset."""
    assert resolve_point(762.0000000001, 0, "front_right", BedSpan(762.0, 762.0)) == (0.0, 0.0)


def test_the_bed_span_defaults_to_the_nominal_bed():
    assert bed_span_from_env() == BedSpan(BED_NOMINAL_MM, BED_NOMINAL_MM)


def test_the_bed_span_is_read_from_the_measured_stops(monkeypatch):
    monkeypatch.setenv(BED_X_MM_ENV, "758.5")
    monkeypatch.setenv(BED_Y_MM_ENV, "760")
    assert bed_span_from_env() == BedSpan(758.5, 760.0)
    assert resolve_studs([{"x": 10, "y": 10}], "back_right") == [{"x": 748.5, "y": 750.0}]


@pytest.mark.parametrize("raw", ["abc", "0", "-5"])
def test_a_bad_bed_span_is_refused_rather_than_guessed(monkeypatch, raw):
    monkeypatch.setenv(BED_Y_MM_ENV, raw)
    with pytest.raises(ValueError, match=BED_Y_MM_ENV):
        bed_span_from_env()


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
        to_bed(1, 1, "top_right", SPAN)


def test_the_designer_uses_the_same_corner_keys():
    js = DESIGNER_JS.read_text(encoding="utf-8")
    keys = re.search(r"const PD_CORNERS = \[([^\]]*)\];", js)
    assert keys, "PD_CORNERS not found in part_designer.js"
    assert tuple(re.findall(r"'(\w+)'", keys.group(1))) == CORNERS
