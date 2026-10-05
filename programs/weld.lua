-- =========================================
-- weld.lua — Weld sub-process for one stud
--
-- Executed per stud by WeldFlex.lua (and once by single_shot.lua) with the
-- torch parked over the stud at the caller's Z_CLEARANCE. The retract lifts
-- to the caller's Z_RETRACT, and the next stud feeds there.
-- Sequence: SEARCH -> PRESS -> WELD -> HOLD -> RETRACT -> FEED
-- See docs/weldNotes.md for full technical documentation & bring-up notes.
-- =========================================

-- ===== IO Map =====
local DI_STUD_ON_WORK = 1  -- Continuity circuit (stud seated on work)
local DI_WELD_READY   = 0  -- Welder ready signal (capacitor charge)

local DO_WELD    = 0       -- Weld trigger output
local DO_FEED    = 1       -- Stud feeder advance output


-- ===== Timing (ms) =====
local WELD_PULSE_MS     = 250
local POST_WELD_HOLD_MS = 500
local FEED_PULSE_MS = (type(WELD_FEED_PULSE_MS) == "number"
    and WELD_FEED_PULSE_MS >= 1
    and WELD_FEED_PULSE_MS <= 10000) and WELD_FEED_PULSE_MS or 250

-- ===== Force Config =====
local N_PER_LBF = 4.448222
local CONTACT_FORCE_N = 10.0

-- FT_LinInsertion accepts at most 100 N; leave conversion margin below it.
local PRESS_TARGET_MAX_LBF = 22.0
local PRESS_TARGET_LBF     = 20.0
if type(WELD_PRESS_LBF) == "number"
   and WELD_PRESS_LBF > 0
   and WELD_PRESS_LBF <= PRESS_TARGET_MAX_LBF then
    PRESS_TARGET_LBF = WELD_PRESS_LBF
end
local PRESS_TARGET_N = PRESS_TARGET_LBF * N_PER_LBF

-- FT_LinInsertion decides when the press motion can end. Its lower threshold
-- is the accepted low edge of the requested +/- 0.5 lbf force tolerance;
-- FT_Control continues regulating at the full requested target through hold.
local PRESS_TOLERANCE_LBF = 0.5
local PRESS_INSERT_THRESHOLD_LBF = PRESS_TARGET_LBF - PRESS_TOLERANCE_LBF
if PRESS_INSERT_THRESHOLD_LBF <= 0.0 then
    PRESS_INSERT_THRESHOLD_LBF = PRESS_TARGET_LBF
end
local PRESS_INSERT_THRESHOLD_N = PRESS_INSERT_THRESHOLD_LBF * N_PER_LBF

local FORCE_CEILING_LBF = 25.0
local FORCE_CEILING_N   = FORCE_CEILING_LBF * N_PER_LBF

local USE_FT_GUARD  = 0
local FT_GUARD_TOOL = 10

-- FT_LinInsertion ends on the first reading at or past its threshold, and seating
-- the stud can spike the reading past it for an instant before it sags well under
-- target (live 2026-09-14: a press that stopped on a momentary 19 lbf barely
-- touched, while a jog held at a steady 19 lbf welds right). Do not answer that by
-- re-running FT_LinInsertion during the hold. Lua cannot read force, so a re-run
-- cannot know whether it starts past its threshold, and the one dry run that tried
-- it (four re-runs, same day) ended in a resettable "Cartesian space command speed
-- exceeded limit" fault. Live shots on this passive hold held pressure accurately.
local PRESS_HOLD_MS = 1000

-- ===== FT_Control & Motion Parameters =====
local FTC_SENSOR_NUM = 1
if type(WELD_FT_SENSOR_NUM) == "number"
   and WELD_FT_SENSOR_NUM >= 1
   and WELD_FT_SENSOR_NUM <= 255 then
    FTC_SENSOR_NUM = WELD_FT_SENSOR_NUM
