-- WeldFlex.lua — canonical stud-weld program.

tool = 10
blend = -1
wobj = 4
offsetEnable = 1
speed = 25 --{{SPEED}}
FEED_PULSE_MS = 250 --{{FEED_PULSE_MS}}
SAFE_Z = 60.0 --{{SAFE_Z}}
RETRACT_Z = 10.0 --{{RETRACT_Z}}
PART_Z = 0.0 --{{PART_Z}}
PRESS_LBF = 20.0 --{{PRESS_LBF}}
FT_SENSOR_NUM = 1 --{{FT_SENSOR_NUM}}
STUD_TYPE = "M4" --{{STUD_TYPE}}
SUBSTRATE = "Mild Steel" --{{SUBSTRATE}}
ARM_MODE = "live" --{{ARM_MODE}}
BOUNDARY_MS = 1500 --{{BOUNDARY_MS}}

-- Home Position (homewf registered point on controller)
USE_HOME_MOVE = 1

studs = {
--{{STUDS}}
}

--{{CYCLE_COUNT}}

-- Move to the taught home position, which is already safe.
if USE_HOME_MOVE == 1 then
    Lin(homewf, speed, -1, 0, 0)
end

local jobAborted = false
local lastWeldX = nil
local lastWeldY = nil

for cycleIndex = 1, cycleCount do --{{LOOP_START}}
    for _, stud in ipairs(studs) do
        -- Publish weld.lua input contract globals
        weldX = stud.x
        weldY = stud.y
        WELD_RUN = 1
        WELD_ARMED = 0
        if ARM_MODE == "live" then
            WELD_ARMED = 1
        end
        WELD_RETRACT_Z = RETRACT_Z
        WELD_PART_Z = PART_Z
        Z_CLEARANCE = PART_Z + RETRACT_Z
        WELD_PRESS_LBF = stud.pressLbf or PRESS_LBF
        WELD_FT_SENSOR_NUM = FT_SENSOR_NUM
        WELD_STUD_TYPE = STUD_TYPE
        WELD_SUBSTRATE = SUBSTRATE
        WELD_FEED_PULSE_MS = FEED_PULSE_MS

        APPROACH_Z = PART_Z + RETRACT_Z
        HIGH_Z = PART_Z + SAFE_Z

        -- The first move leaves home at high clearance. After each weld,
        -- weld.lua has already retracted to APPROACH_Z for the local traverse.
        local travelZ = APPROACH_Z
        if lastWeldX == nil or lastWeldY == nil then
            travelZ = HIGH_Z
        end
        local travelSpeed = speed
        if lastWeldX ~= nil and lastWeldY ~= nil and stud.s2sSpeed ~= nil then
            travelSpeed = stud.s2sSpeed
        end
        -- flag=0: offset in the wobj-4 workpiece frame (FR Lua manual §3.2.12),
        -- not flag=1's tool frame — flag=1 rode the torch's current orientation
        -- instead of the taught bed axes, which is why Z looked ignored.
        PointsOffsetEnable(0, weldX, weldY, travelZ, 0, 0, 0)
        Lin(zerozero, travelSpeed, -1, 0, 0)
        PointsOffsetDisable()

        if travelZ ~= APPROACH_Z then
            -- Descend from high travel clearance into the first stud's approach level.
            PointsOffsetEnable(0, weldX, weldY, APPROACH_Z, 0, 0, 0)
            Lin(zerozero, speed, -1, 0, 0)
            PointsOffsetDisable()
        end

        if stud.s2sWaitMs ~= nil and stud.s2sWaitMs > 0 then
            WaitMs(stud.s2sWaitMs)
        end

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

    -- Clear the part before the next cycle (and on a fault): elevate off the
    -- last stud, then return to the taught home so the operator can swap parts
    -- with the head out of the way. Runs every cycle, including the last.
    if USE_HOME_MOVE == 1 then
        if lastWeldX ~= nil and lastWeldY ~= nil then
            PointsOffsetEnable(0, lastWeldX, lastWeldY, HIGH_Z, 0, 0, 0)
            Lin(zerozero, speed, -1, 0, 0)
            PointsOffsetDisable()
        end
        Lin(homewf, speed, -1, 0, 0)
        lastWeldX = nil
        lastWeldY = nil
    end

    if jobAborted then break end

    WaitMs(BOUNDARY_MS) --{{CYCLE_MARKER}}
    --{{GATE}}
end
