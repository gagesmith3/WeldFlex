"""The no-go circle around the robot's base.

The FR16 is mounted beside the bed, so the bed reaches in close to J1. Near
the base the arm can't hold the head vertical without folding, and it folds J2
up under the head: a Goto to a stud 175 mm from J1 was stopped just short of
hitting the arm on its level move in (2026-09-25). The controller has no model
of the weld head, so nothing on the robot side catches this.

The circle is J1's axis, as an offset from zerozero in the wobj-2 frame (the
same numbers a stud resolves to), plus a radius the head must stay outside of.
It is checked at every stud and along every level move between two of them,
since the near miss happened mid-travel.

All three settings unset means no zone: a guessed base position would refuse
good studs or pass bad ones. Partly set, or not a number, raises instead, so a
typo can't quietly switch the check off.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Sequence

BASE_X_ENV = "WELDFLEX_BASE_X_MM"
BASE_Y_ENV = "WELDFLEX_BASE_Y_MM"
RADIUS_ENV = "WELDFLEX_BASE_KEEPOUT_MM"


@dataclass(frozen=True)
class Keepout:
    """J1's axis relative to zerozero, and the radius the head stays outside of (mm)."""

    x_mm: float
    y_mm: float
    radius_mm: float


def keepout_from_env() -> Keepout | None:
    """The configured zone, or None when none is set. Read on every call, like
    part_origin.bed_span_from_env(), so tests can monkeypatch it."""
    raw = {name: os.getenv(name, "").strip() for name in (BASE_X_ENV, BASE_Y_ENV, RADIUS_ENV)}
    if not any(raw.values()):
        return None
    missing = [name for name, value in raw.items() if not value]
    if missing:
        raise ValueError(f"The robot base no-go zone is only partly set; also set {', '.join(missing)}.")
    values = {}
    for name, text in raw.items():
        try:
            values[name] = float(text)
        except ValueError:
            raise ValueError(f"{name} must be a number of mm, got {text!r}") from None
    if not values[RADIUS_ENV] > 0:
        raise ValueError(f"{RADIUS_ENV} must be greater than 0 mm, got {raw[RADIUS_ENV]!r}")
    return Keepout(values[BASE_X_ENV], values[BASE_Y_ENV], values[RADIUS_ENV])


def _closest_on_segment(ax: float, ay: float, bx: float, by: float,
                        px: float, py: float) -> tuple[float, float]:
    """Where along a→b (0 to 1) it comes closest to (px, py), and how close."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return t, math.hypot(ax + t * dx - px, ay + t * dy - py)


def _refusal(what: str, distance: float, zone: Keepout) -> ValueError:
    return ValueError(
        f"{what} comes within {distance:.0f} mm of the robot base; the head must stay "
        f"{zone.radius_mm:g} mm clear of it, or the arm folds into itself."
    )


def check_point(x: float, y: float, what: str, zone: Keepout | None = None) -> None:
    """Refuse a point (offsets from zerozero) inside the zone."""
    zone = keepout_from_env() if zone is None else zone
    if zone is None:
        return
    distance = math.hypot(x - zone.x_mm, y - zone.y_mm)
    if distance < zone.radius_mm:
        raise _refusal(what, distance, zone)


def check_move(start: tuple[float, float], end: tuple[float, float], what: str,
               zone: Keepout | None = None) -> None:
    """Refuse a level move whose straight line passes through the zone.

    A move that is closest to the base where it starts only gets further away,
    so it passes even from inside the zone: a head jogged in too close can
    always be sent back out.
    """
    zone = keepout_from_env() if zone is None else zone
    if zone is None:
        return
    t, distance = _closest_on_segment(*start, *end, zone.x_mm, zone.y_mm)
    if distance < zone.radius_mm and t > 0:
        raise _refusal(what, distance, zone)


def check_studs(studs: Sequence[dict], zone: Keepout | None = None) -> None:
    """Refuse resolved studs (offsets from zerozero) that a run would take into the zone.

    Checks each stud, then each level move from one stud to the next in weld
    order, the way WeldFlex.lua travels. The legs to and from homewf are not
    checked: home is taught on the controller and the host doesn't know it.
    """
    zone = keepout_from_env() if zone is None else zone
    if zone is None:
        return
    points = [(float(s["x"]), float(s["y"])) for s in studs]
    for index, (x, y) in enumerate(points):
        check_point(x, y, f"Stud {index + 1}", zone)
    for index in range(1, len(points)):
        check_move(points[index - 1], points[index],
                   f"The move from stud {index} to stud {index + 1}", zone)
