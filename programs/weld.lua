-- =========================================
-- weld.lua — Weld sub-process for one stud
--
-- Executed per stud by WeldFlex.lua with torch at safe Z clearance.
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
local FEED_PULSE_MS     = 1000

-- ===== Force Config =====
local N_PER_LBF = 4.448222
local CONTACT_FORCE_N = 10.0

local PRESS_TARGET_MAX_LBF = 25.0
local PRESS_TARGET_LBF     = 20.0
if type(WELD_PRESS_LBF) == "number"
   and WELD_PRESS_LBF > 0
   and WELD_PRESS_LBF <= PRESS_TARGET_MAX_LBF then
    PRESS_TARGET_LBF = WELD_PRESS_LBF
end
local PRESS_TARGET_N = PRESS_TARGET_LBF * N_PER_LBF

local FORCE_CEILING_LBF = 25.0
local FORCE_CEILING_N   = FORCE_CEILING_LBF * N_PER_LBF

local USE_FT_GUARD  = 0
local FT_GUARD_TOOL = 10

local PRESS_HOLD_MS = 1000

-- ===== FT_Control & Motion Parameters =====
local FTC_SENSOR_NUM = 1
local FTC_GAIN_P     = 0.005

local READY_TIMEOUT_MS   = 5000
local READY_SAMPLE_MS    = 100

-- FT_FindSurface & FT_LinInsertion parameters
local FIND_RCS  = 0     -- 0 = tool frame, 1 = base frame
local FIND_DIR  = 2     -- 1 = positive, 2 = negative (flipped with TCP Z, 2026-09-01)
local FIND_AXIS = 3     -- 3 = Z axis
local FIND_ACC  = 0.0

local PRESS_DIR = 0     -- 0 = negative (FT_LinInsertion encoding; flipped with TCP Z, 2026-09-01)

local SEARCH_SPEED_MMS = 50.0
local PRESS_SPEED_MMS  = 10.0

local RETRACT_Z_MM = (type(WELD_RETRACT_Z) == "number" and WELD_RETRACT_Z > 0) and WELD_RETRACT_Z or 10.0
local PART_Z_MM = (type(WELD_PART_Z) == "number") and WELD_PART_Z or 0.0
local Z_CLEARANCE = PART_Z_MM + RETRACT_Z_MM
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
local RETRACT_SPEED = 25


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


-- =========================================
-- Helper Functions
-- =========================================

local function moveToZ(zOffset, vel)
    -- flag=0: workpiece frame, matching WeldFlex.lua (see its comment).
    PointsOffsetEnable(0, weldX, weldY, zOffset, 0, 0, 0)
    PTP(zerozero, vel, -1, 0)
    PointsOffsetDisable()
end

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
        flag)
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
    moveToZ(Z_CLEARANCE, RETRACT_SPEED)
    print("[WELD] FAULT: " .. msg)
    beacon(site)
end

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
    if WELD_RETRACT_Z == nil and Z_CLEARANCE == nil then
        error("[WELD] WELD_RETRACT_Z not set — WeldFlex.lua must publish the retract Z")
    end
end


-- =========================================
-- Phase Execution
-- =========================================

local pressZ0 = nil

local function searchForStud()
    pub(SV_PHASE, PH_SEARCH)

    local ret = ftCall(FT_FindSurface, FIND_RCS, FIND_DIR, FIND_AXIS,
                       SEARCH_SPEED_MMS, FIND_ACC, SEARCH_MAX_MM, CONTACT_FORCE_N)
    if ftRefused(ret) then
        fault(string.format("FT_FindSurface refused the approach (code %s), no surface within %.1f mm",
            tostring(ret), SEARCH_MAX_MM), 1)
    end

    pub(SV_PHASE, PH_SEARCH_DONE)

    pressZ0 = readToolZ()
    if pressZ0 ~= nil then
        pub(SV_PRESS_Z0, pressZ0)
    end

    if readDI(DI_STUD_ON_WORK) ~= 1 then
        fault(string.format("touched a surface but DI%d (stud on work) is not active",
            DI_STUD_ON_WORK), 4)
    end

    print(string.format("[WELD] Contact confirmed, DI%d active.", DI_STUD_ON_WORK))
