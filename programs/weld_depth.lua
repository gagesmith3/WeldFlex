-- =========================================
-- weld_depth.lua — Fixed-Z weld sub-process for one stud
--
-- The fixed_z alternative to weld.lua, chosen per recipe by its Depth Mode.
-- Executed per stud by WeldFlex.lua with the torch parked over the stud at the
-- caller's Z_CLEARANCE. No force sensor: the head plunges by position to
-- WELD_DEPTH_Z (wobj-2, already PART_Z + Weld Z) and fires there.
-- Sequence: PLUNGE -> WELD -> HOLD -> RETRACT -> FEED
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

local READY_TIMEOUT_MS   = 5000
local READY_SAMPLE_MS    = 100

-- ===== Motion =====
-- Lin percentages, capped again by the pendant's Auto Speed.
local PLUNGE_SPEED  = 5
local RETRACT_SPEED = 5

-- Settles the gun spring before firing; weld.lua's force hold does the same job.
local PLUNGE_SETTLE_MS = 1000

-- Mirrors lua_builder.WELD_Z_MIN; the builder refuses deeper, this is the backstop.
local WELD_Z_MIN = -10.0

local LIFT_MAX_DRIFT_MM = 10.0

-- ===== Collision Guard Settings =====
-- Always raised for the plunge: the gun spring loads the joints like a press does.
local PRESS_COLL_FLAG  = 3
local PRESS_COLL_JOINT = 500
local PRESS_COLL_TCP   = 1000

local USE_PRESS_ANTICOLLISION = 1
local PRESS_COLL_PCT   = 100
local BASE_COLL_MODE   = 0
local BASE_COLL_LEVEL  = 3

local USE_PRESS_COLL_OFF   = 1
local PRESS_COLL_OFF_LEVEL = 10


-- =========================================
-- Telemetry Definitions (shared with weld.lua)
-- =========================================

local PH_ENTER        = 10
local PH_PLUNGE       = 35
local PH_SETTLED      = 36
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
local GUARD_NONE       = 9

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

-- Only an explicit 0 turns the DI0/DI1 checks off.
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

-- Straight up the workpiece Z from wherever the head is; see weld.lua's departFromStud().
local parkPose = nil

local function departFromStud()
    local liftX, liftY = weldX, weldY
    local now = readPose()
    if parkPose ~= nil and now ~= nil then
        local dx = now[1] - parkPose[1]
        local dy = now[2] - parkPose[2]
        if dx * dx + dy * dy <= LIFT_MAX_DRIFT_MM * LIFT_MAX_DRIFT_MM then
            liftX = weldX + dx
            liftY = weldY + dy
        else
            print(string.format("[WELD] WARNING: head reads %.1f, %.1f mm off the park pose; " ..
                "retracing the descent instead of lifting straight up.", dx, dy))
        end
    end

    PointsOffsetEnable(0, liftX, liftY, Z_CLEARANCE, 0, 0, 0)
    Lin(zerozero, RETRACT_SPEED, -1, 0, 0)
    PointsOffsetDisable()
end

local FAULT_BEACON_MS = 3000

local function sixOf(v)
    return {v, v, v, v, v, v}
end

local faulting = false

local function plungeCollisionGuard(on)
    local haveCustom = type(CustomCollisionDetectionStart) == "function"
                   and type(CustomCollisionDetectionEnd) == "function"
    local haveLevel  = USE_PRESS_ANTICOLLISION == 1
                   and type(SetAnticollision) == "function"

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
              "the plunge runs at the configured level and may fault.")
    end
end

local function fault(msg, site)
    faulting = true
    WELD_FAULT = 1
    SPLCSetDO(DO_WELD, 0)
    plungeCollisionGuard(0)
    pub(SV_PHASE, PH_FAULT_BASE + site)
    departFromStud()
    print("[WELD] FAULT: " .. msg)
    WaitMs(FAULT_BEACON_MS)
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
end

