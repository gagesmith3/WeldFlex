-- WeldFlex.lua — canonical stud-weld program.

tool = 10
blend = -1
wobj = 4
offsetEnable = 1
speed = 25 --{{SPEED}}
SAFE_Z = 10.0 --{{SAFE_Z}}
PART_Z = 0.0 --{{PART_Z}}
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

for cycleIndex = 1, cycleCount do --{{LOOP_START}}
    for _, stud in ipairs(studs) do
        -- Publish weld.lua input contract globals
        weldX = stud.x
        weldY = stud.y
        WELD_RUN = 1
        WELD_ARMED = 1
        WELD_SAFE_Z = SAFE_Z
        Z_CLEARANCE = SAFE_Z
        WELD_PART_Z = PART_Z
        WELD_PRESS_LBF = PRESS_LBF
        WELD_STUD_TYPE = STUD_TYPE
        WELD_SUBSTRATE = SUBSTRATE

        -- Approach stud position at safe Z clearance above part
        APPROACH_Z = PART_Z + SAFE_Z
        PointsOffsetEnable(1, weldX, weldY, APPROACH_Z, 0, 0, 0)
        Lin(zerozero, speed, -1, 0, 1)
        PointsOffsetDisable()

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
    -- 1. Traverse at safe Z clearance to homewf XY
    PointsOffsetEnable(1, 0, 0, APPROACH_Z, 0, 0, 0)
    Lin(homewf, speed, -1, 0, 1)
    PointsOffsetDisable()

    -- 2. Descend to homewf
    Lin(homewf, speed, -1, 0, 0)
end