end
-- FT_Control's proportional gain: how fast it moves for a given force error, and
-- with no press feed (PRESS_FEED_MMS) what sets how fast the press closes.
-- 0.0003 was commissioned on 2026-10-05: a smooth climb to 16 lbf, live and dry,
-- where 0.0001 (the gain since 2026-09-04) took ~6.5 s touch to lift. 0.005
-- before 2026-09-04, dropped with no reason recorded; FAIRINO suggests about
-- 0.001. The Admin page's Weld Tuning publishes WELD_PRESS_GAIN. Too much gain
-- overshoots or oscillates, and the gun bottoms out near 9 lbf (see
-- PRESS_FEED_MMS), so raise it in small steps on dry shots.
-- backend/weld_tuning.py holds the same numbers.
local FTC_GAIN_P = 0.0003
local FTC_GAIN_MIN = 0.00005
local FTC_GAIN_MAX = 0.001
if type(WELD_PRESS_GAIN) == "number"
   and WELD_PRESS_GAIN >= FTC_GAIN_MIN
   and WELD_PRESS_GAIN <= FTC_GAIN_MAX then
    FTC_GAIN_P = WELD_PRESS_GAIN
end

local READY_TIMEOUT_MS   = 5000
local READY_SAMPLE_MS    = 100

-- FT_FindSurface & FT_LinInsertion parameters
local FIND_RCS  = 0     -- 0 = tool frame, 1 = base frame
local FIND_DIR  = 2     -- 1 = positive, 2 = negative (flipped with TCP Z, 2026-09-01)
local FIND_AXIS = 3     -- 3 = Z axis
local FIND_ACC  = 0.0

-- FT_LinInsertion requires a direction, but with no feed (PRESS_FEED_MMS) it
-- moves nothing in it. When it did feed, the feed worked against FT_Control
-- (2026-10-05). Whether this or the encoding was wrong is unproven: the Lua and
-- SDK manuals give 0/1, the 8080 protocol manual 1 = positive / 2 = negative.
local PRESS_DIR = 0     -- 0 = negative (FT_LinInsertion encoding; flipped with TCP Z, 2026-09-01)

