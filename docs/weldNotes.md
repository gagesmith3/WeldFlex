# Weld Sub-Process (`weld.lua`) Technical Notes & Documentation

This document contains full technical notes, bring-up findings, communication specs, and rationale moved from inline header documentation in [`weld.lua`](file:///c:/Users/Gage/Desktop/WeldFlex/programs/weld.lua).

---

## 1. Process Overview & Sequence

`weld.lua` owns the welding sub-process for a single stud, called per stud by `WeldFlex.lua`.
The sequence moves through 6 distinct phases:

1. **SEARCH**: `FT_FindSurface` approaches at `SEARCH_SPEED_MMS = 5 mm/s` until light contact (`CONTACT_FORCE_N = 10 N`), then `DI1` must confirm stud-on-work continuity unless the recipe's DI check is off.
2. **PRESS**: `FT_Control` regulates target force (`PRESS_TARGET_LBF`, default 20 lbf / 88.96 N) while `FT_LinInsertion` drives in at `PRESS_SPEED_MMS = 0.25 mm/s`. Faster was tried on 2026-09-14 (search at 10 mm/s with the press at 1.0, then 0.5) and both times the press stuck well short of force, so both speeds were restored even though a shot takes 30-45 s. A lower Safe Z shortens the search without touching the press. `FT_LinInsertion` ends on the first reading past `PRESS_INSERT_THRESHOLD_LBF` — the target less a `PRESS_TOLERANCE_LBF = 0.5` low edge — and seating the stud can spike the reading past it for an instant before the force sags (live 2026-09-14: that press barely touched, while a jog held at a steady 19 lbf welds right). The hold is a passive `PRESS_HOLD_MS = 1000` wait with `FT_Control` still regulating at the full target, and later shots that day held pressure accurately on it. Do not re-run `FT_LinInsertion` through the hold to push sagged force back: Lua cannot read force, so a re-run cannot know whether it starts past its threshold, and the one dry run that tried it (four re-runs, 2026-09-14) faulted with a resettable "Cartesian space command speed exceeded limit". `FTC_GAIN_P` is held at the conservative `0.0001` for all targets while the production press response is commissioned. As of 2026-09-21, `weld.lua` samples tool Z again once the hold ends and publishes the delta as `s_var_9` (`SV_PRESS_HOLD_TRAVEL`) — further travel during the hold is `FT_Control` still closing a spike-induced undershoot on its own; a near-zero reading is ambiguous between "already at target" and "gain too slow," and telling those apart still needs a live force reading. This is diagnostic only — nothing in `weld.lua` or the host acts on it.
3. **WELD**: Only if `WELD_ARMED = 1`: re-checks `DI1` and `DI0` (capacitors charged) unless the recipe's DI check is off, then pulses the weld trigger, `DO0`, for 250 ms. The trigger output and pulse length are fixed constants in `weld.lua` (`DO_WELD`, `WELD_PULSE_MS`), not caller inputs. **`FT_Control` is never turned off for this phase or the next** — nothing disengages it between `pressToForce()` starting it and `retract()` calling `forceControlOff()`. The stud tip flashes off in milliseconds once the arc strikes, and the F/T sensor's RS-485 link sits next to that current pulse, so a real, sudden force-loss step and an EMI-glitched sample look identical to the still-active regulator — either one hands it a force error to react to, which can appear at the robot as a jolt at the moment of firing (reported 2026-09-21). As of 2026-09-21, `weld.lua` samples tool Z immediately before the pulse and again at the end of the post-weld hold, publishing the delta as `s_var_10` (`SV_WELD_JOLT_TRAVEL`) — diagnostic only, spanning both this phase and the next; nothing acts on it yet, and disengaging `FT_Control` before firing was considered but deferred pending what this reading shows on a live shot.
4. **HOLD**: Remains at pressure for `POST_WELD_HOLD_MS` (500 ms) while weld solidifies.
5. **RETRACT**: A `Lin` back to the pose the caller parked at before the search: `zerozero` offset by (`weldX`, `weldY`, `Z_CLEARANCE`) in the workpiece frame. Search and press drove straight down tool Z from that pose without turning the tool, so the straight line back retraces the descent exactly, however far the head sits out of square with the bed. Every departure from a stud goes through this path, faults included — a fault during press leaves the collet on the stud exactly like a good weld does. `PTP` and `MoveCart` reach the same endpoint but interpolate in joint space, which bows the path off that line while the collet is still on the stud. **Open: the resettable "Force sensor range threshold reached" fault at retract is not a lift-path problem.** It was blamed first on the workpiece-Z `PTP` lift (2026-09-08), then on joint-space bowing, but it kept tripping after the `MoveCart` change and again with this `Lin` (live Single Shots, 21 lbf, M4 on aluminum, 2026-09-15). Two facts would narrow it down: whether it ever trips on a dry run (no weld current and no stud welded into the chuck), and whether the weld phase at the fault is `40` (weld, hold, or the force-control release, before any lift) or `50` (during the lift).
6. **FEED**: Pulses `DO1` for `WELD_FEED_PULSE_MS` (250 ms by default) to
  trigger the next stud. The pulse ends before the outer program travels;
  mechanical reload continues during the following stud-to-stud move.

> [!IMPORTANT]
> Any phase unable to reach its required condition departs the stud (phase 5), ensures the weld trigger output is off, drops force overlays, sets `WELD_FAULT`, and returns control to the parent program.

---

## 2. Host Communication & Telemetry

`print()` and `error()` text do not leave the pendant console. Telemetry is published two ways for host visibility over XML-RPC:

### System Variables (Primary)
Written via `pub(slot, value)` using `SetSysVarvalue` / `SetSysVarValue`:
- **`s_var_1` (Packed Phase & Inputs)**: Encodes phase + DI states: `phase + (100 * DI1) + (1000 * DI0)`. (Digit 9 represents un-sampled, 1=active, 0=inactive).
  - Phase Codes:
    - `10`: `PH_ENTER`
    - `20`: `PH_SEARCH`
    - `21`: `PH_SEARCH_DONE`
    - `30`: `PH_PRESS_ON`
    - `31`: `PH_PRESS_INSERT`
    - `32`: `PH_PRESS_HOLD`
    - `33`: `PH_PRESS_HELD`
    - `40`: `PH_WELD`
    - `50`: `PH_RETRACT`
    - `60`: `PH_DONE`
    - `90 + beacon_site`: Fault codes (`91`..`911`)
- **`s_var_2` (`SV_LAST_RET`)**: Raw return value of the last `FT_*` call (`-999` for nil, `-998` for non-numeric).
- **`s_var_3` (`SV_PRESS_Z0`)**: Base-frame tool Z at contact (mm), published *before* press to allow host travel calculation even on aborted press.
- **`s_var_4` (`SV_PRESS_TRAVEL`)**: Insertion travel achieved (`pressZ0 - zNow`), published only when insertion completes.
- **`s_var_5` (`SV_PRESS_GUARD`)**: Active collision guard state:
  - `0`: `GUARD_RELEASED`
  - `1`: `GUARD_CUSTOM` (`CustomCollisionDetectionStart`)
  - `2`: `GUARD_BOTH` (custom + `SetAnticollision`)
  - `3`: `GUARD_LEVEL` (`SetAnticollision` only)
  - `4`: `GUARD_NOT_NEEDED` (press force < 40 N)
  - `9`: `GUARD_NONE` (neither instruction available)
- **`s_var_6` (`SV_STUD_ON_WORK`)**: Recent `DI1` level.
- **`s_var_7` (`SV_WELD_READY`)**: Recent `DI0` level.
- **`s_var_8` (`SV_PRESS_LBF`)**: Applied press force target in lbf.
- **`s_var_9` (`SV_PRESS_HOLD_TRAVEL`)**: Tool Z travel (mm) during `PRESS_HOLD_MS`,
  measured after the hold ends — zero at the top of each stud. Lua still has no
  force-read instruction, so this is a proxy: further travel during the hold
  means `FT_Control` kept driving in after `FT_LinInsertion` stopped (evidence
  it is closing a spike-induced undershoot); a reading near zero could mean
  either "already at target" or "gain too slow to close a real gap in
  `holdMs`" — those look identical without a live force reading.
- **`s_var_10` (`SV_WELD_JOLT_TRAVEL`)**: Tool Z travel (mm) from immediately
  before the arc pulse to the end of `POST_WELD_HOLD_MS`, published at the end
  of that hold — zero at the top of each stud, and never set if the stud never
  reached the WELD phase. `FT_Control` is still regulating force across this
  entire span (see phase 3 above), so this shows whether — and how far — the
  tool actually moved while the arc fired. A dry run never fires the arc, so
  its number is the sensor's own noise floor for comparison against a live
  shot. Diagnostic only.

### Beacon Lines (Fallback)
After a fault, the program parks ~3s on a unique `WaitMs` line site (`1`, `4`, `5`, `9`, `10`, `11`) accessible via RPC `GetCurrentLine()`.

---

## 3. Input Contract & Globals

### Required Globals (Set by `WeldFlex.lua` and `single_shot.lua`):
- `weldX`, `weldY`: Stud X/Y offsets from `zerozero` point. `lua_builder` has
  already resolved them from the part's origin corner (`backend/part_origin.py`),
  so for any corner but front-left they are not the numbers the part stores.
- `Z_CLEARANCE`: Safe Z clearance offset in work-object frame. The parent
  program derives it from `PART_Z + SAFE_Z`; `RETRACT_Z` remains recipe data
  but is not used by the current safe-plane force-motion path.
- `WELD_RUN`: Set to `1` to execute sequence. Controller upload check executes top-level Lua on upload; without `WELD_RUN = 1`, file is define-only.

### Run Mode Globals (resolved once by `lua_builder.RunMode`):
Both callers publish these once, above their cycle loop, through the `--{{RUN_MODE}}` marker. Neither derives or changes them in Lua.
- `WELD_ARMED`: `1` fires the weld trigger output for real. Any other value (or unset) suppresses the weld pulse while search, press, hold, retract, and feeder advance still run. Comes from the Live/Dry choice made for every run.
- `WELD_DI_CHECK`: The recipe's DI check. `0` skips the `DI0` welder-ready wait, the `DI1` stud-on-work check after search, and the pre-fire re-check of both, **on live runs too** (owner decision, 2026-09-14). Any other value, or unset, keeps all three checks.

### Optional Globals:
- `WELD_PRESS_LBF`: Press target in lbf, overriding 20.0 lbf default (clamped up to `PRESS_TARGET_MAX_LBF = 22.0 lbf` / 97.9 N), keeping `FT_LinInsertion` below its documented 100 N threshold limit.
- `WELD_FEED_PULSE_MS`: Feeder trigger duration in ms, provided by the
  generated parent program. Values outside 1-10000 ms use the 250 ms default.

## 3.1 Dynamic Stud-to-Stud Speed Compensation

When a recipe enables Dynamic Speed Compensation, `WeldFlex.lua` emits a speed
and optional dwell for every destination after the first stud. The feeder's
reload timer begins with the prior `DO1` pulse. After the electrical pulse ends,
the next horizontal move and any generated dwell consume the rest of the
recipe's reload time.

DSC applies only to the post-weld horizontal move at safe height. Home,
first-stud approach, force-guided descent, and return-to-home continue using
the recipe's normal motion speed. It is disabled by default and refuses to build until
`WELDFLEX_DSC_CALIBRATED=1` confirms a dry-run calibration for the controller's
actual `Lin` timing model. The machine-level calibration values live in `.env`;
restart the backend after changing them.

### Commissioning Preconditions

The FAIRINO pendant's **Auto Speed** is a global cap on the program's requested
motion percentage. It must be set to **100%** before timing DSC, running a DSC
part, or accepting a calibration. A pendant Auto Speed of 25% made a generated
100% long stud-to-stud move run at roughly a quarter of its expected speed during
the 2026-09-02 `allentown_mini` validation; the DSC model cannot compensate past
its own 100% ceiling.

Use percentage-mode linear moves with no inline offset while a
`PointsOffsetEnable(0, ...)` global workpiece offset is active:

```lua
PointsOffsetEnable(0, weldX, weldY, travelZ, 0, 0, 0)
Lin(zerozero, travelSpeed, -1, 0, 0)
PointsOffsetDisable()
```

The fifth `Lin` argument is the instruction's inline `offset_flag`, not a
speed-mode selector. Passing `1` there enables a second workpiece/base offset;
it is incorrect when the offset is already supplied by `PointsOffsetEnable`.
The 2026-09-02 hardware check showed the corrected `0` form tracks the expected
travel behavior more closely. Physical-speed `Lin` mode is a separate,
uncommissioned interface and must not reuse DSC's percentage values without a
new timing calibration.

---

## 4. Force Ladder Concept

Walking the press force up incrementally (e.g. 5 lbf -> 10 lbf -> 15 lbf -> 20 lbf) distinguishes between:
1. **Regulated Press**: Motor/regulator operates correctly, stopping insertion when force is reached (travel scales with load, e.g. ~1.5 lbf/mm chuck spring).
2. **Blind Regulator**: Regulator fails to read sensor; `FT_LinInsertion` drives blind into chuck spring until hitting max displacement budget or joint collision limit.

---

## 5. FR Lua Manual Findings & Firmware Behavior

- **`FT_FindSurface` Return Code**: Documentation claims null return, but live tests (2026-07-29) showed return code `1` on successful contact. `ftRefused()` treats `< 0` or `>= 3` as actual errors.
- **No Force Reading in Lua**: `FT_GetForceTorqueRCS` is Python SDK-only and unavailable in controller Lua. Press must use blocking `FT_Control` + `FT_LinInsertion` composite.
- **Direction Encodings**:
  - `FT_FindSurface` `dir`: `1` = positive, `2` = negative.
  - `FT_LinInsertion` `linorn`: `0` = negative, `1` = positive.
- **`pcall` Banned**: The controller's post-upload validator refuses scripts containing `pcall`, `xpcall`, or `assert`. Defensive coding must rely on explicit `type()` checks.
- **Collision Scale**: Standard mode 1~100% maps to 0~100 N. At 20 lbf (88.96 N), normal press reaction torque approaches 89% of full scale, causing false joint-3 collision trips if collision guard is not adjusted.

---

## 6. Collision Guard Strategy

When target press force exceeds `PRESS_GUARD_MIN_N` (40 N / ~9 lbf):
1. `CustomCollisionDetectionStart` raises joint/TCP collision thresholds (`PRESS_COLL_JOINT = 500`, `PRESS_COLL_TCP = 1000`).
2. `SetAnticollision` sets mode 0 with collision off level (`10`) or mode 1 (100%).
3. On completion or fault, `forceControlOff()` reverts collision detection via `CustomCollisionDetectionEnd` and resets `SetAnticollision` to baseline (`BASE_COLL_LEVEL = 3`).

---

## 7. Preconditions

- **F/T Sensor**: Active and zeroed prior to execution (done via Calibration page).
- **Stud Loaded**: Stud must be loaded in torch prior to cycle start (weld-then-feed pattern).
- **Welder Integration**: Welder powered on, `DI0` ready line high, work return clamped (`DI1`).
