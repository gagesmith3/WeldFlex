-- =========================================
-- weld.lua — the weld sub-process, one stud.
--
-- Called once per stud by WeldFlex.lua, with the torch already parked over the
-- stud location at the safe Z clearance. This file owns everything from there:
--
--   1. SEARCH   FT_FindSurface creeps in until light contact, then DI1 must
--               confirm stud-on-work before anything else happens
--   2. PRESS    FT_Control holds 20 lbf while FT_LinInsertion drives in to it,
--               then the regulator holds that force for the hold window
--   3. WELD     re-check DI1, wait for DI0 (caps at charge), then pulse DO0
--               for 250 ms
--   4. HOLD     stay at pressure for 500 ms so the weld solidifies
--   5. RETRACT  return to the safe Z clearance WeldFlex.lua set
--   6. FEED     pulse DO1 for 1 s to advance the next stud
--
-- Any phase that cannot reach its condition retracts to safe Z, leaves DO0 off,
-- and raises a Lua error rather than welding into an unverified position.
--
-- -----------------------------------------------------------------------------
-- How this file talks back to the host — READ THIS FIRST
-- -----------------------------------------------------------------------------
-- print()/error() text never leaves the pendant console, so this file publishes
-- its progress two ways, both readable over XML-RPC while the program runs:
--
--   * SYSTEM VARIABLES (primary). pub() writes a phase code to s_var_1, the raw
--     return value of the last FT_* call to s_var_2, the tool Z at contact to
--     s_var_3, the travel the press achieved to s_var_4, and which
--     collision-threshold instruction the press got to s_var_5. The host reads
--     them with the SDK's GetSysVarValue(id), which is a real RPC call — unlike
--     FT_GetForceTorqueRCS and GetDI, whose SDK wrappers read the robot_state_pkg
--     cache that CNDE never fills on this firmware.
--
--     s_var_3 exists because the host samples tool Z every tick but has no
--     reference plane to measure it against, and it is published BEFORE the press
--     so it survives the program being killed mid-insertion — which is exactly
--     what an aborted press does. System variables persist after the program dies,
--     so as long as the arm has not been jogged, the host can still difference
--     s_var_3 against a live tool Z and recover how far a faulted press got.
--   * BEACON LINES (fallback). After a fault the program parks ~3 s on a line
--     unique to the fault site, and GetCurrentLine (proven live) sees it.
--     Comments are stripped for upload with line numbers preserved, so a traced
--     line number matches this file as it sits in the repo.
--
-- The beacons are kept because the system-variable channel is not yet verified
-- on this controller. If s_var_1 tracks the phase table below on the first run,
-- the beacons become redundant and can go.
--
-- -----------------------------------------------------------------------------
-- Input contract — WeldFlex.lua MUST set these globals before running this file
-- -----------------------------------------------------------------------------
--   weldX, weldY  the current stud's X/Y offset from the `zerozero` taught point.
--                 These are globals on purpose: `for _, stud in ipairs(studs)`
--                 binds a *local*, which a NewDofile'd chunk cannot see. The
--                 caller has to copy stud.x/stud.y out into weldX/weldY.
--   Z_CLEARANCE   safe Z offset in the work-object frame. RETRACT returns here.
--   WELD_RUN      1 executes the sequence; anything else (including unset) makes
--                 this file define-only. This is the upload gate: the controller's
--                 post-upload check EXECUTES top-level Lua (verified live
--                 2026-07-28 — it refused this file with requireContract's own
--                 error string), so loaded without a caller this file must do
--                 nothing at all.
--
-- Optional:
--   WELD_ARMED    1 fires DO0 for real; anything else (including unset) runs the
--                 whole sequence with the weld output disarmed. Unset means
--                 disarmed on purpose — the default must not be "strike an arc".
--   WELD_FORCE_TEST
--                 1 = prove the press, nothing else: SEARCH continues past an
--                 inactive DI1 (reported, not enforced — there may be no stud or
--                 welder in the loop yet), the hold at weld force is stretched to
--                 FORCE_TEST_HOLD_MS so it can be watched on the Weld Test page,
--                 and the sequence ends at RETRACT. WELD and FEED never run, and
--                 DO0 is forced off even if WELD_ARMED = 1 — the two flags are
--                 contradictory and disarmed wins.
--
-- There are deliberately no defaults for weldX/weldY. A missing offset would
-- silently weld at the origin of the work object, so it faults instead.
--
-- Frame of reference is WeldFlex.lua's: wobj = 4, tool = 10. feedCycle.lua and
-- testCycle.lua run wobj = 2 and are NOT a reference for anything here.
--
-- -----------------------------------------------------------------------------
-- Preconditions this file does NOT establish for itself
-- -----------------------------------------------------------------------------
--   * The F/T sensor must already be active and zeroed. That is done from the
--     Calibration page before the run; weld.lua deliberately does not re-zero,
--     so a stale or wrong-orientation zero shows up as a force offset here.
--   * A stud must already be loaded in the torch. The order below is weld-then-
--     feed, so stud 1 of cycle 1 fires into whatever the torch is holding.
--     feedCycle.lua is the standalone primer for that.
--   * The welder must be powered, with its ready line on DI0 and its work-return
--     clamped so DI1 can see continuity. WELD is gated on both — including on a
--     WELD_ARMED = 0 dry run, which exercises the interlocks rather than skipping
--     them. A dry run with the welder switched off will therefore stall at WELD
--     and fault on the DI0 timeout; the Weld Test page shows both inputs live, so
--     that reads as "welder off", not as a mystery.
--
-- -----------------------------------------------------------------------------
-- What the FR Lua manual settled, and what is still unverified
-- -----------------------------------------------------------------------------
-- Settled by docs/FR Lua programmingscript.txt (the controller-side Lua manual,
-- which is a DIFFERENT API from the Python SDK this file was first written
-- against):
--
--   * Every FT_* instruction documents "Return value null" (Tables 3-217 through
--     3-227), and the manual is WRONG about this firmware: FT_FindSurface
--     returned the number 1 from a search that physically succeeded (live
--     2026-07-29). Both the old `if err ~= 0` and its replacement `if
--     type(ret) == "number" and ret ~= 0` fault on that, which is why every run
--     to date has "found the surface and immediately retracted" — the retract
--     everyone was watching was fault()'s own. See ftRefused() for the rule that
--     replaced them and why 1 provably is not an error code.
--   * There is NO force-read instruction in controller Lua. FT_GetForceTorqueRCS
--     appears nowhere in the manual; it is Python-SDK-only. A Lua-side "poll
--     until force >= 20 lbf" loop is not implementable, which is why PRESS below
--     is a blocking FT_Control + FT_LinInsertion composite (manual Code 3-53)
--     rather than a ramp-and-sample loop. FT_Control IS the regulator; the host
--     is what verifies the force.
--   * FT_FindSurface `dir` is 1-positive / 2-negative (Table 3-224), but
--     FT_LinInsertion `linorn` is 0-negative / 1-positive (Table 3-223). Two
--     different encodings, which is why they do not share a constant below.
--   * pcall IS BANNED. The controller's post-upload check refuses the whole file
--     if the token appears anywhere in it — "pcall is not allowed in lua file"
--     (live refusal 2026-07-29). So nothing here can be written defensively by
--     catching it; a call that might throw has to be replaced by one that can't.
--     Assume the same of any other error-handling builtin (xpcall, assert).
--   * The system-variable telemetry channel WORKS. pub()'s bare numeric slot
--     reported both the phase and the FT_* return to the Weld Test page live on
--     2026-07-29 — which is the only reason the return value above was ever
--     seen. Slots 1 and 2 are a real channel now, not a hope.
--   * A travel-limit abort latches a resettable axis fault. FT_FindSurface hitting
--     disMax latched an axis-2 overspeed (2026-07-28), fixed by raising
--     SEARCH_MAX_MM. That is a real mechanism and worth checking — but it is NOT
--     what stops the press, and this bullet used to claim it was.
--   * A 20 lbf press is a collision by the controller's own definition, and that
--     is what kills STAGE 2. The collision threshold is a force on a 0-100 N scale
--     (Lua manual 3.3.9: "1~100% corresponds to 0~100N"), and PRESS_TARGET_N is
--     88.96 N — 89% of it, held deliberately. The axis-3 collision fault fired at
--     the shared 20 mm PRESS_MAX_MM on 2026-07-29 and fired again in the same
--     place after the budget was tripled to 60 mm, which is what separates this
--     from the disMax mechanism above: raising the budget changed nothing, so the
--     budget was never binding. STAGE 1 is unaffected — CONTACT_FORCE_N is 10 N,
--     a tenth of the scale — and the fix is pressCollisionGuard(), which widens
--     the monitor for the press window only. See the PRESS_COLL_* block.
--
-- Still unverified — check these first if the first live run misbehaves:
--   * GetActualTCPPose() in controller Lua. It appears in the manual's PrintMsg
--     example (section 3.1.6) and nowhere else — its return shape is a guess, so
--     readToolZ() type-checks everything and returns nil rather than assuming.
--     If s_var_3/s_var_4 stay at 0 while the press clearly moves, that read is
--     what failed, and nothing else in this file depends on it.
--   * FIND_RCS / FIND_AXIS address the TOOL frame (tool Z+ toward the work),
--     which is a different frame from the work-object Z that Z_CLEARANCE lives
--     in. If the torch retracts on SEARCH instead of approaching, set FIND_DIR
--     to 2 (negative) and change nothing else.
--   * FT_FindSurface's `ft` is a CONTACT trigger, not a press target — from
--     in-contact it declares success at first touch regardless of `ft`. That is
--     why PRESS uses FT_Control/FT_LinInsertion instead.
--   * FTC_SENSOR_NUM = 1. The vendor's Lua examples use 1; nothing in this repo
--     records what number the WebApp gave our sensor. If the press never builds
--     force, suspect this first. The Press travel tile is what tells them apart:
--     a press that settles around 13 mm (the ~1.5 lbf/mm chuck spring at 20 lbf)
--     was regulating on real force, while one that runs to PRESS_MAX_MM was
--     driving blind and this constant is wrong.
--   * CustomCollisionDetectionStart/End and SetAnticollision in controller Lua.
--     Both are in the manual (Tables 3-121 to 3-123) and neither has ever been
--     called on this cell. pressCollisionGuard() type-checks both and publishes
--     which one took to s_var_5, so a run that faults again says whether the fix
--     applied at all. GUARD_NONE means it did not.
--   * error() as the abort. It is core Lua, so it will definitely stop this
--     chunk. Whether the controller propagates it across the NewDofile boundary
--     and halts WeldFlex.lua's loop is NOT verified — if it does not, a faulted
--     stud is skipped and the run continues to the next one.
--
-- Dry-run this with WELD_ARMED = 0 before letting it strike an arc.
-- =========================================