-- The search speed. Search 10 mm/s on 2026-09-14 ran with presses that stalled,
-- but the press feed was the cause (below), not the search. A lower park height
-- (a run's Search Height) also shortens the search.
--
-- Since 2026-10-02 this is only the fallback. While in beta the Admin page's
-- Weld Tuning panel sets it for every run, and the caller publishes it as
-- WELD_SEARCH_SPEED_MMS. A value that is missing or outside the bounds below is
-- ignored. The ceiling is the fastest it has run on hardware.
-- backend/weld_tuning.py holds the same numbers.
local SEARCH_SPEED_MMS = 7.5
local SEARCH_SPEED_MIN_MMS = 0.5
local SEARCH_SPEED_MAX_MMS = 10.0
if type(WELD_SEARCH_SPEED_MMS) == "number"
   and WELD_SEARCH_SPEED_MMS >= SEARCH_SPEED_MIN_MMS
   and WELD_SEARCH_SPEED_MMS <= SEARCH_SPEED_MAX_MMS then
    SEARCH_SPEED_MMS = WELD_SEARCH_SPEED_MMS
end

-- FT_LinInsertion's own feed, on top of FT_Control: none. It was meant to help
-- FT_Control close on the target, but a dry Single Shot ladder on 2026-10-05
-- showed it working against it: 0.15 / 0.25 / 0.35 mm/s took about 8.5 / 10 /
-- 14 s, and the force hunted up and down more as the speed rose. Faster feeds
-- never finished: 0.5 stalled short on 2026-10-02 until the gun was pushed, as
-- 1.0 and 0.5 did on 2026-09-14. Those stalls were put down to arriving too
-- fast at the time, and the press was slowed to 0.25, 0.10 and then 0.15 for
-- it; they were this fight.
--
-- With no feed the insertion moves nothing and only ends the press on force,
-- while FT_Control alone moves the gun at FTC_GAIN_P. FAIRINO's own insertion
-- example (readthedocs 2.1.12.27) does the same. The Admin page's press speed
-- and its "Feed" mode, which kept the old press, are archived at the
-- archive/press-feed-mode tag.
--
-- Also seen 2026-09-28 with a 0.25 feed: a 10 lbf press read ~9 lbf on the Force
-- page and then jumped straight to 14-15. Something in the gun bottoms out near
-- 9 lbf, after which force climbs almost vertically for little travel.
local PRESS_FEED_MMS = 0.0

-- The park height is the caller's global Z_CLEARANCE, read where it is used and
-- never shadowed here: WeldFlex.lua parks at the Search Height, single_shot.lua at
-- its Safe Z. The search starts there. The retract lifts to the caller's
-- Z_RETRACT instead (see departFromStud()), so the feed never fires over the
-- stud just welded at the Search Height (owner, 2026-10-02).
local SEARCH_MAX_MM   = 100.0
local PRESS_MAX_MM    = 60.0
local PRESS_ADJUST_MM = 60.0

-- ===== Collision Guard Settings =====
local PRESS_GUARD_MIN_N = 40.0

local PRESS_COLL_FLAG  = 3
local PRESS_COLL_JOINT = 500
local PRESS_COLL_TCP   = 1000

local USE_PRESS_ANTICOLLISION = 1
local PRESS_COLL_PCT   = 100
local BASE_COLL_MODE   = 0
local BASE_COLL_LEVEL  = 3

local USE_PRESS_COLL_OFF   = 1
local PRESS_COLL_OFF_LEVEL = 10

-- ===== Retract =====
-- A Lin percentage, capped again by the pendant's Auto Speed. Raised from 10 on
-- 2026-09-30 as a trial: at 10 a welded stud sometimes stayed in the chuck long
-- enough to bring the plate up with it and trip "Force sensor range threshold
-- reached", and the same stud then came off clean on a manual jog straight up.
-- 25 matches WeldFlex.lua's default travel speed. Live since that day; on
-- 2026-10-01 the pull-off had less resistance, but zerozero had just been
-- re-taught too, so which change helped is unknown. Later on 2026-10-01 it
-- still tripped at 25 on three studs, each about 0.2 s into this Lin, so it
-- came down to 5, under both speeds that have tripped and the Points page's
-- descent speed. See docs/weldNotes.md.
local RETRACT_SPEED = 5

-- How far the head may read off the park pose's X/Y at the retract before the
-- straight-up lift stops trusting the reading (see departFromStud()). A real
-- drift is the descent's depth times tool Z's tilt off the bed's vertical, about
-- 1 mm for 2 degrees over 30 mm, so 10 mm means something other than tilt.
local LIFT_MAX_DRIFT_MM = 10.0


-- =========================================
-- Telemetry Definitions
-- =========================================

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
local PH_FAULT_BASE   = 90

local SV_PHASE        = 1
local SV_LAST_RET     = 2
local SV_PRESS_Z0     = 3
local SV_PRESS_TRAVEL = 4
local SV_PRESS_GUARD  = 5
local SV_STUD_ON_WORK = 6
local SV_WELD_READY   = 7
local SV_PRESS_LBF    = 8
local SV_PRESS_HOLD_TRAVEL = 9
local SV_WELD_JOLT_TRAVEL  = 10

local GUARD_RELEASED   = 0
local GUARD_CUSTOM     = 1
local GUARD_BOTH       = 2
local GUARD_LEVEL      = 3
local GUARD_NOT_NEEDED = 4
local GUARD_NONE       = 9

local RET_NIL     = -999
local RET_NON_NUM = -998

local function sysVarSetter()
    if type(SetSysVarvalue) == "function" then return SetSysVarvalue end
    if type(SetSysVarValue) == "function" then return SetSysVarValue end
    return nil
end

local current_phase = 0
local current_di1   = -1
local current_di0   = -1

local function pub(slot, value)
    local setter = sysVarSetter()
    if setter == nil then return end

    if slot == SV_PHASE then current_phase = value end
    if slot == SV_STUD_ON_WORK then current_di1 = value end
    if slot == SV_WELD_READY then current_di0 = value end

    if slot == SV_PHASE or slot == SV_STUD_ON_WORK or slot == SV_WELD_READY then
        local d1 = (current_di1 == 1 and 1 or (current_di1 == 0 and 0 or 9))
        local d0 = (current_di0 == 1 and 1 or (current_di0 == 0 and 0 or 9))
        local packed = current_phase + (100 * d1) + (1000 * d0)
        setter(SV_PHASE, packed)
    end

    if slot ~= SV_PHASE then
        setter(slot, value)
    end
end

local function encodeRet(v)
    if v == nil then return RET_NIL end
    if type(v) ~= "number" then return RET_NON_NUM end
    return v
end

local function ftCall(fn, ...)
    local ret = fn(...)
    pub(SV_LAST_RET, encodeRet(ret))
    return ret
end

local function ftRefused(ret)
    if type(ret) ~= "number" then return false end
    return ret < 0 or ret >= 3
end

-- The recipe's DI check, published by the caller as WELD_DI_CHECK. Only an
-- explicit 0 turns the DI0/DI1 checks off; a caller that never publishes it
-- gets them.
local function diCheckEnabled()
    return WELD_DI_CHECK ~= 0
end


-- =========================================
-- Helper Functions
-- =========================================

local function readDI(id)
    local ret1, ret2 = GetDI(id, 0)
    local level = 0
    if ret1 == 1 or ret1 == true or ret2 == 1 or ret2 == true then
        level = 1
    end
    if id == DI_STUD_ON_WORK then
        pub(SV_STUD_ON_WORK, level)
    elseif id == DI_WELD_READY then
        pub(SV_WELD_READY, level)
    end
    return level
end

local function writeDO(id, status)
    if type(SetDO) == "function" then
        SetDO(id, status, 0, 0)
    end
    if type(SPLCSetDO) == "function" then
        SPLCSetDO(id, status)
    end
end

local function readToolZ()
    if type(GetActualTCPPose) ~= "function" then return nil end
    local p = GetActualTCPPose()
    if type(p) ~= "table" then return nil end
    if type(p[3]) ~= "number" then return nil end
    return p[3]
end

local function readPose()
    if type(GetActualTCPPose) ~= "function" then return nil end
    local p = GetActualTCPPose()
    if type(p) ~= "table" then return nil end
    if type(p[1]) ~= "number" or type(p[2]) ~= "number" then return nil end
    return p
end

-- ===== F/T Collision Guard Off For The Lift =====
-- FT_Guard(0) turns off the force sensor's collision guard. It does not switch
-- the sensor itself off, and no Lua instruction can. weld.lua never turned that
-- guard on before ftGuardTravel() below, so the first FT_Guard(0) only changed
-- anything if something else had left it on, such as a pendant program. It did:
-- with it before the lift, "Force sensor range threshold reached" stopped
-- tripping at the retract (owner, 2026-10-02, b885e56). Unconditional, unlike
-- ftGuardPress(), and all six axes in case the off is per-axis. Also runs before
-- every search, since the travel guard is armed by then.
local function ftGuardOff()
    if type(FT_Guard) ~= "function" then
        print("[WELD] FT_Guard is not available; continuing without turning the guard off.")
        return
    end
    FT_Guard(0, FTC_SENSOR_NUM,
        1, 1, 1, 1, 1, 1,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    print("[WELD] F/T collision guard off.")
end

-- ===== F/T Collision Guard On For Travel =====
-- Owner's call, 2026-10-02: with the guard off for the lift, turn it back
-- on once the stud is done, so the caller's travel to the next stud (and home)
-- stops on a hit. Armed after the feed, since a stud blown into the chuck can
-- spike the reading, and only on a clean finish: after a fault it stays off for
-- the recovery. Off again before the next search (weldOneStud()), because the
-- search and press load the sensor far past this window.
--
-- This is weld.lua's own guard, not whatever was on before: nothing can read a
-- guard's settings back. The window is +/-TRAVEL_GUARD_N on Fx, Fy and Fz around
-- zero, since Lua cannot read force to take a starting value; the head is in
-- free air here, so the zeroed reading should be near 0. A side hit at the gun
-- tip reaches the sensor as the same force, so the moments are left out. The
-- guard stays on after the run ends, until the next search turns it off.
local USE_TRAVEL_GUARD = 1
local TRAVEL_GUARD_N   = 30.0   -- ~6.7 lbf; the first value tried, fine in the 2026-10-02 test runs

local function ftGuardTravel()
    if USE_TRAVEL_GUARD ~= 1 then return end
    if type(FT_Guard) ~= "function" then
        print("[WELD] FT_Guard is not available; travelling without a force guard.")
        return
    end
    FT_Guard(1, FTC_SENSOR_NUM,
        1, 1, 1, 0, 0, 0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        TRAVEL_GUARD_N, TRAVEL_GUARD_N, TRAVEL_GUARD_N, 0.0, 0.0, 0.0,
        TRAVEL_GUARD_N, TRAVEL_GUARD_N, TRAVEL_GUARD_N, 0.0, 0.0, 0.0)
    print(string.format("[WELD] F/T collision guard on for travel, +/-%.0f N on Fx/Fy/Fz.", TRAVEL_GUARD_N))
end

-- ===== Departure: Straight Up Off The Bed =====
-- The lift goes straight up the workpiece Z from wherever the head actually is,
-- the way the Points page lifts (backend/point_moves.py): only Z changes. A side
-- load on the chuck trips the sensor's moment limit almost at once (owner,
-- 2026-10-01: about 3 lbf pushed sideways on the chuck faults it), so the lift
-- must not drag the chuck sideways off the stud.
--
-- The caller parks the torch at zerozero + (weldX, weldY, Z_CLEARANCE) in the
-- workpiece frame, and FT_FindSurface and FT_LinInsertion then drive straight down
-- tool Z (FIND_RCS = 0). If zerozero's taught orientation leaves tool Z off the
-- bed's vertical, the pressed head ends up beside the park pose's X/Y. Until
-- 2026-10-01 the retract was a Lin back to the park pose, which retraced tool Z
-- and so pulled at that same tilt.
--
-- The head's offset is measured, not assumed: GetActualTCPPose at the park pose
-- (weldOneStud(), before anything moves) and again here. Only the difference is
-- used, so the reading's origin cancels out. It must share the workpiece frame's
-- axes, the same assumption the Points page makes. With either reading missing,
-- or the head more than LIFT_MAX_DRIFT_MM off the park pose, it falls back to the
-- old Lin to the park pose's X/Y, which runs back close to the descent.
--
-- It lifts to the caller's Z_RETRACT (a run's Retract Z), not back to the park
-- height, and feedNextStud() runs after it. Until 2026-10-02 it returned to
-- Z_CLEARANCE, the Search Height, so the next stud fed just above the stud
-- just welded, and the caller lifted to Retract Z only afterwards. One Lin at
-- RETRACT_SPEED the whole way, so it stays slow for however long the chuck is
-- still on the stud. A Z_RETRACT below the park height is not lowered to.
--
-- A Lin, not PTP or MoveCart: those interpolate in joint space, which bows the
-- path while the collet is still on the stud.
--
-- Every departure from a stud goes through here, faults included: a fault
-- during press leaves the collet on the stud exactly like a good weld does.
local parkPose = nil

