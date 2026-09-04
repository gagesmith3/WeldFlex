-- weld_faceplate.lua — single-point maintenance weld for shop fixture faceplates.
--
-- Unlike WeldFlex.lua this program targets one fixed point and never
-- approaches homewf before the first cycle — it goes straight to the target
-- and stays there for the whole run. It returns home exactly once, after the
-- last cycle (see the bottom of the file), not between cycles. Built by
-- backend/lua_builder.py's build_weld_faceplate_lua(); never edit the copy on the controller.

tool = 10
blend = -1
wobj = 4
offsetEnable = 1
speed = 25 --{{SPEED}}
SAFE_Z = 10.0 --{{SAFE_Z}}
PART_Z = 0.0 --{{PART_Z}}
PRESS_LBF = 20.0 --{{PRESS_LBF}}
FT_SENSOR_NUM = 1 --{{FT_SENSOR_NUM}}
STUD_TYPE = "M4" --{{STUD_TYPE}}
SUBSTRATE = "Mild Steel" --{{SUBSTRATE}}
ARM_MODE = "live" --{{ARM_MODE}}
BOUNDARY_MS = 1500 --{{BOUNDARY_MS}}

-- Home Position (homewf registered point on controller) — disabled for
-- faceplate welds; operator uses the dedicated Move Home button instead.
USE_HOME_MOVE = 0

faceplateX = 0.0 --{{FACEPLATE_X}}
faceplateY = 0.0 --{{FACEPLATE_Y}}

--{{CYCLE_COUNT}}

for cycleIndex = 1, cycleCount do --{{LOOP_START}}
    -- Publish weld.lua's input contract globals.
    weldX = faceplateX
    weldY = faceplateY
    WELD_RUN = 1
    WELD_ARMED = 0
    if ARM_MODE == "live" then
        WELD_ARMED = 1
    end
    WELD_SAFE_Z = SAFE_Z
    Z_CLEARANCE = PART_Z + SAFE_Z
    WELD_PART_Z = PART_Z
    WELD_PRESS_LBF = PRESS_LBF
    WELD_FT_SENSOR_NUM = FT_SENSOR_NUM
    WELD_STUD_TYPE = STUD_TYPE
    WELD_SUBSTRATE = SUBSTRATE

    APPROACH_Z = PART_Z + SAFE_Z

    -- Move to the fixed target.
    PointsOffsetEnable(0, faceplateX, faceplateY, APPROACH_Z, 0, 0, 0)
    PTP(zerozero, speed, -1, 0)
    PointsOffsetDisable()

    -- Execute single-stud weld sequence (search, press, weld, hold, retract).
    WELD_FAULT = 0
    NewDofile("/fruser/weld.lua", 1, 1)
    DofileEnd()

    if WELD_FAULT == 1 then
        print("[FACEPLATE] weld faulted — stopping.")
        break
    end

    WaitMs(BOUNDARY_MS) --{{CYCLE_MARKER}}
    --{{GATE}}
end

-- =========================================
-- Return to Home Sequence — runs once, after every cycle is done (including
-- a fault-break out of the loop above), never between cycles.
-- =========================================
if USE_HOME_MOVE == 1 then
    APPROACH_Z = PART_Z + SAFE_Z

    -- 1. Elevate straight up from the faceplate target to safe Z clearance.
    PointsOffsetEnable(0, faceplateX, faceplateY, APPROACH_Z, 0, 0, 0)
    PTP(zerozero, speed, -1, 0)
    PointsOffsetDisable()

    -- 2. Traverse at safe Z clearance to homewf XY.
    PointsOffsetEnable(0, 0, 0, APPROACH_Z, 0, 0, 0)
    PTP(homewf, speed, -1, 0)
    PointsOffsetDisable()

    -- 3. Descend to homewf.
    PTP(homewf, speed, -1, 0)
end