-- ===== IO map =====
-- DI1 is a continuity circuit: welder -> work surface -> gun. It is what turns
-- "we touched something" into "the stud is seated on the work", so it gates both
-- the end of SEARCH and the weld pulse itself.
local DI_STUD_ON_WORK = 1
-- DI0 is the welder's own ready line — capacitor bank at charge. It drops after
-- each shot and returns when the bank recovers, so WELD waits on it rather than
-- sampling it once. Mirrored in app.py as WELD_READY_DI; nothing enforces that
-- across the language boundary, so the two are changed together.
local DI_WELD_READY   = 0

local DO_WELD    = 0        -- weld trigger
local DO_FEED    = 1        -- stud feeder advance (matches feedCycle.lua)

-- Press-verification mode, published by the caller as WELD_FORCE_TEST. See the
-- input contract above. Resolved once here so every gate below reads one local.
local FORCE_TEST_MODE = (WELD_FORCE_TEST == 1) and 1 or 0

-- Master arm, published by the caller as the WELD_ARMED global. 0 = run the full
-- motion/force sequence but never fire DO0. Defaults to DISARMED when the caller
-- sets nothing: an unset global must never be the thing that strikes an arc.
-- A force test can never be armed: weldOneStud() returns before fireWeld() in
-- that mode, and this gate makes the same promise even if that changes.
local DO_WELD_ENABLED = (WELD_ARMED == 1 and FORCE_TEST_MODE ~= 1) and 1 or 0

