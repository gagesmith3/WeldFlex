# Weld Sub-Process (`weld.lua`) Technical Notes & Documentation

This document contains full technical notes, bring-up findings, communication specs, and rationale moved from inline header documentation in [`weld.lua`](file:///c:/Users/Gage/Desktop/WeldFlex/programs/weld.lua).

---

## 1. Process Overview & Sequence

`weld.lua` owns the welding sub-process for a single stud, called per stud by `WeldFlex.lua`.
The sequence moves through 6 distinct phases:

1. **SEARCH**: `FT_FindSurface` creeps in until light contact (`CONTACT_FORCE_N = 10 N`), then `DI1` must confirm stud-on-work continuity.
2. **PRESS**: `FT_Control` regulates target force (`PRESS_TARGET_LBF`, default 20 lbf / 88.96 N) while `FT_LinInsertion` drives in, holding pressure for `PRESS_HOLD_MS`.
3. **WELD**: Re-checks `DI1` and `DI0` (capacitors charged), then pulses `DO0` for `WELD_PULSE_MS` (250 ms) only if `WELD_ARMED = 1`.
4. **HOLD**: Remains at pressure for `POST_WELD_HOLD_MS` (500 ms) while weld solidifies.
5. **RETRACT**: Returns to safe `Z_CLEARANCE` set by caller.
6. **FEED**: Pulses `DO1` for `FEED_PULSE_MS` (1000 ms) to advance the next stud into the torch.

> [!IMPORTANT]
> Any phase unable to reach its required condition retracts to safe Z, ensures `DO0` is off, drops force overlays, and raises a Lua error (`error()`).

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
- `Z_CLEARANCE`: Safe Z clearance offset in work-object frame.
- `WELD_RUN`: Set to `1` to execute sequence. Controller upload check executes top-level Lua on upload; without `WELD_RUN = 1`, file is define-only.

### Optional Globals:
- `WELD_ARMED`: `1` fires `DO0` for real. Any other value (or unset) suppresses the weld pulse while search, press, hold, retract, and feeder advance still run.
- `WELD_FORCE_TEST`: `1` = force verification mode. Ignores `DI1` check failure (reports only), stretches hold to 5000 ms, ends after retract, forces `DO0` off.
- `WELD_PRESS_LBF`: Press target in lbf, overriding 20.0 lbf default (clamped up to `PRESS_TARGET_MAX_LBF = 25.0 lbf`).

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
