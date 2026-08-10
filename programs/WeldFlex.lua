-- WeldFlex.lua — canonical stud-weld program.

tool = 10
blend = -1
wobj = 4
offsetEnable = 1
speed = 25 --{{SPEED}}
SAFE_Z = 10.0 --{{SAFE_Z}}
PART_Z = 0.0 --{{PART_Z}}
HIGH_Z_CLEARANCE = 50.0 --{{HIGH_Z}}
PRESS_LBF = 20.0 --{{PRESS_LBF}}
STUD_TYPE = "M4" --{{STUD_TYPE}}
SUBSTRATE = "Mild Steel" --{{SUBSTRATE}}
BOUNDARY_MS = 1500 --{{BOUNDARY_MS}}

-- Home Position (homewf registered point on controller)
USE_HOME_MOVE = 1

studs = {
--{{STUDS}}
}

--{{CYCLE_COUNT}}

-- Move to starting home position (elevate to safe Z first)
if USE_HOME_MOVE == 1 then
    APPROACH_Z = PART_Z + SAFE_Z
    PointsOffsetEnable(1, 0, 0, APPROACH_Z, 0, 0, 0)
    Lin(homewf, speed, -1, 0, 1)
    PointsOffsetDisable()
end

local jobAborted = false
local lastWeldX = nil
local lastWeldY = nil

for cycleIndex = 1, cycleCount do --{{LOOP_START}}
    for _, stud in ipairs(studs) do
        -- Publish weld.lua input contract globals
        weldX = stud.y
        weldY = stud.x
        WELD_RUN = 1
        WELD_ARMED = 1
        WELD_SAFE_Z = SAFE_Z
        Z_CLEARANCE = SAFE_Z
        WELD_PART_Z = PART_Z
        WELD_PRESS_LBF = stud.pressLbf or PRESS_LBF
        WELD_STUD_TYPE = STUD_TYPE
        WELD_SUBSTRATE = SUBSTRATE

        APPROACH_Z = PART_Z + SAFE_Z
        HIGH_Z = APPROACH_Z + HIGH_Z_CLEARANCE

        -- 1. If coming from a previous stud, elevate straight UP to high Z level first
        if lastWeldX ~= nil and lastWeldY ~= nil then
            PointsOffsetEnable(1, lastWeldX, lastWeldY, HIGH_Z, 0, 0, 0)
            Lin(zerozero, speed, -1, 0, 1)
            PointsOffsetDisable()
        end

        -- 2. Traverse horizontally in X/Y to stud position at high Z level
        PointsOffsetEnable(1, weldX, weldY, HIGH_Z, 0, 0, 0)
        Lin(zerozero, speed, -1, 0, 1)
        PointsOffsetDisable()

        -- 3. Move vertically down into place Z level (APPROACH_Z) over stud
        PointsOffsetEnable(1, weldX, weldY, APPROACH_Z, 0, 0, 0)
        Lin(zerozero, speed, -1, 0, 1)
        PointsOffsetDisable()

        lastWeldX = weldX
        lastWeldY = weldY

        -- Execute single-stud weld sequence (search, press, weld, hold, retract, feed)
        WELD_FAULT = 0
        NewDofile("/fruser/weld.lua", 1, 1)
        DofileEnd()

        if WELD_FAULT == 1 then
            print("[WELDFLEX] Surface search/weld faulted — returning to home without firing.")
            jobAborted = true
            break
        end
    end

    if jobAborted then break end

    WaitMs(BOUNDARY_MS) --{{CYCLE_MARKER}}
    --{{GATE}}
end

-- =========================================
-- Return to Home Sequence
-- =========================================
if USE_HOME_MOVE == 1 then
    APPROACH_Z = PART_Z + SAFE_Z
    HIGH_Z = APPROACH_Z + HIGH_Z_CLEARANCE

    -- 1. Elevate straight UP to high Z level at current position if returning from a stud
    if lastWeldX ~= nil and lastWeldY ~= nil then
        PointsOffsetEnable(1, lastWeldX, lastWeldY, HIGH_Z, 0, 0, 0)
        Lin(zerozero, speed, -1, 0, 1)
        PointsOffsetDisable()
    end

    -- 2. Traverse at safe Z clearance to homewf XY
    PointsOffsetEnable(1, 0, 0, APPROACH_Z, 0, 0, 0)
    Lin(homewf, speed, -1, 0, 1)
    PointsOffsetDisable()

    -- 3. Descend to homewf
    Lin(homewf, speed, -1, 0, 0)
end
