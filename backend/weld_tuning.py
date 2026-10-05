"""weld.lua's search speed and press gain, set from the Admin page while in beta.

Both were hard-coded in weld.lua until 2026-10-02. Finding a working value
meant an edit, a commit and a deploy per try, so the Admin page's Weld Tuning
panel now sets them for every run and Single Shot. The job manager reads them
when Run is pressed and records them with the run.

The press gain is FT_Control's proportional gain, and it alone sets how fast
the press closes. Until 2026-10-05 FT_LinInsertion also fed the gun at a press
speed set here, but a dry ladder that day showed that feed working against
FT_Control (0.15 / 0.25 / 0.35 mm/s took about 8.5 / 10 / 14 s, and faster
feeds stall short). weld.lua now gives the insertion no feed, as in FAIRINO's
own insertion example. The press speed and the "Feed" press mode that kept the
old press are archived at the archive/press-feed-mode tag.

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

# The 2026-10-02 trial value. Before that: 5.0.
DEFAULT_SEARCH_SPEED_MMS = 7.5

# The ceiling is the fastest the search has run on hardware (2026-09-14).
# Anything faster is untried, not known to be safe.
SEARCH_SPEED_MIN_MMS = 0.5
SEARCH_SPEED_MAX_MMS = 10.0

# FT_Control's proportional gain. 0.0003 was commissioned on 2026-10-05: a
# smooth climb to 16 lbf, live and dry, where 0.0001 (the gain since
# 2026-09-04) took ~6.5 s touch to lift. 0.005 before 2026-09-04, dropped with
# no reason recorded. The ceiling is FAIRINO's suggested value; the floor is
# the gain in FAIRINO's own insertion example. Too much gain overshoots or
# oscillates, so raise it in small steps on dry shots.
DEFAULT_PRESS_GAIN = 0.0003
PRESS_GAIN_MIN = 0.00005
PRESS_GAIN_MAX = 0.001


def format_gain(value: float) -> str:
    """0.0003, not %g's 3e-04: for the Admin page and the generated Lua alike."""
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def _number(label: str, value: float | int | str, places: int) -> float:
    try:
        number = float(str(value).strip())
    except ValueError:
        raise ValueError(f"{label} must be a number, got {value!r}") from None
    return round(number, places) if math.isfinite(number) else number


def _check_search(value: float | int | str) -> float:
    # Three places is all lua_builder.format_number writes, so a run records
    # the speed it actually sent.
    number = _number("Search speed", value, 3)
    if not math.isfinite(number) or not SEARCH_SPEED_MIN_MMS <= number <= SEARCH_SPEED_MAX_MMS:
        raise ValueError(f"Search speed must be {SEARCH_SPEED_MIN_MMS:g} to "
                         f"{SEARCH_SPEED_MAX_MMS:g} mm/s, got {number:g}")
    return number


def _check_gain(value: float | int | str) -> float:
    # Six places is all format_gain writes.
    number = _number("Press gain", value, 6)
    if not math.isfinite(number) or not PRESS_GAIN_MIN <= number <= PRESS_GAIN_MAX:
        raise ValueError(f"Press gain must be {format_gain(PRESS_GAIN_MIN)} to "
                         f"{format_gain(PRESS_GAIN_MAX)}, got {str(value).strip()}")
    return number


@dataclass(frozen=True)
class WeldTuning:
    """One weld.lua search speed (mm/s) and press gain. Out-of-range values are refused."""

    search_speed_mms: float = DEFAULT_SEARCH_SPEED_MMS
    press_gain: float = DEFAULT_PRESS_GAIN

    def __post_init__(self) -> None:
        object.__setattr__(self, "search_speed_mms", _check_search(self.search_speed_mms))
        object.__setattr__(self, "press_gain", _check_gain(self.press_gain))

    def to_dict(self) -> dict[str, float]:
        return {"search_speed_mms": self.search_speed_mms, "press_gain": self.press_gain}


def load(path: str | os.PathLike | None = None) -> WeldTuning:
    """The saved settings, or the defaults for any that are missing or unusable.

    Keys this version no longer uses (press_speed_mms, press_mode) are ignored,
    and dropped the next time the panel saves."""
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
    for name in ("search_speed_mms", "press_gain"):
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
