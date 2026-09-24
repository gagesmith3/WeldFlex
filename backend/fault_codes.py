"""FAIRINO motion-controller fault text, keyed by the main/sub code pair.

The controller reports a fault as two integers (`main_errcode`/`sub_errcode` on
the 8083 feed, `GetRobotErrorCode()` over XML-RPC). The pendant and web UI turn
that pair into a sentence like "Axis 3 collision fault"; this is the same table,
so the kiosk can say the same thing.

Source: FAIRINO Collaborative Robot User Manual V3.9.8, Appendix, "Motion
controller errors" (manual.fairino.support/latest/CobotsManual/appendix.html),
transcribed 2026-09-24. Firmware can report pairs the manual does not list, so
`describe()` always falls back to the main-code category and the raw numbers
rather than returning nothing.

Separate from `frame_8083.ERROR_CODES`, which is the feed's coarse 0-12
`error_code` byte and uses a different numbering.
"""

from __future__ import annotations

from dataclasses import dataclass

_AXES = range(1, 7)

# main code -> (category, resettable by ResetAllError)
CATEGORIES: dict[int, tuple[str, bool]] = {
    1: ("Command point error", True),
    2: ("Drive fault", False),
    3: ("Soft limit exceeded", True),
    4: ("Collision fault", True),
    5: ("Active slave count error", False),
    6: ("Slave error", False),
    7: ("IO error", True),
    8: ("Gripper error", True),
    9: ("File error", False),
    10: ("Singular pose", True),
    11: ("Drive communication error", False),
    12: ("External axis soft limit", True),
    13: ("Parameter setting error", True),
}

