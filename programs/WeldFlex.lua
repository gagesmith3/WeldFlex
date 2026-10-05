-- WeldFlex.lua — canonical stud-weld program.

tool = 2
blend = -1
wobj = 2
offsetEnable = 1
speed = 25 --{{SPEED}}
FEED_PULSE_MS = 250 --{{FEED_PULSE_MS}}
SAFE_Z = 60.0 --{{SAFE_Z}}
RETRACT_Z = 60.0 --{{RETRACT_Z}}
SEARCH_Z = 10.0 --{{SEARCH_Z}}
PART_Z = 0.0 --{{PART_Z}}
PRESS_LBF = 20.0 --{{PRESS_LBF}}
FT_SENSOR_NUM = 1 --{{FT_SENSOR_NUM}}
-- From the Admin page's Weld Tuning: weld.lua's search speed and press feed in
-- mm/s, and FT_Control's press gain. A press feed of 0 is the "Force only" press:
-- FT_Control alone moves the gun and FT_LinInsertion only ends the press on force.
SEARCH_SPEED = 7.5 --{{SEARCH_SPEED}}
PRESS_SPEED = 0 --{{PRESS_SPEED}}
PRESS_GAIN = 0.0001 --{{PRESS_GAIN}}
STUD_TYPE = "M4" --{{STUD_TYPE}}
SUBSTRATE = "Mild Steel" --{{SUBSTRATE}}
BOUNDARY_MS = 1500 --{{BOUNDARY_MS}}

-- weld.lua's run mode (WELD_ARMED, WELD_DI_CHECK), resolved once by
-- lua_builder.RunMode. Published here and never changed below.
--{{RUN_MODE}}

-- Home Position (homewf registered point on controller)
USE_HOME_MOVE = 1

studs = {
--{{STUDS}}
}

--{{CYCLE_COUNT}}

-- The stud the first cycle starts at, counted from 1 as the part designer
-- numbers them. Above 1 it resumes a part whose earlier studs are already
-- welded; every later cycle starts back at stud 1.
START_STUD = 1 --{{START_STUD}}

-- Move to the taught home position. homewf is taught AT the safe height
-- (PART_Z + SAFE_Z above zerozero), so the legs between home and the part
-- are level moves; nothing below offsets homewf. An earlier version lifted
-- to homewf + HIGH_Z, which stacked the safe height on top of a home that
-- already sat there: a full safe height straight up, then back down.
if USE_HOME_MOVE == 1 then
    Lin(homewf, speed, -1, 0, 0)
end

local jobAborted = false
local lastWeldX = nil
local lastWeldY = nil
local firstStud = START_STUD

for cycleIndex = 1, cycleCount do --{{LOOP_START}}
    for studIndex = firstStud, #studs do
        local stud = studs[studIndex]

        -- All three heights are measured up from zerozero's Z in the wobj-2
        -- frame. HIGH_Z is Safe Z, the fixture-clearing plane the legs to and
        -- from homewf travel at. LIFT_Z is Retract Z: weld.lua's retract lifts
        -- there after every stud, the next stud feeds there, and the head
        -- travels there between studs. PARK_Z is the Search Height, where
        -- weld.lua's search starts.
        HIGH_Z = PART_Z + SAFE_Z
        LIFT_Z = PART_Z + RETRACT_Z
        PARK_Z = PART_Z + SEARCH_Z

        -- Publish weld.lua input contract globals
        weldX = stud.x
        weldY = stud.y
        WELD_RUN = 1
        Z_CLEARANCE = PARK_Z
        Z_RETRACT = LIFT_Z
        WELD_PRESS_LBF = stud.pressLbf or PRESS_LBF
        WELD_FT_SENSOR_NUM = FT_SENSOR_NUM
        WELD_SEARCH_SPEED_MMS = SEARCH_SPEED
        WELD_PRESS_SPEED_MMS = PRESS_SPEED
        WELD_PRESS_GAIN = PRESS_GAIN
        WELD_STUD_TYPE = STUD_TYPE
        WELD_SUBSTRATE = SUBSTRATE
        WELD_FEED_PULSE_MS = FEED_PULSE_MS

        -- Z and XY never move together: every move below is straight up,
        -- straight down, or level. The first stud of a cycle is reached level
        -- at HIGH_Z out of homewf, which is taught there. Every later one
        -- starts where weld.lua's retract left the head, straight up off the
        -- previous stud at LIFT_Z, and travels level at that, so the head no
        -- longer climbs all the way to Safe Z between studs. lua_builder
        -- refuses a Retract Z below the Search Height or above Safe Z.
        --
        -- flag=0: offset in the wobj-2 workpiece frame (FR Lua manual §3.2.12),
        -- not flag=1's tool frame — flag=1 rode the torch's current orientation
        -- instead of the taught bed axes, which is why Z looked ignored.
        local travelZ = HIGH_Z
        local travelSpeed = speed
        if lastWeldX ~= nil and lastWeldY ~= nil then
            travelZ = LIFT_Z
            if stud.s2sSpeed ~= nil then
                travelSpeed = stud.s2sSpeed
            end
        end

        PointsOffsetEnable(0, weldX, weldY, travelZ, 0, 0, 0)
        Lin(zerozero, travelSpeed, -1, 0, 0)
        PointsOffsetDisable()

        -- The reload dwell follows a feed, so it is skipped when this stud was
        -- reached from home (a cycle resumed at START_STUD) and not from a weld.
        if lastWeldX ~= nil and stud.s2sWaitMs ~= nil and stud.s2sWaitMs > 0 then
            WaitMs(stud.s2sWaitMs)
        end

        -- Straight down to the Search Height. weld.lua searches down tool Z from
        -- here, then lifts straight up to LIFT_Z from wherever it pressed.
        if travelZ ~= PARK_Z then
            PointsOffsetEnable(0, weldX, weldY, PARK_Z, 0, 0, 0)
            Lin(zerozero, speed, -1, 0, 0)
            PointsOffsetDisable()
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
    firstStud = 1

    -- Clear the part before the next cycle (and on a fault): lift straight up
    -- off the last stud from its retract height to the safe height, then traverse level
    -- into homewf, which is taught at that height. Runs every cycle,
    -- including the last.
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
