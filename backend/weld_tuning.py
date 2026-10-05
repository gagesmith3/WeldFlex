"""weld.lua's search and press settings, set from the Admin page while in beta.

Both speeds were hard-coded in weld.lua until 2026-10-02. Finding a working
combination meant an edit, a commit and a deploy per try, so the Admin page's
Weld Tuning panel now sets them for every run and Single Shot. The job manager
reads them when Run is pressed and records them with the run.

The press has two modes since 2026-10-05. A dry Single Shot ladder that day
showed FT_LinInsertion's own feed working against FT_Control: 0.15 / 0.25 /
0.35 mm/s took about 8.5 / 10 / 14 s, with the force hunting up and down as
the speed rose, and faster feeds stall short of force. "force" (the default)
gives the insertion no feed at all, so it only ends the press on force while
FT_Control alone moves the gun, as in FAIRINO's own insertion example. "feed"
is the press as it was before, at the saved press speed, kept as a fallback.
In both, the press gain is FT_Control's proportional gain, which sets how fast
the regulator closes on the target.

weld.lua keeps its own copies of the defaults and bounds below, and falls back to
its defaults if a caller publishes nothing usable. Nothing enforces the two
copies agree across the language boundary, so tests/test_lua_builder.py asserts it.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent / "weld_tuning.json"

# The 2026-10-02 trial values. Before that: search 5.0 with press 0.25, then 0.10.
# The press speed only reaches weld.lua in "feed" mode.
DEFAULT_SEARCH_SPEED_MMS = 7.5
DEFAULT_PRESS_SPEED_MMS = 0.15

# Each ceiling is the fastest that speed has run on hardware (2026-09-14, when
# search 10 with press 1.0, then 0.5, left the press stuck short both times).
# Anything faster is untried, not known to be safe.
SEARCH_SPEED_MIN_MMS = 0.5
SEARCH_SPEED_MAX_MMS = 10.0
PRESS_SPEED_MIN_MMS = 0.05
PRESS_SPEED_MAX_MMS = 1.0

# Label for each press mode, in the order the Admin page lists them.
PRESS_MODES = {"force": "Force only", "feed": "Feed (old press)"}
DEFAULT_PRESS_MODE = "force"

# FT_Control's proportional gain. 0.0001 since 2026-09-04 (0.005 before, dropped
# with no reason recorded). The ceiling is FAIRINO's suggested value; the floor
# is the gain in FAIRINO's own insertion example. Too much gain overshoots or
# oscillates, so raise it in small steps on dry shots.
DEFAULT_PRESS_GAIN = 0.0001
PRESS_GAIN_MIN = 0.00005
PRESS_GAIN_MAX = 0.001


def format_gain(value: float) -> str:
    """0.0001, not %g's 1e-04: for the Admin page and the generated Lua alike."""
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def _number(label: str, value: float | int | str, places: int) -> float:
    try:
        number = float(str(value).strip())
    except ValueError:
        raise ValueError(f"{label} must be a number, got {value!r}") from None
    return round(number, places) if math.isfinite(number) else number


def _check(label: str, value: float | int | str, lo: float, hi: float) -> float:
    # Three places is all lua_builder.format_number writes, so a run records
    # the speed it actually sent.
    number = _number(label, value, 3)
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(f"{label} must be {lo:g} to {hi:g} mm/s, got {number:g}")
    return number


def _check_gain(value: float | int | str) -> float:
    # Six places is all format_gain writes.
    number = _number("Press gain", value, 6)
    if not math.isfinite(number) or not PRESS_GAIN_MIN <= number <= PRESS_GAIN_MAX:
        raise ValueError(f"Press gain must be {format_gain(PRESS_GAIN_MIN)} to "
                         f"{format_gain(PRESS_GAIN_MAX)}, got {str(value).strip()}")
    return number


def _check_mode(value: str) -> str:
    mode = str(value).strip()
    if mode not in PRESS_MODES:
        raise ValueError(f"Press mode must be one of {', '.join(PRESS_MODES)}, got {value!r}")
    return mode


@dataclass(frozen=True)
class WeldTuning:
    """One set of weld.lua search/press settings. Out-of-range values are refused."""

    search_speed_mms: float = DEFAULT_SEARCH_SPEED_MMS
    press_speed_mms: float = DEFAULT_PRESS_SPEED_MMS
    press_mode: str = DEFAULT_PRESS_MODE
    press_gain: float = DEFAULT_PRESS_GAIN

    def __post_init__(self) -> None:
        object.__setattr__(self, "search_speed_mms", _check(
            "Search speed", self.search_speed_mms, SEARCH_SPEED_MIN_MMS, SEARCH_SPEED_MAX_MMS))
        object.__setattr__(self, "press_speed_mms", _check(
            "Press speed", self.press_speed_mms, PRESS_SPEED_MIN_MMS, PRESS_SPEED_MAX_MMS))
        object.__setattr__(self, "press_mode", _check_mode(self.press_mode))
        object.__setattr__(self, "press_gain", _check_gain(self.press_gain))

    @property
    def press_feed_mms(self) -> float:
        """The FT_LinInsertion speed weld.lua gets: 0 in "force" mode."""
        return self.press_speed_mms if self.press_mode == "feed" else 0.0

    def to_dict(self) -> dict[str, float | str]:
        return {
            "search_speed_mms": self.search_speed_mms,
            "press_speed_mms": self.press_speed_mms,
            "press_mode": self.press_mode,
            "press_gain": self.press_gain,
        }


def load(path: str | os.PathLike | None = None) -> WeldTuning:
    """The saved settings, or the defaults for any that are missing or unusable.

    A file saved before the press modes existed has no press_mode, so it loads
    as "force", the default."""
    path = Path(path or SETTINGS_PATH)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return WeldTuning()
    except (OSError, ValueError) as exc:
        log.warning("weld tuning unreadable, using defaults: %s", exc)
        return WeldTuning()
    if not isinstance(saved, dict):
        log.warning("weld tuning is not an object, using defaults: %r", saved)
        return WeldTuning()
    values = {}
    for name in ("search_speed_mms", "press_speed_mms", "press_mode", "press_gain"):
        if name not in saved:
            continue
        try:
            WeldTuning(**{name: saved[name]})
        except (TypeError, ValueError) as exc:
            log.warning("weld tuning %s ignored, using the default: %s", name, exc)
            continue
        values[name] = saved[name]
    return WeldTuning(**values)


def save(tuning: WeldTuning, path: str | os.PathLike | None = None) -> None:
    path = Path(path or SETTINGS_PATH)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(tuning.to_dict(), f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
