"""Which bed corner a part's stud X/Y are measured from.

A part is tooled (fixtured) against the stops at one corner of the bed, and its
studs are dimensioned inward from that corner, so every coordinate is positive.
The robot only knows one reference: the taught `zerozero` point at the bed's
front-left corner, with work object 2's +X to the right and +Y toward the back.
This module turns a part's corner-relative X/Y into offsets from `zerozero`.

The other three corners are *computed*, not taught: `zerozero` plus the measured
distance to the right-hand stop (`WELDFLEX_BED_X_MM`) and the back stop
(`WELDFLEX_BED_Y_MM`). That assumes wobj 2's axes run along the bed edges.
Teaching a reference point at each corner is the likely next step; keep every
corner decision in this module so that change stays here.

Mirroring stud positions is safe where mirroring a coordinate frame is not: a
controller frame must stay right-handed with +Z up, but a stud is a point welded
with the tool vertical, and `dynamic_stud_legs` only uses distances between
studs, which a mirror preserves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

# Named as the operator sees the bed. Front is the operator's side; zerozero is
# taught at the front-left. static/js/part_designer.js carries the same keys
# (PD_CORNERS) and tests/test_part_origin.py asserts the two agree.
CORNERS = ("front_left", "front_right", "back_left", "back_right")
DEFAULT_CORNER = "front_left"
CORNER_LABELS = {
    "front_left": "Front-left",
    "front_right": "Front-right",
    "back_left": "Back-left",
    "back_right": "Back-right",
}

BED_X_MM_ENV = "WELDFLEX_BED_X_MM"
BED_Y_MM_ENV = "WELDFLEX_BED_Y_MM"
# The 30 in bed, in mm — the nominal size the part designer draws (BED_MM in
# app.py, BED in part_designer.js). The real stop-to-stop distances go in .env.
BED_NOMINAL_MM = 762.0

# Slack on the off-bed check so an edge stud typed in inches (30 in → 762 mm plus
# float noise) is not refused. Far below the 0.001 mm the Lua is written to.
_EDGE_TOLERANCE_MM = 1e-6


@dataclass(frozen=True)
class BedSpan:
    """Distance from zerozero to the far stops, along wobj 2's +X and +Y (mm)."""

    x_mm: float
    y_mm: float


def _env_span(name: str) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return BED_NOMINAL_MM
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number of mm, got {raw!r}") from None
    if not value > 0:
        raise ValueError(f"{name} must be greater than 0 mm, got {raw!r}")
    return value


def bed_span_from_env() -> BedSpan:
    """The measured stop-to-stop distances, defaulting to the nominal bed.

    Read on every call, like lua_builder.default_dsc_calibration(), so a changed
    .env takes effect on restart and tests can monkeypatch it. A bad value
    raises rather than falling back: guessing the far corners would put every
    stud of a right- or back-cornered part in the wrong place.
    """
    return BedSpan(_env_span(BED_X_MM_ENV), _env_span(BED_Y_MM_ENV))


def parse_corner(value, *, strict: bool = False) -> str:
    """Normalise a stored or submitted corner key.

    Missing means the default: every part saved before corners existed was
    measured from zerozero. An unknown value also reads as the default, unless
    `strict`, which raises instead — for input that is about to be saved or run.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_CORNER
    key = str(value).strip().lower()
    if key in CORNERS:
        return key
    if strict:
        raise ValueError(f"Unknown origin corner {value!r}; expected one of {', '.join(CORNERS)}")
    return DEFAULT_CORNER


def _mirrors(corner: str) -> tuple[bool, bool]:
    """Whether a corner measures X leftward / Y frontward (against wobj 2)."""
    return corner.endswith("_right"), corner.startswith("back_")


def to_bed(x: float, y: float, corner: str, span: BedSpan) -> tuple[float, float]:
    """Corner-relative X/Y → offsets from zerozero along wobj 2's axes."""
    corner = parse_corner(corner, strict=True)
    mirror_x, mirror_y = _mirrors(corner)
    return (span.x_mm - x if mirror_x else x,
            span.y_mm - y if mirror_y else y)


def resolve_point(x: float, y: float, corner: str, span: BedSpan | None = None,
                  *, what: str = "Point") -> tuple[float, float]:
    """`to_bed`, refusing a value that would flip across the bed.

    Only a mirrored axis is checked. There, a number past the far stop does not
    land just past it — it becomes a negative offset behind zerozero, on the
    opposite side of the bed. An axis measured from zerozero passes through
    unchecked, exactly as every part did before corners existed — and a
    front-left point never reads the span at all, so a bad .env value cannot
    stop a part that does not use it.
    """
    corner = parse_corner(corner, strict=True)
    mirror_x, mirror_y = _mirrors(corner)
    if not (mirror_x or mirror_y):
        return x, y
    span = bed_span_from_env() if span is None else span
    x, y = float(x), float(y)
    tol = _EDGE_TOLERANCE_MM
    for axis, value, limit, mirrored in (("X", x, span.x_mm, mirror_x),
                                         ("Y", y, span.y_mm, mirror_y)):
        if mirrored and not -tol <= value <= limit + tol:
            raise ValueError(
                f"{what} is {value:g} mm from the {CORNER_LABELS[corner].lower()} corner "
                f"along {axis}; it must be 0 to {limit:g} mm, the measured distance "
                f"between the stops."
            )
    # Within the tolerance: snap onto the stop, so float noise reaches the robot
    # as 0, not -1e-10.
    if mirror_x:
        x = min(max(x, 0.0), span.x_mm)
    if mirror_y:
        y = min(max(y, 0.0), span.y_mm)
    return to_bed(x, y, corner, span)


def resolve_studs(studs: Sequence[dict], corner: str,
                  span: BedSpan | None = None) -> list[dict]:
    """A part's studs as offsets from zerozero, every other key kept.

    Raises ValueError naming the first stud that would flip across the bed.
    """
    corner = parse_corner(corner, strict=True)
    if any(_mirrors(corner)) and span is None:
        span = bed_span_from_env()
    resolved = []
    for index, stud in enumerate(studs):
        bed_x, bed_y = resolve_point(stud["x"], stud["y"], corner, span,
                                     what=f"Stud {index + 1}")
        resolved.append({**stud, "x": bed_x, "y": bed_y})
    return resolved
