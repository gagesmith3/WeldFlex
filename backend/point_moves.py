"""Safe moves to a taught point, for the Points page.

Every move follows the owner's travel rule: straight up, level, or straight
down, never a joint-interpolated sweep. A move to a point is three `Lin` legs,
all offsets from the taught `zerozero` in the wobj-2 frame, the same way
WeldFlex.lua travels:

1. **Lift.** Straight up from where the head is now to the travel height,
   turning the head to zerozero's orientation (square to the bed) on the way.
2. **Travel.** Level at the travel height, to directly above the point.
3. **Descend.** Straight down into the point at a slower speed (skipped when
   the operator asks to stop above it).

The travel height is TRAVEL_HEIGHT_MM above zerozero, or higher when the head
or the point is already higher, so the move never drops toward the table to
travel.

Before any motion the program resets the controller's collision detection to
the normal level. weld.lua raises it for the press and a stop mid-press skips
the restore, so without this a point move could run with the guard effectively
off.

The host plans the move and does every check. The program it uploads is
straight-line code with no branches, because the controller's upload check
executes top-level Lua (a runtime `error()` there refuses the upload).

**Frame assumption, unverified on hardware:** `GetActualTCPPose` reports the
TCP in the active tool/work-object frame. The route refuses unless tool 2 and
wobj 2 are active, and the confirm modal shows the planned distances so a
wrong frame shows up as absurd numbers before anything moves.
"""

from __future__ import annotations

from dataclasses import dataclass

# 5 in above zerozero (the bed surface). Owner, 2026-09-24.
TRAVEL_HEIGHT_MM = 127.0
TRAVEL_SPEED_PCT = 10
DESCEND_SPEED_PCT = 5
# weld.lua's BASE_COLL_LEVEL: the level a run travels at. Standard mode, 1-10,
# lower is more sensitive. Detection needs the tool payload set correctly
# on the controller.
COLLISION_LEVEL = 3
MOVE_TOOL = 2
MOVE_WOBJ = 2
# Below this, relative to zerozero, the head would be under the bed surface.
# A reading that low means a frame mismatch, not a real pose.
MIN_Z_REL_MM = -20.0
# Shorter legs are skipped, since a zero-length Lin gains nothing.
MIN_LEG_MM = 0.5
MIN_TURN_DEG = 0.5

MODES = ("to", "above")


class PointMoveError(ValueError):
    """The move can't be planned safely; the message is for the operator."""


@dataclass(frozen=True)
class Plan:
    point: str
    mode: str
    # All relative to zerozero, in the wobj-2 frame (mm).
    start: tuple[float, float, float]
    travel_z: float
    target: tuple[float, float, float]
    lift_mm: float
    travel_mm: float
    descend_mm: float
    do_lift: bool
    do_travel: bool
    do_descend: bool


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def plan_move(point: str, mode: str, current_pose, zerozero_pose, point_pose) -> Plan:
    """Plan a move from `current_pose` to the taught point.

    All three poses are [x, y, z, rx, ry, rz] in the wobj-2 frame.
    `zerozero_pose` is the reference every leg is offset from.
    """
    if mode not in MODES:
        raise PointMoveError(f"Unknown move mode {mode!r}.")
    zx, zy, zz = (float(v) for v in zerozero_pose[:3])
    cur = [float(v) for v in current_pose[:6]]
    cx, cy, cz = cur[0] - zx, cur[1] - zy, cur[2] - zz
    tx, ty, tz = (float(point_pose[i]) - (zx, zy, zz)[i] for i in range(3))

    if cz < MIN_Z_REL_MM:
        raise PointMoveError(
            f"The head reads {cz:.0f} mm below zerozero, which can't be right. "
            "Check that tool 2 / wobj 2 are active and zerozero is taught."
        )
    if tz < MIN_Z_REL_MM:
        raise PointMoveError(
            f"{point} reads {tz:.0f} mm below zerozero. Check how it was taught."
        )

    travel_z = max(TRAVEL_HEIGHT_MM, cz, tz)
    lift_mm = travel_z - cz
    squared = all(
        _angle_diff(cur[3 + i], float(zerozero_pose[3 + i])) < MIN_TURN_DEG for i in range(3)
    )
    travel_mm = ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5
    descend_mm = travel_z - tz if mode == "to" else 0.0

    return Plan(
        point=point, mode=mode,
        start=(cx, cy, cz), travel_z=travel_z, target=(tx, ty, tz),
        lift_mm=lift_mm, travel_mm=travel_mm, descend_mm=descend_mm,
        do_lift=lift_mm >= MIN_LEG_MM or not squared,
        do_travel=travel_mm >= MIN_LEG_MM,
        # Always run the last Lin for "to": even with no drop it sets the
        # point's own orientation, which may differ from zerozero's.
        do_descend=mode == "to",
    )


def _offset_lin(x: float, y: float, z: float, speed: int) -> str:
    return (
        f"PointsOffsetEnable(0, {x:.3f}, {y:.3f}, {z:.3f}, 0, 0, 0)\n"
        f"Lin(zerozero, {speed}, -1, 0, 0)\n"
        "PointsOffsetDisable()\n"
    )


def build_program(plan: Plan) -> str:
    """The controller program for `plan`. Point names are trusted (from POINTS)."""
    level = ", ".join([str(COLLISION_LEVEL)] * 6)
    lines = [
        f"-- Points page: move to {plan.point} ({plan.mode})\n",
        f"tool = {MOVE_TOOL}\n",
        "blend = -1\n",
        f"wobj = {MOVE_WOBJ}\n",
        # Clear any press-time thresholds a stopped weld left behind, then
        # travel at the normal collision level.
        "CustomCollisionDetectionEnd()\n",
        f"SetAnticollision(0, {{{level}}}, 0)\n",
    ]
    cx, cy, _ = plan.start
    tx, ty, _ = plan.target
    if plan.do_lift:
        lines.append("-- Straight up, squaring the head\n")
        lines.append(_offset_lin(cx, cy, plan.travel_z, TRAVEL_SPEED_PCT))
    if plan.do_travel:
        lines.append("-- Level travel above the point\n")
        lines.append(_offset_lin(tx, ty, plan.travel_z, TRAVEL_SPEED_PCT))
    if plan.do_descend:
        lines.append("-- Straight down into the point\n")
        lines.append(f"Lin({plan.point}, {DESCEND_SPEED_PCT}, -1, 0, 0)\n")
    return "".join(lines)