local function liftHeight()
    if Z_RETRACT > Z_CLEARANCE then return Z_RETRACT end
    return Z_CLEARANCE
end

local function departFromStud()
    local liftX, liftY = weldX, weldY
    local now = readPose()
    if parkPose ~= nil and now ~= nil then
        local dx = now[1] - parkPose[1]
        local dy = now[2] - parkPose[2]
        if dx * dx + dy * dy <= LIFT_MAX_DRIFT_MM * LIFT_MAX_DRIFT_MM then
            liftX = weldX + dx
            liftY = weldY + dy
            print(string.format("[WELD] Lifting straight up from %.3f, %.3f mm off the park pose.", dx, dy))
        else
            print(string.format("[WELD] WARNING: head reads %.1f, %.1f mm off the park pose; " ..
                "retracing the descent instead of lifting straight up.", dx, dy))
        end
    else
        print("[WELD] WARNING: no pose reading; retracing the descent instead of lifting straight up.")
    end

    ftGuardOff()

    -- flag=0: workpiece frame, matching WeldFlex.lua's traverse (see its comment).
    PointsOffsetEnable(0, liftX, liftY, liftHeight(), 0, 0, 0)
    Lin(zerozero, RETRACT_SPEED, -1, 0, 0)
    PointsOffsetDisable()