-- ===== Pulse and dwell timing =====
local WELD_PULSE_MS     = 250
local POST_WELD_HOLD_MS = 500   -- hold position at pressure so the weld sets
local FEED_PULSE_MS     = 1000

-- ===== Force =====
local N_PER_LBF = 4.448222

-- Light touch used to *detect* the surface. Same value test_ft_sensor.lua proved.
local CONTACT_FORCE_N = 10.0

local PRESS_TARGET_LBF = 20.0
local PRESS_TARGET_N   = PRESS_TARGET_LBF * N_PER_LBF   -- 88.96 N

-- Hard over-force ceiling, handed to FT_Guard. Only 5 lbf above the press
-- target, so on a stiff part this will nuisance-trip — raise it here.
local FORCE_CEILING_LBF = 25.0
local FORCE_CEILING_N   = FORCE_CEILING_LBF * N_PER_LBF -- 111.21 N

-- FT_Guard is the only over-force instruction controller Lua actually has
-- (Table 3-217) — with no force-read instruction, this file cannot check a
-- ceiling itself. It is OFF for bring-up: an FT_Guard trip is a collision abort,
-- and a latched fault blocks every raw RPC read with code 14 until it is
-- cleared (2026-07-28), which would destroy the telemetry from the very run
-- that is meant to prove the press.
--
-- Force is bounded two ways while this is off: FT_Control regulates to
-- PRESS_TARGET_N and FT_LinInsertion stops at it. Travel is NO LONGER a
-- meaningful third bound — the budgets below were raised to 60 mm, which against
-- the ~1.5 lbf/mm chuck spring is ~90 lbf, not the ~30 lbf the old 20 mm implied.
-- A dead sensor is therefore bounded by stroke, not by force. Turn this on once
-- the press is proven and the budgets are tightened back down.
local USE_FT_GUARD  = 0
local FT_GUARD_TOOL = 10        -- matches WeldFlex.lua's `tool`

local PRESS_HOLD_MS = 1000      -- force must stay at target this long

-- ===== Constant-force press (FT_Control) =====
-- The sensor's WebApp peripheral number. UNVERIFIED on our cell — the vendor's
-- Lua examples use 1. If the ramp never builds force, suspect this first.
local FTC_SENSOR_NUM = 1
-- Force-loop P gain, from the vendor SDK example. Higher builds force faster
-- and overshoots harder.
local FTC_GAIN_P = 0.0005

-- Force-test hold window. Long enough to watch on the Weld Test page (which
-- samples a few times a second) and to expose a slow force decay that the
-- production 1 s hold would miss.
local FORCE_TEST_HOLD_MS = 5000

-- How long WELD will hold at pressure waiting for the caps to come up to charge.
-- Sized for a recharge, not for a welder that is switched off — if DI0 is dead
-- this is just how long the torch sits on the part before faulting.
local READY_TIMEOUT_MS = 5000
local READY_SAMPLE_MS  = 100

-- ===== FT_FindSurface geometry =====
-- FT_FindSurface(rcs, dir, axis, lin_v, lin_a, disMax, ft) — Table 3-224.
local FIND_RCS  = 0     -- 0 = tool frame, 1 = base frame
local FIND_DIR  = 1     -- 1 = positive, 2 = negative  (NOT the same encoding as PRESS_DIR)
local FIND_AXIS = 3     -- 1 = X, 2 = Y, 3 = Z (1-indexed in controller Lua)
local FIND_ACC  = 0.0   -- manual: lin_a defaults to 0

-- FT_LinInsertion(rcs, ft, lin_v, lin_a, dismax, linorn) — Table 3-223.
-- linorn is 0 = negative, 1 = positive. Deliberately NOT shared with FIND_DIR:
-- the two instructions encode direction differently, and aliasing them would
-- silently reverse one of them the day somebody flips it.
local PRESS_DIR = 1

-- Approach speeds in mm/s. Only 3.0 is proven on this controller.
local SEARCH_SPEED_MMS = 5.0
local PRESS_SPEED_MMS  = 2.0

-- Travel ceilings in mm. SEARCH_MAX_MM is the blind-travel bound if the force
-- reading is dead — size it to real part-height variation, not generously.
-- TEMPORARY 100.0 for bring-up: at 15.0 the live run (2026-07-28) hit disMax
-- with no contact at offset (100,100) — the surface there sits lower than the
-- zerozero plane — and the disMax abort latched the axis-2 overspeed fault.
-- Tighten this back down once the test offset lands over the real fixture.
local SEARCH_MAX_MM  = 100.0
-- FT_LinInsertion's dismax: how far the insertion move may travel past contact.
--
-- TEMPORARY 60.0 for bring-up. The gun chuck/fixture is a spring at roughly
-- 1.5 lbf/mm (live 2026-07-28), so 20 lbf needs ~13 mm of stroke from zero force.
-- At the old shared 20.0 this and the regulator's budget were BOTH at their cap
-- right where the run died on a resettable axis-3 collision fault (live
-- 2026-07-29) — the same signature as the axis-2 overspeed on 2026-07-28, which
-- was FT_FindSurface's hard abort at disMax and was fixed the same way. Force,
-- not distance, is meant to be the stop condition. Tighten this back down to
-- just above the travel a proven press actually settles at.
local PRESS_MAX_MM = 60.0