# (main, sub) -> description. Rows whose resettability differs from their
# category's are listed in `_NOT_RESETTABLE`.
_DESCRIPTIONS: dict[tuple[int, int], str] = {
    (1, 1): "Joint command point error",
    (1, 2): "Linear target point error (including tool mismatch)",
    (1, 3): "Arc intermediate point error (including tool mismatch)",
    (1, 4): "Arc target point error (including tool mismatch)",
    (1, 5): "Arc command points too close",
    (1, 6): "Full circle/helix intermediate point 1 error",
    (1, 7): "Full circle/helix intermediate point 2 error",
    (1, 8): "Full circle/helix intermediate point 3 error",
    (1, 9): "Full circle/helix command points too close",
    (1, 10): "TPD command point error",
    (1, 11): "TPD command tool does not match current tool",
    (1, 12): "TPD start point deviation too large",
    (1, 13): "Internal/external tool switching error",
    (1, 14): "New helix start point error",
    (1, 15): "New spline command point error",
    (1, 17): "PTP joint command limit exceeded",
    (1, 18): "TPD joint command limit exceeded",
    (1, 19): "LIN/ARC joint command limit exceeded",
    (1, 20): "Cartesian space command overspeed",
    (1, 21): "Joint space torque command limit exceeded",
    (1, 22): "JOG joint command limit exceeded",
    **{(1, 22 + a): f"Axis {a} speed limit exceeded" for a in _AXES},
    (1, 29): "Joint feedback speed limit exceeded",
    (1, 30): "Joint command/feedback deviation too large",
    (1, 31): "DMP target point error (including tool mismatch)",
    (1, 33): "Next command joint configuration changed",
    (1, 34): "Current command joint configuration changed",
    (1, 35): "LIN command joint speed limit exceeded",
    (1, 36): "LIN command adaptive speed exceeds threshold",
    (1, 37): "Unreachable point in trajectory",
    (1, 38): "Unreachable point — singular pose",
    (1, 49): "Invalid command between ARCSTART/ARCEND",
    (1, 50): "Invalid command between WEAVESTART/WEAVEEND",
    (1, 51): "Weaving parameter error",
    (1, 52): "Weaving command points too close",
    (1, 53): "Weaving trajectory — singular pose",
    (1, 54): "Weaving trajectory — joint limit exceeded",
    (1, 55): "Weaving planning anomaly (tool/direction)",
    (1, 56): "Weaving planning anomaly (arc waypoint)",
    (1, 65): "Laser sensor command deviation too large",
    (1, 66): "Laser sensor command interrupted",
    (1, 81): "External axis command speed limit exceeded",
    (1, 82): "External axis deviation too large",
    (1, 83): "Extended peripheral communication error",
    (1, 84): "Extended peripheral packet loss error",
    (1, 97): "Conveyor tracking — pose change too large",
    (1, 113): "Constant force control — X exceeds max adjustment distance",
    (1, 114): "Constant force control — Y exceeds max adjustment distance",
    (1, 115): "Constant force control — Z exceeds max adjustment distance",
    (1, 116): "Constant force control — RX exceeds max adjustment angle",
    (1, 117): "Constant force control — RY exceeds max adjustment angle",
    (1, 118): "Constant force control — RZ exceeds max adjustment angle",
    (1, 119): "External sensor data error",
    (1, 120): "Helix exploration motion failed",
    (1, 121): "Rotational insertion motion failed",
    (1, 122): "Linear insertion motion failed",
    (1, 123): "Surface positioning motion failed",
    (1, 129): "Exceeded max torque record points",
    (1, 130): "Speed switching error",
    (1, 147): "Focus following error",
    (1, 148): "Attitude speed limit exceeded",
    (1, 149): "Joint status word feedback abnormal",
    **{(2, a): f"Axis {a} drive fault" for a in _AXES},
    **{(3, a): f"Axis {a} soft limit exceeded" for a in _AXES},
    **{(4, a): f"Axis {a} collision fault" for a in _AXES},
    (4, 7): "End effector collision fault",
    (5, 1): "Active slave count error",
    (6, 1): "Slave offline",
    (6, 2): "Slave status does not match set value",
    (6, 3): "Slave not configured",
    (6, 4): "Slave configuration error",
    (6, 5): "Slave initialization error",
    (6, 6): "Slave mailbox communication initialization error",
    (7, 1): "IO channel error",
    (7, 2): "IO value error",
    (7, 3): "WaitDI timeout",
    (7, 4): "WaitAI timeout",
    (7, 5): "WaitAxleDI timeout",
    (7, 6): "WaitAxleAI timeout",
    (7, 7): "IO channel configured function error",
    (7, 8): "Arc start timeout",
    (7, 9): "Arc end timeout",
    (7, 10): "Search positioning timeout",
    (7, 11): "Conveyor IO detection timeout",
    (7, 12): "WaitAuxDI timeout",
    (7, 13): "WaitAuxAI timeout",
    (7, 14): "Wire search positioning timeout",
    (8, 1): "Gripper motion timeout",
    (9, 1): "zbt configuration file version error",
    (9, 2): "zbt configuration file failed to load",
    (9, 3): "User configuration file version error",
    (9, 4): "User configuration file failed to load",
    (9, 5): "External axis configuration file version error",
    (9, 6): "External axis configuration file failed to load",
    (9, 7): "Robot model inconsistent — requires reconfiguration",
    (9, 8): "dhpara configuration file version error",
    (9, 9): "dhpara configuration file failed to load",
    (9, 10): "Robot model not set",
    (9, 11): "Load configuration file version error",
    (9, 12): "Load configuration file failed to load",
    (9, 13): "Speed configuration file version error",
    (9, 14): "Speed configuration file failed to load",
    (10, 1): "Singular pose",
    **{(11, a): f"Axis {a} drive communication error" for a in _AXES},
    **{(12, a): f"External axis {a} soft limit exceeded" for a in range(1, 5)},
    (13, 1): "Tool number out of range",
    (13, 2): "Positioning completion threshold error",
    (13, 3): "Collision level error",
    (13, 4): "Load weight error",
    (13, 5): "Load center of mass X error",
    (13, 6): "Load center of mass Y error",
    (13, 7): "Load center of mass Z error",
    (13, 8): "DI filter time error",
    (13, 9): "AxleDI filter time error",
    (13, 10): "AI filter time error",
    (13, 11): "AxleAI filter time error",
    (13, 12): "DI high/low level range error",
    (13, 13): "DO high/low level range error",
    (13, 14): "Workpiece number out of range",
    (13, 15): "External axis number out of range",
    (13, 16): "Conveyor encoder channel error",
    (13, 17): "Conveyor workpiece axis number error",
}

# Main code 1 is resettable as a category, except these.
_NOT_RESETTABLE = {(1, 20), (1, 29), (1, 30), (1, 82)}


@dataclass(frozen=True)
class FaultText:
    category: str | None     # None when the main code itself is unknown
    description: str         # always something printable
    resettable: bool | None  # None when the manual does not say
    documented: bool         # False when the pair is not in the manual's table


def describe(main: int | None, sub: int | None) -> FaultText | None:
    """Text for a main/sub fault pair, or None when there is no fault."""
    if not main:
        return None
    sub = sub or 0
    known = CATEGORIES.get(main)
    text = _DESCRIPTIONS.get((main, sub))
    if text is not None:
        resettable = False if (main, sub) in _NOT_RESETTABLE else known[1]
        return FaultText(known[0], text, resettable, True)
    if known is not None:
        return FaultText(known[0], f"{known[0]} (sub-code {sub})", known[1], False)
    return FaultText(None, f"Undocumented fault {main}/{sub}", None, False)