local function requireContract()
    if weldX == nil or weldY == nil then
        error("[WELD] weldX/weldY not set — WeldFlex.lua must publish the stud offset")
    end
    if type(Z_CLEARANCE) ~= "number" then
        error("[WELD] Z_CLEARANCE not set — the caller must publish the height it parked at")
    end
    if type(WELD_DEPTH_Z) ~= "number" then
        error("[WELD] WELD_DEPTH_Z not set — WeldFlex.lua must publish the weld depth")
    end
end


-- =========================================
-- Phase Execution
-- =========================================

local weldZ0 = nil

local function plungeToDepth()
    if WELD_DEPTH_Z >= Z_CLEARANCE then
        return fault(string.format("weld depth %.2f mm is not below the park height %.2f mm",
            WELD_DEPTH_Z, Z_CLEARANCE), 6)
    end
    if type(PART_Z) == "number" and WELD_DEPTH_Z - PART_Z < WELD_Z_MIN then
        return fault(string.format("weld depth %.2f mm is more than %.1f mm below the part surface",
            WELD_DEPTH_Z, -WELD_Z_MIN), 6)
    end

    plungeCollisionGuard(1)

    local z0 = readToolZ()
    if z0 ~= nil then pub(SV_PRESS_Z0, z0) end

    pub(SV_PHASE, PH_PLUNGE)
    print(string.format("[WELD] Plunging to Z %.2f mm (from %.2f mm).", WELD_DEPTH_Z, Z_CLEARANCE))
    PointsOffsetEnable(0, weldX, weldY, WELD_DEPTH_Z, 0, 0, 0)
    Lin(zerozero, PLUNGE_SPEED, -1, 0, 0)
    PointsOffsetDisable()

    local zNow = readToolZ()
    if z0 ~= nil and zNow ~= nil then
        pub(SV_PRESS_TRAVEL, z0 - zNow)
    else
        pub(SV_PRESS_TRAVEL, Z_CLEARANCE - WELD_DEPTH_Z)
    end

    WaitMs(PLUNGE_SETTLE_MS)
    pub(SV_PHASE, PH_SETTLED)

    if not diCheckEnabled() then
        print("[WELD] DI check off: skipping the DI1 stud-on-work check.")
        return
    end

    if readDI(DI_STUD_ON_WORK) ~= 1 then
        return fault(string.format("reached weld depth but DI%d (stud on work) is not active",
            DI_STUD_ON_WORK), 4)
    end
end

local function fireWeld()
    pub(SV_PHASE, PH_WELD)
    weldZ0 = readToolZ()

    if WELD_ARMED ~= 1 then
        print("[WELD] Dry-run: WELD_ARMED is not 1; skipping arc pulse.")
        return
    end

    if diCheckEnabled() then
        local d1 = readDI(DI_STUD_ON_WORK)
        local d0 = readDI(DI_WELD_READY)

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
end

local function holdAfterWeld()
    WaitMs(POST_WELD_HOLD_MS)

    local zAfterHold = readToolZ()
    if weldZ0 ~= nil and zAfterHold ~= nil then
        pub(SV_WELD_JOLT_TRAVEL, weldZ0 - zAfterHold)
    end
end

local function retract()
    plungeCollisionGuard(0)
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

    parkPose = readPose()

    pub(SV_PRESS_Z0, 0)
    pub(SV_PRESS_TRAVEL, 0)
    pub(SV_PRESS_HOLD_TRAVEL, 0)
    pub(SV_WELD_JOLT_TRAVEL, 0)
    pub(SV_PRESS_GUARD, GUARD_RELEASED)
    pub(SV_STUD_ON_WORK, -1)
    pub(SV_WELD_READY, -1)
    pub(SV_PRESS_LBF, 0)

    writeDO(DO_WELD, 0)

    waitForWeldReady()
    if WELD_FAULT == 1 or faulting then return end

    plungeToDepth()
    if WELD_FAULT == 1 or faulting then return end

    fireWeld()
    if WELD_FAULT == 1 or faulting then return end

    holdAfterWeld()
    retract()
    feedNextStud()
    pub(SV_PHASE, PH_DONE)
end

if WELD_RUN == 1 then
    weldOneStud()
end