-- FT_Control's max_dis: how far the force regulator may displace the TCP from
-- the commanded path. A SEPARATE budget from PRESS_MAX_MM, deliberately not
-- shared with it: the regulator starts spending this the moment FT_Control is
-- enabled, before the insertion move begins, so the two run down at different
-- rates and aliasing them means neither can be tuned without moving the other.
local PRESS_ADJUST_MM = 60.0

-- ===== Collision detection during the press =====
--
-- The controller's collision threshold is a FORCE, on a 0-100 N scale: "In custom
-- percentage mode, 1~100% corresponds to 0~100N" (Lua manual 3.3.9, Table 3-121).
-- The SDK agrees — SetAnticollision mode 0 is levels 1-10, mode 1 is 0-100%
-- (Robot.py:5192).
--
-- PRESS_TARGET_N is 88.96 N. That is 89% of the controller's entire collision
-- scale, applied deliberately and held for a second. To the joint-torque monitor a
-- correct 20 lbf press and a crash on the way down are the same signal, so the
-- press trips it partway up its own force ramp and the run dies on a resettable
-- axis-3 fault — the elbow carries the most unmodelled reaction torque when this
-- cell presses straight down. Seen live twice (2026-07-29), and PRESS_MAX_MM was
-- tripled between those two runs with no effect, which is what ruled out the
-- travel-limit abort behind the identical-looking axis-2 fault of 2026-07-28.
-- STAGE 1 has never tripped it and is left alone: CONTACT_FORCE_N is 10 N, a tenth
-- of the scale.
--
-- So the press has to widen the monitor for exactly as long as it owns the part,
-- and put it back. Both instructions below are type()-guarded the way FT_Guard is:
-- an unrecognised one leaves collision detection alone rather than aborting the
-- file, and pressCollisionGuard() publishes which one actually took.
--
-- A collision abort kills the program outright, so the release in forceControlOff()
-- does NOT run on that path — the widened setting survives until the controller is
-- reset. Both calls therefore pass config = 0 ("do not update configuration file",
-- Table 3-121), so nothing here can outlive a reboot.

-- Preferred lever, because CustomCollisionDetectionEnd() reverts to whatever the
-- pendant has configured. That matters more than it looks: nothing in this repo or
-- the SDK can READ the configured collision level — there is no getter, only the
-- robot_state_pkg field, and CNDE never connects on this FR-16 — so an instruction
-- that restores without needing to know the baseline is the only one that can be
-- scoped honestly. flag 3 = joint and TCP detection; the thresholds are the
-- vendor's own example values (Code 3-37), well clear of the 0-100 N band the
-- press lives in.
local PRESS_COLL_FLAG  = 3
local PRESS_COLL_JOINT = 100
local PRESS_COLL_TCP   = 300

-- Second lever, OFF by default. SetAnticollision has no getter to pair with it, so
-- turning this on means restoring to a GUESSED baseline below rather than to the
-- machine's real setting — a silent change to a safety parameter, which is not
-- something to do speculatively. Turn it on only if SV_PRESS_GUARD comes back
-- GUARD_NONE (this firmware has no custom-threshold instruction) or if the press
-- still faults with GUARD_CUSTOM applied, and set BASE_COLL_* to match the
-- pendant's Collision Level page first.
local USE_PRESS_ANTICOLLISION = 0
local PRESS_COLL_PCT   = 100    -- mode 1: percent of the 0-100 N scale
local BASE_COLL_MODE   = 0      -- 0 = standard level (1-10), 1 = percentage
local BASE_COLL_LEVEL  = 3      -- GUESS. Read the pendant before trusting this.

-- ===== Retract =====
local RETRACT_SPEED = 10        -- percent, per PTP arg 2


-- =========================================
-- Telemetry
-- =========================================

-- Phase codes published to s_var_1. The host maps these back to labels in
-- app.py's _WELD_PHASES; the two tables are changed together.
local PH_ENTER        = 10
local PH_SEARCH       = 20
local PH_SEARCH_DONE  = 21
local PH_PRESS_ON     = 30
local PH_PRESS_INSERT = 31
local PH_PRESS_HOLD   = 32
local PH_PRESS_HELD   = 33
local PH_WELD         = 40
local PH_RETRACT      = 50
local PH_DONE         = 60
-- A fault publishes 90 + its beacon site, so 91..911 name the same failures the
-- beacon lines do.
local PH_FAULT_BASE   = 90

local SV_PHASE    = 1   -- current phase code
local SV_LAST_RET = 2   -- raw return of the last FT_* call, encoded
-- Base-frame tool Z at the moment SEARCH confirmed contact, in mm. Published
-- BEFORE the press starts, on purpose: the host already samples tool Z every
-- tick, but an absolute Z means nothing without the plane the press began from,
-- and a press that is killed mid-insertion never gets to publish anything else.
-- With this slot the host can show how far the press actually drove even when
-- the program dies partway — which is exactly the axis-3 collision case.
local SV_PRESS_Z0 = 3
-- Travel the insertion actually achieved (SV_PRESS_Z0 minus tool Z after
-- FT_LinInsertion returned), in mm. Only lands when the insertion COMPLETES,
-- which is itself the signal: its absence means the press was aborted.
local SV_PRESS_TRAVEL = 4
-- Which collision-threshold instruction the press actually got, from the codes
-- below. Whether either exists on this firmware is the open question the next run
-- answers, and a press that faults again reads completely differently depending on
-- this slot: GUARD_NONE means the fix never applied, GUARD_CUSTOM means it applied
-- and 300 N of TCP headroom was still not enough.
local SV_PRESS_GUARD = 5