end

local FAULT_BEACON_MS = 3000

local function beacon(site)
    if site == 1 then WaitMs(FAULT_BEACON_MS) end
    if site == 4 then WaitMs(FAULT_BEACON_MS) end
    if site == 5 then WaitMs(FAULT_BEACON_MS) end
    if site == 9 then WaitMs(FAULT_BEACON_MS) end
    if site == 10 then WaitMs(FAULT_BEACON_MS) end
    if site == 11 then WaitMs(FAULT_BEACON_MS) end
end

local function ftControlPress(flag)
    return FT_Control(flag, FTC_SENSOR_NUM,
        0, 0, 1, 0, 0, 0,
        -- FT_Control takes signed Fz; compression is negative in this frame.
        -- FT_LinInsertion keeps its threshold positive below.
        0.0, 0.0, -PRESS_TARGET_N, 0.0, 0.0, 0.0,
        FTC_GAIN_P, 0.0, 0.0, 0.0, 0.0, 0.0,
        0, 0,
        PRESS_ADJUST_MM, 0.0,                      -- max_dis (mm), max_ang
        0.0, 0, 0,
        2.0, 2.0, 8.0, 8.0,
        0.2, 0.2, 1.0, 1.0,
        0)
end

local function ftGuardPress(flag)
    if USE_FT_GUARD ~= 1 then return end
    if type(FT_Guard) ~= "function" then return end
    FT_Guard(flag, FT_GUARD_TOOL,
        0, 0, 1, 0, 0, 0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, FORCE_CEILING_N, 0.0, 0.0, 0.0,
        0.0, 0.0, -FORCE_CEILING_N, 0.0, 0.0, 0.0)
