# Weld Sub-Process (`weld.lua`) Technical Notes & Documentation

This document contains full technical notes, bring-up findings, communication specs, and rationale moved from inline header documentation in [`weld.lua`](file:///c:/Users/Gage/Desktop/WeldFlex/programs/weld.lua).

---

## 1. Process Overview & Sequence

`weld.lua` owns the welding sub-process for a single stud, called per stud by `WeldFlex.lua`.
The sequence moves through 6 distinct phases:

1. **SEARCH**: `FT_FindSurface` approaches at `SEARCH_SPEED_MMS = 5 mm/s` until light contact (`CONTACT_FORCE_N = 10 N`), then `DI1` must confirm stud-on-work continuity.
2. **PRESS**: `FT_Control` regulates target force (`PRESS_TARGET_LBF`, default 20 lbf / 88.96 N) while `FT_LinInsertion` drives in, holding pressure for `PRESS_HOLD_MS`. `FT_LinInsertion` ends on `PRESS_INSERT_THRESHOLD_LBF` — the target less a `PRESS_TOLERANCE_LBF = 0.5` low edge — while `FT_Control` keeps regulating at the full target through hold. `FTC_GAIN_P` is held at the conservative `0.0001` for all targets while the production press response is commissioned.
3. **WELD**: Re-checks `DI1` and `DI0` (capacitors charged), then pulses the weld trigger output for its pulse duration, only if `WELD_ARMED = 1`. Both are set by the caller — `WELD_TRIGGER_DO` (default `0`, range 0–15) and `WELD_TRIGGER_PULSE_MS` (default `250`, range 1–1000) — so the trigger is no longer hardwired to DO0.
4. **HOLD**: Remains at pressure for `POST_WELD_HOLD_MS` (500 ms) while weld solidifies.
5. **RETRACT**: Returns to the pre-search pose captured at the caller's current safe height, via `MoveCart` along the tool's own approach axis; falls back to a workpiece-Z lift to `Z_CLEARANCE` if that pose could not be read. Every departure from a stud goes through this path, faults included — a fault during press leaves the collet on the stud exactly like a good weld does. Lifting along workpiece Z instead dragged the collet sideways by however far the head sits out of square with the bed (~0.9 mm over a 25 mm retract at 2°), and the gun's length turns a light catch into a moment the sensor cannot take: 5 N·m over a ~0.25 m TCP offset is only ~20 N of side load, so the moment range is reached while Fz is still nowhere near its 200 N range. That is the resettable "force sensor range reached" seen on retract.
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

### Beacon Lines (Fallback)
After a fault, the program parks ~3s on a unique `WaitMs` line site (`1`, `4`, `5`, `9`, `10`, `11`) accessible via RPC `GetCurrentLine()`.

---

## 3. Input Contract & Globals

### Required Globals (Set by `WeldFlex.lua`):
- `weldX`, `weldY`: Stud X/Y offsets from `zerozero` point.
- `Z_CLEARANCE`: Safe Z clearance offset in work-object frame. The parent
  program derives it from `PART_Z + SAFE_Z`; `RETRACT_Z` remains recipe data
  but is not used by the current safe-plane force-motion path.
- `WELD_RUN`: Set to `1` to execute sequence. Controller upload check executes top-level Lua on upload; without `WELD_RUN = 1`, file is define-only.

### Optional Globals:
- `WELD_ARMED`: `1` fires the weld trigger output for real. Any other value (or unset) suppresses the weld pulse while search, press, hold, retract, and feeder advance still run.
- `WELD_TRIGGER_DO` / `WELD_TRIGGER_PULSE_MS`: Weld trigger output number and pulse duration. Out-of-range values fall back to `0` and `250` ms respectively.
- `WELD_SKIP_INTERLOCKS`: `1` bypasses the Atlas `DI1`/`DI0` checks. Set by `WeldFlex.lua` only for a Liberty dry or commissioning build. **A live arc with interlocks bypassed is refused** unless `WELD_LIBERTY_COMMISSIONING` is also `1`.
- `WELD_LIBERTY_COMMISSIONING`: `1` permits the interlock bypass above, and skips the Atlas pre-fire input re-check.
- `WELD_SKIP_FEED`: `1` suppresses the phase 6 feeder pulse.
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