-- SV_PRESS_GUARD codes. Mirrored in app.py's _WELD_GUARD_CODES.
local GUARD_RELEASED = 0    -- restored, or never raised
local GUARD_CUSTOM   = 1    -- CustomCollisionDetectionStart only
local GUARD_BOTH     = 2    -- custom thresholds + SetAnticollision
local GUARD_LEVEL    = 3    -- SetAnticollision only
local GUARD_NONE     = 9    -- neither instruction exists; detection UNCHANGED

-- nil is not writable to a numeric system variable, and nil is exactly the
-- value worth reporting — the manual says every FT_* call returns null, and
-- confirming that on real firmware is half the point of this run.
local RET_NIL     = -999
local RET_NON_NUM = -998

-- The manual gives the setter as SetSysVarvalue (Table 3-12) while the Python
-- SDK spells it SetSysVarValue; the manual's own example is OCR-mangled enough
-- that neither the casing nor the argument form is trustworthy. Resolve it once.
local function sysVarSetter()
    if type(SetSysVarvalue) == "function" then return SetSysVarvalue end
    if type(SetSysVarValue) == "function" then return SetSysVarValue end
    return nil
end

-- Publish one telemetry value. Diagnostics must never be the reason a torch
-- stops mid-press, and the usual way to guarantee that — wrapping the call in
-- pcall — is unavailable: the controller refuses any file containing the token
-- (2026-07-29). So the guarantee has to come from the call itself.
--
-- The slot goes over as a bare NUMBER, which is the form that cannot throw under
-- either reading of the docs. The Python SDK's SetSysVarValue takes `id` in
-- [1..20] (Robot.py:4689), so a number is what the controller ultimately wants;
-- and if the Lua binding really does take the "system variable name" the manual
-- describes (Table 3-12), Lua's C API converts a number argument to a string for
-- free, so a number is still accepted. The reverse is not true — passing
-- "s_var_1" to a number-expecting binding throws. That also reconciles the
-- manual's own example, which passes an UNQUOTED s_var_3: most likely a
-- predefined global equal to 3, i.e. the number form already.
--
-- Safety then comes from homogeneity rather than from catching anything: every
-- pub() call in this file passes (number, number), and the first one runs at
-- PH_ENTER before any motion. If this is going to throw, it throws there, with
-- the torch parked clear — not mid-press with FT_Control holding 20 lbf.
local function pub(slot, value)
    local setter = sysVarSetter()
    if setter == nil then return end
    setter(slot, value)
end

-- Encode an FT_* return value so it survives a numeric system variable.
local function encodeRet(v)
    if v == nil then return RET_NIL end
    if type(v) ~= "number" then return RET_NON_NUM end
    return v
end

-- Run an FT_* instruction, publish what it returned, and hand back the raw
-- value. Centralised so every call site reports the return contract identically.
local function ftCall(fn, ...)
    local ret = fn(...)
    pub(SV_LAST_RET, encodeRet(ret))
    return ret
end

-- The one rule for reading an FT_* return.
--
-- The Lua manual documents "Return value null" for every FT_* instruction, but
-- this firmware returns numbers: on 2026-07-29 FT_FindSurface returned 1 after a
-- search the operator watched succeed — the torch descended, touched off, and
-- the only reason it came back up was the old version of this function calling
-- that 1 a refusal.
--
-- 1 cannot be a failure, because it is not a code. FAIRINO's error-code table
-- (SDK manual section 2.5) runs -7..-1, then 0 = "Successful call", then jumps
-- straight to 3. There is no code 1 and no code 2 anywhere in it. That reading of
-- the table is trustworthy: it puts 14 at "Interface execution failed", which is
-- exactly the code the host already sees live from FT_GetForceTorqueRCS while a
-- force-control move owns the sensor. So a returned 1 is something outside the
-- error space — a found/true flag, most likely — and treating an undocumented
-- small positive as fatal costs a whole run at the very first instruction.
--
-- Refusals are therefore only values inside the vendor's error space: negative,
-- or >= 3. nil, 0, 1, 2 and anything non-numeric all mean "nothing was reported".
-- Whatever comes back is published to SV_LAST_RET either way, so an unexpected
-- value is visible on the Weld Test page without being able to stop the run.
local function ftRefused(ret)
    if type(ret) ~= "number" then return false end
    return ret < 0 or ret >= 3
end


-- =========================================
-- Helpers
-- =========================================

-- Absolute move to a Z offset in the work-object frame, holding the stud's X/Y.
-- Mirrors the caller's enable -> PTP -> disable pattern in WeldFlex.lua. The
-- offset is measured from `zerozero`, so this is absolute: it returns to a known
-- pose no matter how far FT_FindSurface displaced the tool.
local function moveToZ(zOffset, vel)
    PointsOffsetEnable(1, weldX, weldY, zOffset, 0, 0, 0)
    PTP(zerozero, vel, -1, 0)
    PointsOffsetDisable()
end

-- Returns 1 when the input is active, 0 otherwise. GetDI is documented as
-- `ret = GetDI(id, thread)` (Table 3-76), a bare value — but this tolerates the
-- (err, value) shape too, because it costs nothing and the controller-side
-- return shapes have been wrong in this file before.
local function readDI(id)
    local a, b = GetDI(id, 0)
    local value = b
    if value == nil then
        value = a
    end
    if value == 1 then
        return 1
    end
    return 0
end