end

local function pressToForce()
    local holdMs = PRESS_HOLD_MS

    if type(FT_Control) ~= "function" then
        fault("FT_Control is not available in this controller's Lua", 9)
    end
    if type(FT_LinInsertion) ~= "function" then
        fault("FT_LinInsertion is not available in this controller's Lua", 9)
    end

    pressCollisionGuard(1)

    print(string.format("[WELD] Press target %.1f lbf (%.1f N); collision guard %s.",
        PRESS_TARGET_LBF, PRESS_TARGET_N,
        pressNeedsGuard and "raised" or "not needed at this force"))

    ftGuardPress(1)

    pub(SV_PHASE, PH_PRESS_ON)
    local ret = ftCall(ftControlPress, 1)
    if ftRefused(ret) then
        fault(string.format("FT_Control refused to start (code %s)", tostring(ret)), 9)
    end

    pub(SV_PHASE, PH_PRESS_INSERT)
    ret = ftCall(FT_LinInsertion, FIND_RCS, PRESS_TARGET_N,
                 PRESS_SPEED_MMS, 0.0, PRESS_MAX_MM, PRESS_DIR)
    if ret ~= nil and type(ret) == "number" and ret < 0 then
        fault(string.format("FT_LinInsertion refused the press (code %s)", tostring(ret)), 5)
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

    print(string.format("[WELD] Force target %.1f lbf held for %d ms; maintaining force for weld.", PRESS_TARGET_LBF, holdMs))
end

local function fireWeld()
    pub(SV_PHASE, PH_WELD)

    local d1 = readDI(DI_STUD_ON_WORK)
    local d0 = readDI(DI_WELD_READY)
    print(string.format("[WELD] Pre-fire check: DI%d (stud_on_work)=%d, DI%d (weld_ready)=%d",
        DI_STUD_ON_WORK, d1, DI_WELD_READY, d0))

    if d1 ~= 1 then
        fault(string.format("DI%d (stud on work) dropped before the weld pulse", DI_STUD_ON_WORK), 10)
    end

    if d0 ~= 1 then
        fault(string.format("DI%d (weld ready) dropped before the weld pulse", DI_WELD_READY), 11)
    end

    if WELD_ARMED ~= 1 then
        print("[WELD] Dry-run: WELD_ARMED is not 1; skipping arc pulse.")
        return
    end

    print(string.format("[WELD] FIRING ARC: DO%d output set HIGH for %d ms", DO_WELD, WELD_PULSE_MS))
    writeDO(DO_WELD, 1)
    WaitMs(WELD_PULSE_MS)
    writeDO(DO_WELD, 0)
    print(string.format("[WELD] Arc pulse complete: DO%d output set LOW", DO_WELD))
end

local function holdAfterWeld()
    WaitMs(POST_WELD_HOLD_MS)
end

local function retract()
    forceControlOff()
    pub(SV_PHASE, PH_RETRACT)
    moveToZ(Z_CLEARANCE, RETRACT_SPEED)
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

    pressZ0 = nil
    pub(SV_PRESS_Z0, 0)
    pub(SV_PRESS_TRAVEL, 0)
    pub(SV_PRESS_GUARD, GUARD_RELEASED)
    pub(SV_STUD_ON_WORK, -1)
    pub(SV_WELD_READY, -1)
    pub(SV_PRESS_LBF, PRESS_TARGET_LBF)

    writeDO(DO_WELD, 0)

    waitForWeldReady()
    if WELD_FAULT == 1 or faulting then return end

    searchForStud()
    if WELD_FAULT == 1 or faulting then return end

    pressToForce()
    if WELD_FAULT == 1 or faulting then return end


    fireWeld()
    if WELD_FAULT == 1 or faulting then return end

    holdAfterWeld()
    retract()
    if WELD_SKIP_FEED ~= 1 then
        feedNextStud()
    end
    pub(SV_PHASE, PH_DONE)
end

if WELD_RUN == 1 then
    weldOneStud()
end
