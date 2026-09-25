"""Which bed corner a part's stud X/Y are measured from.

A part is tooled (fixtured) against the stops at one corner of the bed, and its
studs are dimensioned inward from that corner, so every coordinate is positive.
Every robot move is an offset from one reference, the taught `zerozero` point
at the bed's front-left corner, with work object 2's +X to the right and +Y
toward the back. This module turns a part's corner-relative X/Y into offsets
from `zerozero`.

Each other corner has its own taught point (CORNER_POINTS). Its X/Y relative to
`zerozero` is that corner's `CornerRef`, read from the controller when a part is
loaded or a stud is Goto'd. A stud is then that ref plus its X/Y, with X
mirrored for a right corner and Y for a back corner so both run inward. The
corner's Z is not used: studs keep zerozero's height, and the weld's surface
search finds the real plate.

This replaced a v1 that computed the corners from stop-to-stop distances in
.env. Those were never measured, and the first front-right Goto landed about
3 in past the corner (2026-09-25). A corner whose point isn't taught is refused
now rather than guessed.

Mirroring stud positions is safe where mirroring a coordinate frame is not: a
controller frame must stay right-handed with +Z up, but a stud is a point welded
with the tool vertical, and `dynamic_stud_legs` only uses distances between
studs, which a mirror preserves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

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
# The taught controller point at each corner. Front-left is zerozero itself.
CORNER_POINTS = {
    "front_left": "zerozero",
    "front_right": "zerozero_fr",
    "back_left": "zerozero_bl",
    "back_right": "zerozero_br",
}

# The 30 in bed, in mm — the nominal size the part designer draws (BED_MM in
# app.py, BED in part_designer.js). Only the preview uses it now.
BED_NOMINAL_MM = 762.0

# How far a corner point may sit off the line along zerozero's axes, mm. A part
# is assumed to square up to wobj 2, so a corner point well off that line means
# wobj 2 isn't square to the bed or the point was taught in another frame, and
# every stud would land rotated.
MAX_CORNER_SKEW_MM = 25.0

# Slack on the off-bed check so an edge stud typed in inches (30 in → 762 mm plus
# float noise) is not refused. Far below the 0.001 mm the Lua is written to.
_EDGE_TOLERANCE_MM = 1e-6


@dataclass(frozen=True)
class CornerRef:
    """A corner's taught point relative to zerozero, along wobj 2's +X and +Y (mm)."""

    x_mm: float
    y_mm: float


ZERO_REF = CornerRef(0.0, 0.0)


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


def needs_ref(corner: str) -> bool:
    """Whether a corner is anywhere but zerozero, so it needs its taught point."""
    return any(_mirrors(parse_corner(corner, strict=True)))


def nominal_ref(corner: str) -> CornerRef:
    """Where a corner would be on the nominal square bed. For drawing only."""
    mirror_x, mirror_y = _mirrors(parse_corner(corner, strict=True))
    return CornerRef(BED_NOMINAL_MM if mirror_x else 0.0, BED_NOMINAL_MM if mirror_y else 0.0)


def corner_ref_from_poses(corner: str, zerozero_pose: Sequence[float],
                          corner_pose: Sequence[float]) -> CornerRef:
    """A corner's ref from its taught pose and zerozero's, both in wobj 2.

    Refuses a point on the wrong side of zerozero, or well off the bed edge it
    should share with zerozero. Either means a wrong frame or a mis-taught
    point, and the studs would land somewhere else entirely.
    """
    corner = parse_corner(corner, strict=True)
    if not needs_ref(corner):
        return ZERO_REF
    point = CORNER_POINTS[corner]
    ref = CornerRef(float(corner_pose[0]) - float(zerozero_pose[0]),
                    float(corner_pose[1]) - float(zerozero_pose[1]))
    mirror_x, mirror_y = _mirrors(corner)
    for axis, value, mirrored, toward in (("X", ref.x_mm, mirror_x, "right of"),
                                          ("Y", ref.y_mm, mirror_y, "behind")):
        if mirrored and not value > 0:
            raise ValueError(
                f"{point} reads {value:.1f} mm along {axis} from zerozero; it should be "
                f"{toward} zerozero. Check it was taught at the "
                f"{CORNER_LABELS[corner].lower()} stop with wobj 2 active."
            )
        if not mirrored and abs(value) > MAX_CORNER_SKEW_MM:
            raise ValueError(
                f"{point} reads {value:.1f} mm along {axis} from zerozero, but should line "
                f"up with it (within {MAX_CORNER_SKEW_MM:g} mm). Either wobj 2 isn't square "
                f"to the bed or the point was taught in another frame."
            )
    return ref


def read_corner_ref(corner: str, read_pose: Callable[[str], Sequence[float]]) -> CornerRef:
    """A corner's ref, read from the controller through `read_pose(point_name)`.

    Front-left reads nothing. Anything `read_pose` raises for a missing point
    comes back as a ValueError telling the operator which point to teach.
    """
    corner = parse_corner(corner, strict=True)
    if not needs_ref(corner):
        return ZERO_REF
    point = CORNER_POINTS[corner]
    try:
        zerozero = read_pose(CORNER_POINTS[DEFAULT_CORNER])
        corner_pose = read_pose(point)
    except Exception as exc:
        raise ValueError(
            f"A {CORNER_LABELS[corner].lower()} part is measured from the taught point "
            f"{point}, and it couldn't be read ({exc}). Teach {point} at that corner's "
            f"stops first."
        ) from None
    return corner_ref_from_poses(corner, zerozero, corner_pose)


def to_bed(x: float, y: float, corner: str, ref: CornerRef) -> tuple[float, float]:
    """Corner-relative X/Y → offsets from zerozero along wobj 2's axes."""
    corner = parse_corner(corner, strict=True)
    mirror_x, mirror_y = _mirrors(corner)
    return (ref.x_mm - x if mirror_x else ref.x_mm + x,
            ref.y_mm - y if mirror_y else ref.y_mm + y)