-- Base-frame tool Z in mm, or nil if it cannot be read.
--
-- GetActualTCPPose() is in the Lua manual (section 3.1.6's PrintMsg example), but
-- only as something printed — its return shape there is never stated, and the
-- return shapes in this file have been wrong before. pcall is banned (see the
-- header), so every assumption has to be a type() check instead of a catch:
-- neither a missing instruction nor an unexpected shape may stop a press, because
-- this exists only to report on one.
--
-- Base frame, not tool frame, so this matches the tcp_z the host reads with
-- GetActualTCPPose(0) and the two can be differenced against each other.
local function readToolZ()
    if type(GetActualTCPPose) ~= "function" then return nil end
    local p = GetActualTCPPose()
    if type(p) ~= "table" then return nil end
    if type(p[3]) ~= "number" then return nil end
    return p[3]
end

-- Fault-site beacon. Nothing can read print()/error() over RPC, so after the
-- retract, park long enough to be sampled on a line unique to the fault site.
-- Superseded by the s_var_1 phase code if that channel proves out; kept until
-- it does.
local FAULT_BEACON_MS = 3000

local function beacon(site)
    if site == 1 then WaitMs(FAULT_BEACON_MS) end   -- search: FT_FindSurface refused
    if site == 4 then WaitMs(FAULT_BEACON_MS) end   -- search: DI1 (stud on work) not active
    if site == 5 then WaitMs(FAULT_BEACON_MS) end   -- press: FT_LinInsertion refused
    if site == 9 then WaitMs(FAULT_BEACON_MS) end   -- press: FT_Control unavailable or refused
    if site == 10 then WaitMs(FAULT_BEACON_MS) end  -- weld: DI1 dropped before the pulse
    if site == 11 then WaitMs(FAULT_BEACON_MS) end  -- weld: DI0 (caps) never came up
end

-- Constant-force control on tool-frame Fz, on (1) or off (0). Full-arg form
-- from the Lua manual (Table 3-218): flag, sensor_num, select[6], force_torque[6],
-- gain[6], adj_sign, ILC_sign, max_dis, max_ang, polishRadio, filter_Sign,
-- posAdapt_sign, M0, M1, B0, B1, Threshold1, Threshold2, adjustCoeff1,
-- adjustCoeff2, isNoBlock — 36 values. Trailing posture-adapt parameters are
-- vendor defaults and inert with posAdapt_sign = 0. Extra args are harmless on
-- firmware with the shorter prototype — Lua drops them — while omitted args
-- would read nil on firmware with the longer one.
local function ftControlPress(flag)
    return FT_Control(flag, FTC_SENSOR_NUM,
        0, 0, 1, 0, 0, 0,                          -- select: Fz only
        0.0, 0.0, -PRESS_TARGET_N, 0.0, 0.0, 0.0,  -- target; pressing Fz is negative
        FTC_GAIN_P, 0.0, 0.0, 0.0, 0.0, 0.0,       -- force PID (P only)
        0, 0,                                      -- adj_sign, ILC_sign
        PRESS_ADJUST_MM, 0.0,                      -- max_dis (mm), max_ang
        0.0, 0, 0,                                 -- polishRadio, filter_Sign, posAdapt_sign
        2.0, 2.0, 8.0, 8.0,                        -- M0, M1, B0, B1 (defaults)
        0.2, 0.2, 1.0, 1.0,                        -- Threshold1/2, adjustCoeff1/2 (defaults)
        0)                                         -- isNoBlock: 0-blocking
end

-- Over-force guard on tool-frame Fz, on (1) or off (0). Table 3-217:
-- flag, tool_id, select[6], value[6], max_threshold[6], min_threshold[6].
-- Inert unless USE_FT_GUARD is set — see the note on that constant.
local function ftGuardPress(flag)
    if USE_FT_GUARD ~= 1 then return end
    -- The type() check is the whole guard here — pcall is banned (see header), so
    -- a firmware that has FT_Guard but dislikes these arguments will abort the
    -- program rather than degrade. That is one more reason USE_FT_GUARD stays 0
    -- until the press itself is proven.
    if type(FT_Guard) ~= "function" then return end
    FT_Guard(flag, FT_GUARD_TOOL,
        0, 0, 1, 0, 0, 0,                              -- select: Fz only
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,                  -- current values
        0.0, 0.0, FORCE_CEILING_N, 0.0, 0.0, 0.0,      -- max thresholds
        0.0, 0.0, -FORCE_CEILING_N, 0.0, 0.0, 0.0)     -- min thresholds
end

-- Both collision instructions take a per-joint table; neither has a scalar form.
local function sixOf(v)
    return {v, v, v, v, v, v}
end

-- Widen (1) or release (0) collision detection for the press window. See the
-- PRESS_COLL_* block for why a 20 lbf press reads as a collision on a 0-100 N
-- scale, and why the release cannot restore a level it has no way to read.
--
-- Publishes what actually took to SV_PRESS_GUARD. That is not decoration: both
-- instructions are unverified on this firmware, and if the press faults again the
-- next question is entirely different depending on whether this applied.
local function pressCollisionGuard(on)
    local haveCustom = type(CustomCollisionDetectionStart) == "function"
                   and type(CustomCollisionDetectionEnd) == "function"
    local haveLevel  = USE_PRESS_ANTICOLLISION == 1
                   and type(SetAnticollision) == "function"

    if on ~= 1 then
        if haveCustom then CustomCollisionDetectionEnd() end
        if haveLevel then
            SetAnticollision(BASE_COLL_MODE, sixOf(BASE_COLL_LEVEL), 0)
        end
        pub(SV_PRESS_GUARD, GUARD_RELEASED)
        return
    end

    if haveCustom then
        CustomCollisionDetectionStart(PRESS_COLL_FLAG,
            sixOf(PRESS_COLL_JOINT), sixOf(PRESS_COLL_TCP), 0)
    end
    if haveLevel then
        SetAnticollision(1, sixOf(PRESS_COLL_PCT), 0)
    end

    if haveCustom and haveLevel then
        pub(SV_PRESS_GUARD, GUARD_BOTH)
    elseif haveCustom then
        pub(SV_PRESS_GUARD, GUARD_CUSTOM)
    elseif haveLevel then
        pub(SV_PRESS_GUARD, GUARD_LEVEL)
    else
        -- Not a fault: the press is still worth attempting, and the run that
        -- faults with this published is what proves the instruction is missing.
        pub(SV_PRESS_GUARD, GUARD_NONE)
        print("[WELD] WARNING: no collision-threshold instruction available — " ..
              "the press runs at the configured level and will likely fault.")
    end
end

-- Drop every force-control overlay. Safe to call when nothing is enabled, and
-- called before any commanded motion: retracting while FT_Control still owns Z
-- would have two controllers fighting over the same axis.
--
-- Collision detection is narrowed back LAST, after the regulator has let go, so
-- the monitor is never tightened while something is still pushing on the part.
local function forceControlOff()
    ftGuardPress(0)
    ftControlPress(0)
    pressCollisionGuard(0)
end

-- Retract, disarm, and stop. Never returns. `site` picks the beacon line above.
local function fault(msg, site)
    SPLCSetDO(DO_WELD, 0)
    forceControlOff()
    pub(SV_PHASE, PH_FAULT_BASE + site)
    moveToZ(Z_CLEARANCE, RETRACT_SPEED)
    print("[WELD] FAULT: " .. msg)
    beacon(site)
    error("[WELD] " .. msg)
end

-- Block until the welder reports caps-at-charge, or fault trying.
--
-- Polled rather than handed to WaitDI: WaitDI aborts the program itself on
-- timeout, which would skip the retract-and-disarm that every other failure in
-- this file goes through — leaving the torch parked on the work at 20 lbf.
local function waitForWeldReady()
    local waited = 0
    while readDI(DI_WELD_READY) ~= 1 do
        if waited >= READY_TIMEOUT_MS then
            fault(string.format("DI%d (caps at charge) never came up within %d ms — welder off, not ready, or unwired",
                DI_WELD_READY, READY_TIMEOUT_MS), 11)
        end
        WaitMs(READY_SAMPLE_MS)
        waited = waited + READY_SAMPLE_MS
    end
    if waited > 0 then
        print(string.format("[WELD] Waited %d ms for DI%d (caps at charge).", waited, DI_WELD_READY))
    end
end

local function requireContract()
    if weldX == nil or weldY == nil then
        error("[WELD] weldX/weldY not set — WeldFlex.lua must publish the stud offset")
    end
    if Z_CLEARANCE == nil then
        error("[WELD] Z_CLEARANCE not set — WeldFlex.lua must publish the safe Z")
    end
end


-- =========================================
-- Phases
-- =========================================

-- Tool Z at contact, carried from SEARCH to PRESS so the press can report the
-- travel it achieved. nil when the pose could not be read; every use checks.
local pressZ0 = nil

-- STAGE 1. One continuous guarded move in to light contact, then confirm with
-- DI1 that what we touched is actually the stud sitting on the work.
-- FT_FindSurface's own force threshold and disMax bound the approach; DI1's
-- continuity is what makes the contact meaningful rather than just "we hit
-- something".
--
-- There is no force check here any more. Controller Lua has no force-read
-- instruction, so the old `pressForceOrFault("search", ...)` was calling a
-- function this environment does not define. The host reads the contact force
-- over RPC instead — it is on the Weld Test page throughout.
local function searchForStud()
    pub(SV_PHASE, PH_SEARCH)

    local ret = ftCall(FT_FindSurface, FIND_RCS, FIND_DIR, FIND_AXIS,
                       SEARCH_SPEED_MMS, FIND_ACC, SEARCH_MAX_MM, CONTACT_FORCE_N)
    if ftRefused(ret) then
        fault(string.format("FT_FindSurface refused the approach (code %s), no surface within %.1f mm",
            tostring(ret), SEARCH_MAX_MM), 1)
    end

    pub(SV_PHASE, PH_SEARCH_DONE)

    -- Publish the contact plane here rather than at the end of this function, so
    -- it lands on both exits — the force-test path below returns early, and that
    -- is the path being used to debug the press.
    pressZ0 = readToolZ()
    if pressZ0 ~= nil then
        pub(SV_PRESS_Z0, pressZ0)
    end

    if readDI(DI_STUD_ON_WORK) ~= 1 then
        -- In a force test there may legitimately be no stud or welder in the
        -- loop yet — report the missing continuity and carry on to the press.
        if FORCE_TEST_MODE == 1 then
            print(string.format("[WELD] Force test: DI%d (stud on work) not active — pressing anyway.",
                DI_STUD_ON_WORK))
            return
        end
        fault(string.format("touched a surface but DI%d (stud on work) is not active",
            DI_STUD_ON_WORK), 4)
    end

    print(string.format("[WELD] Contact confirmed, DI%d active.", DI_STUD_ON_WORK))
end

-- STAGE 2. Drive to weld force and hold it there.
--
-- This is the vendor's documented press-in composite, manual Code 3-53 lines
-- 17-20: enable the constant-force loop, issue ONE blocking insertion move that
-- stops at the force threshold, then let the loop regulate through a plain
-- WaitMs. Force control in this API is an overlay on a commanded motion, not
-- something that moves on its own and not something you sample — every vendor
-- example pairs FT_Control with a motion or insertion instruction.
--
-- The previous ramp-and-poll design could not work: it sampled with
-- FT_GetForceTorqueRCS, which controller Lua does not define. The regulator is
-- now the thing that guarantees the force, and the WaitMs is the "steady for
-- 1 second" requirement — the host watches the actual value over RPC.
--
-- FT_Control is enabled here and deliberately left ON when this returns, so
-- pressure follows the stud through WELD and HOLD as it melts. retract() and
-- fault() both shut it off before commanding any other motion.
local function pressToForce()
    -- The force test stretches the hold so a slow decay is visible; the weld
    -- itself only needs the force held for PRESS_HOLD_MS.
    local holdMs = PRESS_HOLD_MS
    if FORCE_TEST_MODE == 1 then
        holdMs = FORCE_TEST_HOLD_MS
    end

    if type(FT_Control) ~= "function" then
        fault("FT_Control is not available in this controller's Lua", 9)
    end
    if type(FT_LinInsertion) ~= "function" then
        fault("FT_LinInsertion is not available in this controller's Lua", 9)
    end

    -- Widen the collision monitor before anything starts pushing. 20 lbf is 89% of
    -- the controller's 0-100 N collision scale, so at the configured level the
    -- press trips it on the way up — see the PRESS_COLL_* block. Raised here
    -- rather than at entry so it covers the press and nothing else; every exit
    -- from this point runs forceControlOff(), which puts it back.
    pressCollisionGuard(1)

    ftGuardPress(1)

    pub(SV_PHASE, PH_PRESS_ON)
    local ret = ftCall(ftControlPress, 1)
    if ftRefused(ret) then
        fault(string.format("FT_Control refused to start (code %s)", tostring(ret)), 9)
    end

    -- Drive in until the sensor sees weld force. Blocking; stops on `ft` or on
    -- dismax, whichever comes first. `ft` is a magnitude in the documented
    -- 0-100 N range, and the direction comes from PRESS_DIR.
    pub(SV_PHASE, PH_PRESS_INSERT)
    ret = ftCall(FT_LinInsertion, FIND_RCS, PRESS_TARGET_N,
                 PRESS_SPEED_MMS, 0.0, PRESS_MAX_MM, PRESS_DIR)
    if ftRefused(ret) then
        fault(string.format("FT_LinInsertion refused the press (code %s)", tostring(ret)), 5)
    end

    -- How far the insertion actually drove. This is the one number that separates
    -- "stopped because it reached weld force" from "stopped because it ran out of
    -- dismax" — and the latter is what aborts hard enough to latch an axis fault.
    -- A value at PRESS_MAX_MM means the budget, not the force, ended the move.
    local zNow = readToolZ()
    if pressZ0 ~= nil and zNow ~= nil then
        local travel = pressZ0 - zNow
        pub(SV_PRESS_TRAVEL, travel)
        print(string.format("[WELD] Press travelled %.2f mm of %.1f mm allowed.",
            travel, PRESS_MAX_MM))
    end

    -- Hold. FT_Control is still regulating to PRESS_TARGET_N, so standing still
    -- here IS the constant-force hold.
    pub(SV_PHASE, PH_PRESS_HOLD)
    WaitMs(holdMs)
    pub(SV_PHASE, PH_PRESS_HELD)

    print(string.format("[WELD] Held %.1f lbf for %d ms.", PRESS_TARGET_LBF, holdMs))
end

local function fireWeld()
    pub(SV_PHASE, PH_WELD)

    -- Final interlocks, cheapest failure first. The stud has to still be seated
    -- when the arc strikes, and the bank has to actually be at charge — firing
    -- into a flat bank spends a stud without making a weld.
    if readDI(DI_STUD_ON_WORK) ~= 1 then
        fault(string.format("DI%d (stud on work) dropped before the weld pulse", DI_STUD_ON_WORK), 10)
    end

    -- Checked on dry runs too: a run that skips the interlock is not a rehearsal
    -- of this sequence. See the note on the welder in the preconditions header.
    waitForWeldReady()

    if DO_WELD_ENABLED ~= 1 then
        print("[WELD] Disarmed (WELD_ARMED ~= 1) — dry run, DO0 not fired.")
        WaitMs(WELD_PULSE_MS)
        return
    end

    SPLCSetDO(DO_WELD, 1)
    WaitMs(WELD_PULSE_MS)
    SPLCSetDO(DO_WELD, 0)
end

-- Hold station at weld force while the stud solidifies. No motion command here
-- on purpose: the robot simply stays where pressToForce() left it, with
-- FT_Control still regulating.
local function holdAfterWeld()
    WaitMs(POST_WELD_HOLD_MS)
end

local function retract()
    -- pressToForce leaves the force overlays running so pressure follows the
    -- stud through WELD and HOLD; they must die before the retract move.
    forceControlOff()
    pub(SV_PHASE, PH_RETRACT)
    moveToZ(Z_CLEARANCE, RETRACT_SPEED)
end

local function feedNextStud()
    SPLCSetDO(DO_FEED, 1)
    WaitMs(FEED_PULSE_MS)
    SPLCSetDO(DO_FEED, 0)
end


-- =========================================
-- Sequence
-- =========================================

local function weldOneStud()
    requireContract()
    pub(SV_PHASE, PH_ENTER)
    pub(SV_LAST_RET, 0)
    -- System variables persist across runs, so clear the press slots here. A
    -- stale contact plane from the last stud would make the host compute travel
    -- against the wrong reference and report a plausible, wrong number.
    pressZ0 = nil
    pub(SV_PRESS_Z0, 0)
    pub(SV_PRESS_TRAVEL, 0)
    pub(SV_PRESS_GUARD, GUARD_RELEASED)

    -- Assert the safe state before moving: a latched weld output from an
    -- aborted previous stud would strike an arc during the approach.
    SPLCSetDO(DO_WELD, 0)

    searchForStud()
    pressToForce()

    -- A force test ends here: the press is proven, so retract and stop. WELD
    -- and FEED are unreachable in this mode — see the input contract.
    if FORCE_TEST_MODE == 1 then
        print("[WELD] Force test complete — weld force reached and held. Retracting.")
        retract()
        pub(SV_PHASE, PH_DONE)
        return
    end

    fireWeld()
    holdAfterWeld()
    retract()
    feedNextStud()
    pub(SV_PHASE, PH_DONE)
end

-- Upload gate — see WELD_RUN in the input contract. The controller's check runs
-- this top level with no globals set, so unset must mean "define only, do
-- nothing". Deliberately not folded into requireContract(): WELD_RUN = 1 with
-- weldX missing is a broken caller and must still fault loudly.
if WELD_RUN == 1 then
    weldOneStud()
end