end

local function sixOf(v)
    return {v, v, v, v, v, v}
end

local faulting = false
local pressNeedsGuard = PRESS_TARGET_N >= PRESS_GUARD_MIN_N

local function pressCollisionGuard(on)
    local haveCustom = type(CustomCollisionDetectionStart) == "function"
                   and type(CustomCollisionDetectionEnd) == "function"
    local haveLevel  = USE_PRESS_ANTICOLLISION == 1
                   and type(SetAnticollision) == "function"

    if not pressNeedsGuard then
        if not faulting then
            pub(SV_PRESS_GUARD, GUARD_NOT_NEEDED)
        end
        return
    end

    if on ~= 1 then
        if haveCustom then CustomCollisionDetectionEnd() end
        if haveLevel then
            SetAnticollision(BASE_COLL_MODE, sixOf(BASE_COLL_LEVEL), 0)
        end
        if not faulting then
            pub(SV_PRESS_GUARD, GUARD_RELEASED)
        end
        return
    end

    if haveCustom then
        CustomCollisionDetectionStart(PRESS_COLL_FLAG,
            sixOf(PRESS_COLL_JOINT), sixOf(PRESS_COLL_TCP), 0)
    end
    if haveLevel then
        if USE_PRESS_COLL_OFF == 1 then
            SetAnticollision(0, sixOf(PRESS_COLL_OFF_LEVEL), 0)
        else
            SetAnticollision(1, sixOf(PRESS_COLL_PCT), 0)
        end
    end

    if haveCustom and haveLevel then
        pub(SV_PRESS_GUARD, GUARD_BOTH)
    elseif haveCustom then
        pub(SV_PRESS_GUARD, GUARD_CUSTOM)
    elseif haveLevel then
        pub(SV_PRESS_GUARD, GUARD_LEVEL)
    else
        pub(SV_PRESS_GUARD, GUARD_NONE)
        print("[WELD] WARNING: no collision-threshold instruction available — " ..
              "the press runs at the configured level and will likely fault.")
    end
end

local function forceControlOff()
    ftGuardPress(0)
    ftControlPress(0)
    pressCollisionGuard(0)
end

local function fault(msg, site)
    faulting = true
    WELD_FAULT = 1
    SPLCSetDO(DO_WELD, 0)
    forceControlOff()
    pub(SV_PHASE, PH_FAULT_BASE + site)
    departFromStud()
    print("[WELD] FAULT: " .. msg)
    beacon(site)
    return true
end