def resolve_point(x: float, y: float, corner: str, ref: CornerRef | None = None,
                  *, what: str = "Point") -> tuple[float, float]:
    """`to_bed`, refusing a value that would flip across the bed.

    Only a mirrored axis is checked. There, a number past zerozero's side does
    not land just past it — it becomes a negative offset behind zerozero, on
    the opposite side of the bed. An axis measured from zerozero passes through
    unchecked, exactly as every part did before corners existed — and a
    front-left point never needs a ref at all.
    """
    corner = parse_corner(corner, strict=True)
    mirror_x, mirror_y = _mirrors(corner)
    if not (mirror_x or mirror_y):
        return x, y
    if ref is None:
        raise ValueError(f"A {CORNER_LABELS[corner].lower()} point needs its corner's "
                         f"taught position ({CORNER_POINTS[corner]}).")
    x, y = float(x), float(y)
    tol = _EDGE_TOLERANCE_MM
    for axis, value, limit, mirrored in (("X", x, ref.x_mm, mirror_x),
                                         ("Y", y, ref.y_mm, mirror_y)):
        if mirrored and not -tol <= value <= limit + tol:
            raise ValueError(
                f"{what} is {value:g} mm from the {CORNER_LABELS[corner].lower()} corner "
                f"along {axis}; it must be 0 to {limit:.1f} mm, the distance from "
                f"{CORNER_POINTS[corner]} to zerozero."
            )
    # Within the tolerance: snap onto the stop, so float noise reaches the robot
    # as the corner, not a hair past it.
    if mirror_x:
        x = min(max(x, 0.0), ref.x_mm)
    if mirror_y:
        y = min(max(y, 0.0), ref.y_mm)
    return to_bed(x, y, corner, ref)


def resolve_studs(studs: Sequence[dict], corner: str,
                  ref: CornerRef | None = None) -> list[dict]:
    """A part's studs as offsets from zerozero, every other key kept.

    Raises ValueError naming the first stud that would flip across the bed, or
    when a corner other than front-left comes without its ref.
    """
    corner = parse_corner(corner, strict=True)
    resolved = []
    for index, stud in enumerate(studs):
        bed_x, bed_y = resolve_point(stud["x"], stud["y"], corner, ref,
                                     what=f"Stud {index + 1}")
        resolved.append({**stud, "x": bed_x, "y": bed_y})
    return resolved
