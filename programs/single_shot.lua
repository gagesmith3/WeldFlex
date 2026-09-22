-- single_shot.lua — one weld at a fixed target, from the Admin page's Single Shot tool.
--
-- Goes straight to the saved target at safe height (no homewf approach), runs
-- weld.lua's full sequence once — search, press, weld, hold, retract, feed —
-- and stays parked over the target. The page's Move Home button returns it;
-- this program never does. Built by backend/lua_builder.py's
-- build_single_shot_lua(); never edit the copy on the controller.

tool = 2
blend = -1
wobj = 2
offsetEnable = 1
speed = 25 --{{SPEED}}
FEED_PULSE_MS = 250 --{{FEED_PULSE_MS}}
SAFE_Z = 10.0 --{{SAFE_Z}}
PART_Z = 0.0 --{{PART_Z}}
PRESS_LBF = 20.0 --{{PRESS_LBF}}
FT_SENSOR_NUM = 1 --{{FT_SENSOR_NUM}}
STUD_TYPE = "M4" --{{STUD_TYPE}}
SUBSTRATE = "Mild Steel" --{{SUBSTRATE}}
BOUNDARY_MS = 1500 --{{BOUNDARY_MS}}

-- weld.lua's run mode (WELD_ARMED, WELD_DI_CHECK), resolved once by
-- lua_builder.RunMode. Published here and never changed below.
--{{RUN_MODE}}

targetX = 0.0 --{{TARGET_X}}
targetY = 0.0 --{{TARGET_Y}}

--{{CYCLE_COUNT}}

-- The page always builds one cycle. The loop stays so the job manager's cycle
-- tracking and gate handling work exactly as they do for WeldFlex.lua.
for cycleIndex = 1, cycleCount do --{{LOOP_START}}
    -- Publish weld.lua's input contract globals.
    weldX = targetX
    weldY = targetY
    WELD_RUN = 1
    WELD_SAFE_Z = SAFE_Z
    Z_CLEARANCE = PART_Z + SAFE_Z
    WELD_PART_Z = PART_Z
    WELD_PRESS_LBF = PRESS_LBF
    WELD_FT_SENSOR_NUM = FT_SENSOR_NUM
    WELD_STUD_TYPE = STUD_TYPE
    WELD_SUBSTRATE = SUBSTRATE
    WELD_FEED_PULSE_MS = FEED_PULSE_MS

    APPROACH_Z = PART_Z + SAFE_Z

    -- Move to the target at safe height.
    PointsOffsetEnable(0, targetX, targetY, APPROACH_Z, 0, 0, 0)
    PTP(zerozero, speed, -1, 0)
    PointsOffsetDisable()

    -- Execute single-stud weld sequence (search, press, weld, hold, retract, feed).
    WELD_FAULT = 0
    NewDofile("/fruser/weld.lua", 1, 1)
    DofileEnd()

    if WELD_FAULT == 1 then
        print("[SINGLE SHOT] weld faulted — stopping.")
        break
    end

    WaitMs(BOUNDARY_MS) --{{CYCLE_MARKER}}
    --{{GATE}}
end