local function waitForWeldReady()
    if not diCheckEnabled() then
        print("[WELD] DI check off: skipping the DI0 welder-ready wait.")
        return
    end

    local waited = 0
    while readDI(DI_WELD_READY) ~= 1 do
        if waited >= READY_TIMEOUT_MS then
            return fault(string.format("DI%d (caps at charge) never came up within %d ms — welder off, not ready, or unwired",
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
    if type(Z_CLEARANCE) ~= "number" then
        error("[WELD] Z_CLEARANCE not set — the caller must publish the height it parked at")
    end
    if type(Z_RETRACT) ~= "number" then
        error("[WELD] Z_RETRACT not set — the caller must publish the height to lift and feed at")
    end
end


-- =========================================
-- Phase Execution
-- =========================================

local pressZ0 = nil
local weldZ0  = nil

local function searchForStud()
    pub(SV_PHASE, PH_SEARCH)

    local ret = ftCall(FT_FindSurface, FIND_RCS, FIND_DIR, FIND_AXIS,
                       SEARCH_SPEED_MMS, FIND_ACC, SEARCH_MAX_MM, CONTACT_FORCE_N)
    if ftRefused(ret) then
        return fault(string.format("FT_FindSurface refused the approach (code %s), no surface within %.1f mm",
            tostring(ret), SEARCH_MAX_MM), 1)
    end

    pub(SV_PHASE, PH_SEARCH_DONE)

    pressZ0 = readToolZ()
    if pressZ0 ~= nil then
        pub(SV_PRESS_Z0, pressZ0)
    end

    if not diCheckEnabled() then
        print("[WELD] DI check off: skipping the DI1 stud-on-work check.")
        return
    end

    if readDI(DI_STUD_ON_WORK) ~= 1 then
        return fault(string.format("touched a surface but DI%d (stud on work) is not active",
            DI_STUD_ON_WORK), 4)
    end

    print(string.format("[WELD] Contact confirmed, DI%d active.", DI_STUD_ON_WORK))
end

local function pressToForce()
    local holdMs = PRESS_HOLD_MS

    if type(FT_Control) ~= "function" then
        return fault("FT_Control is not available in this controller's Lua", 9)
    end
    if type(FT_LinInsertion) ~= "function" then
        return fault("FT_LinInsertion is not available in this controller's Lua", 9)
    end

    pressCollisionGuard(1)

    print(string.format("[WELD] Press target %.1f lbf (%.1f N); collision guard %s.",
        PRESS_TARGET_LBF, PRESS_TARGET_N,
        pressNeedsGuard and "raised" or "not needed at this force"))
    print(string.format("[WELD] Press gain %.6f; FT_Control alone moves the gun.", FTC_GAIN_P))

    ftGuardPress(1)

    pub(SV_PHASE, PH_PRESS_ON)
    local ret = ftCall(ftControlPress, 1)
    if ftRefused(ret) then
        return fault(string.format("FT_Control refused to start (code %s)", tostring(ret)), 9)
    end

    -- The only insertion in a press; see PRESS_HOLD_MS for why it is not re-run.
    -- With no feed (PRESS_FEED_MMS) it moves nothing itself: FT_Control closes
    -- on the target, and this returns once force reaches the threshold.
    pub(SV_PHASE, PH_PRESS_INSERT)
    ret = ftCall(FT_LinInsertion, FIND_RCS, PRESS_INSERT_THRESHOLD_N,
                 PRESS_FEED_MMS, 0.0, PRESS_MAX_MM, PRESS_DIR)
    if ftRefused(ret) then
        return fault(string.format("FT_LinInsertion refused the press (code %s)", tostring(ret)), 5)
    end

    local zNow = readToolZ()
    if pressZ0 ~= nil and zNow ~= nil then
        local travel = pressZ0 - zNow
        pub(SV_PRESS_TRAVEL, travel)
        print(string.format("[WELD] Press travelled %.2f mm of %.1f mm allowed.",
            travel, PRESS_MAX_MM))
    end

    pub(SV_PHASE, PH_PRESS_HOLD)
    WaitMs(holdMs)
    pub(SV_PHASE, PH_PRESS_HELD)

    -- Lua still cannot read force, but it can read Z again now that the hold is
    -- over. FT_Control keeps regulating through the hold at FTC_GAIN_P — slow on
    -- purpose (see its declaration) — so if insertion stopped short on a
    -- momentary spike (see PRESS_HOLD_MS above), any further advance during the
    -- passive wait is the regulator closing that gap on its own. A reading near
    -- zero either means it was already at target when insertion stopped, or that
    -- the gain is too slow to close a real gap within holdMs; those look
    -- identical from here and still need a live force reading (pendant/FT setup
    -- page) to tell apart.
    local zAfterHold = readToolZ()
    if zNow ~= nil and zAfterHold ~= nil then
        local holdTravel = zNow - zAfterHold
        pub(SV_PRESS_HOLD_TRAVEL, holdTravel)
        print(string.format("[WELD] FT_Control advanced %.3f mm further during the %d ms hold.",
            holdTravel, holdMs))
    end

    print(string.format("[WELD] Force target %.1f lbf held for %d ms; maintaining force for weld.", PRESS_TARGET_LBF, holdMs))
end

local function fireWeld()
    pub(SV_PHASE, PH_WELD)

    -- FT_Control is still running here — nothing has turned it off since
    -- pressToForce() started it, and nothing turns it off until retract()
    -- below. The stud's tip flashes off in milliseconds once the arc strikes,
    -- and the F/T sensor's RS-485 link sits right next to that current pulse,
    -- so a real force-loss step and an EMI-glitched sample look the same from
    -- here: either one hands the still-active regulator a force error to
    -- react to. This is the "before" mark for SV_WELD_JOLT_TRAVEL in
    -- holdAfterWeld() below — diagnostic only, changes nothing about how the
    -- pulse fires.
    weldZ0 = readToolZ()

    if WELD_ARMED ~= 1 then
        print("[WELD] Dry-run: WELD_ARMED is not 1; skipping arc pulse.")
        return
    end

    if diCheckEnabled() then
        local d1 = readDI(DI_STUD_ON_WORK)
        local d0 = readDI(DI_WELD_READY)
        print(string.format("[WELD] Pre-fire check: DI%d (stud_on_work)=%d, DI%d (weld_ready)=%d",
            DI_STUD_ON_WORK, d1, DI_WELD_READY, d0))

        if d1 ~= 1 then
            return fault(string.format("DI%d (stud on work) dropped before the weld pulse", DI_STUD_ON_WORK), 10)
        end

        if d0 ~= 1 then
            return fault(string.format("DI%d (weld ready) dropped before the weld pulse", DI_WELD_READY), 11)
        end
    else
        print("[WELD] DI check off: firing without the DI0/DI1 pre-fire check.")
    end

    print(string.format("[WELD] FIRING ARC: DO%d output set HIGH for %d ms", DO_WELD, WELD_PULSE_MS))
    writeDO(DO_WELD, 1)
    WaitMs(WELD_PULSE_MS)
    writeDO(DO_WELD, 0)
    print(string.format("[WELD] Arc pulse complete: DO%d output set LOW", DO_WELD))
end

local function holdAfterWeld()
    WaitMs(POST_WELD_HOLD_MS)

    -- Diagnostic only, same idea as SV_PRESS_HOLD_TRAVEL: how far the tool
    -- moved from just before the arc to the end of this hold, while
    -- FT_Control was regulating force the whole time. A dry run never fires,
    -- so its number is the sensor's own noise floor to compare a live shot
    -- against.
    local zAfterHold = readToolZ()
    if weldZ0 ~= nil and zAfterHold ~= nil then
        local joltTravel = weldZ0 - zAfterHold
        pub(SV_WELD_JOLT_TRAVEL, joltTravel)
        print(string.format("[WELD] Tool moved %.3f mm from just before the arc to the end of the post-weld hold.",
            joltTravel))
    end
end

local function retract()
    forceControlOff()
    pub(SV_PHASE, PH_RETRACT)
    departFromStud()
end

local function feedNextStud()
    writeDO(DO_FEED, 1)
    WaitMs(FEED_PULSE_MS)
    writeDO(DO_FEED, 0)
end


-- =========================================
-- Master Sequence & Execution Gate
-- =========================================

local function weldOneStud()
    requireContract()
    if WELD_ARMED == nil then WELD_ARMED = 0 end
    if WELD_FAULT == 1 or faulting then return end

    pub(SV_PHASE, PH_ENTER)
    pub(SV_LAST_RET, 0)

    -- Still at the caller's park pose: the reference departFromStud() measures
    -- the head's drift from.
    parkPose = readPose()

    pressZ0 = nil
    pub(SV_PRESS_Z0, 0)
    pub(SV_PRESS_TRAVEL, 0)
    pub(SV_PRESS_HOLD_TRAVEL, 0)
    pub(SV_WELD_JOLT_TRAVEL, 0)
    pub(SV_PRESS_GUARD, GUARD_RELEASED)
    pub(SV_STUD_ON_WORK, -1)
    pub(SV_WELD_READY, -1)
    pub(SV_PRESS_LBF, PRESS_TARGET_LBF)

    writeDO(DO_WELD, 0)

    waitForWeldReady()
    if WELD_FAULT == 1 or faulting then return end

    -- The travel guard from the last stud (or a run before this one) is still on.
    ftGuardOff()
    searchForStud()
    if WELD_FAULT == 1 or faulting then return end

    pressToForce()
    if WELD_FAULT == 1 or faulting then return end


    fireWeld()
    if WELD_FAULT == 1 or faulting then return end

    holdAfterWeld()
    retract()
    feedNextStud()
    ftGuardTravel()
    pub(SV_PHASE, PH_DONE)
end

if WELD_RUN == 1 then
    weldOneStud()
end
