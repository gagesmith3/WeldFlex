"""weld.lua's search and press speeds, set from the Admin page while in beta.

Both speeds were hard-coded in weld.lua until 2026-10-02. Finding a working
combination meant an edit, a commit and a deploy per try, so the Admin page's
Weld Tuning panel now sets them for every run and Single Shot. The job manager
reads them when Run is pressed and records them with the run.

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
DEFAULT_SEARCH_SPEED_MMS = 7.5
DEFAULT_PRESS_SPEED_MMS = 0.15

# Each ceiling is the fastest that speed has run on hardware (2026-09-14, when
# search 10 with press 1.0, then 0.5, left the press stuck short both times).
# Anything faster is untried, not known to be safe.
SEARCH_SPEED_MIN_MMS = 0.5
SEARCH_SPEED_MAX_MMS = 10.0
PRESS_SPEED_MIN_MMS = 0.05
PRESS_SPEED_MAX_MMS = 1.0


def _check(label: str, value: float | int | str, lo: float, hi: float) -> float:
    try:
        number = float(str(value).strip())
    except ValueError:
        raise ValueError(f"{label} must be a number, got {value!r}") from None
    # Three places is all lua_builder.format_number writes, so a run records
    # the speed it actually sent.
    number = round(number, 3) if math.isfinite(number) else number
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(f"{label} must be {lo:g} to {hi:g} mm/s, got {number:g}")
    return number


@dataclass(frozen=True)
class WeldTuning:
    """One search/press speed pair, in mm/s. Out-of-range values are refused."""

    search_speed_mms: float = DEFAULT_SEARCH_SPEED_MMS
    press_speed_mms: float = DEFAULT_PRESS_SPEED_MMS

    def __post_init__(self) -> None:
        object.__setattr__(self, "search_speed_mms", _check(
            "Search speed", self.search_speed_mms, SEARCH_SPEED_MIN_MMS, SEARCH_SPEED_MAX_MMS))
        object.__setattr__(self, "press_speed_mms", _check(
            "Press speed", self.press_speed_mms, PRESS_SPEED_MIN_MMS, PRESS_SPEED_MAX_MMS))

    @classmethod
    def of(cls, search_speed_mms: float | int | str | None = None,
           press_speed_mms: float | int | str | None = None) -> "WeldTuning":
        """Like the constructor, but None also means the default."""
        values = {}
        if search_speed_mms is not None:
            values["search_speed_mms"] = search_speed_mms
        if press_speed_mms is not None:
            values["press_speed_mms"] = press_speed_mms
        return cls(**values)

    def to_dict(self) -> dict[str, float]:
        return {"search_speed_mms": self.search_speed_mms, "press_speed_mms": self.press_speed_mms}


def load(path: str | os.PathLike | None = None) -> WeldTuning:
    """The saved speeds, or the defaults for any that are missing or unusable."""
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
    for name in ("search_speed_mms", "press_speed_mms"):
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
