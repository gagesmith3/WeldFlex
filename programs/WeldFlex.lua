-- WeldFlex.lua — canonical stud-weld program.
--
-- The copy in this repo is the source template; backend/lua_builder.py fills it
-- in and uploads the result to the controller as /fruser/WeldFlex.lua, replacing
-- it on every run. Never edit the copy on the controller — edit this file, which
-- is the only place motion parameters live.
--
-- As checked in it is valid Lua: a zero-stud, one-cycle no-op. That is
-- deliberate, so the template can be read and syntax-checked on its own.
--
-- Six marker lines are substituted at build time. STUDS becomes one row per
-- stud, CYCLE_COUNT becomes the cycle total, BOUNDARY_MS becomes the length of
-- the cycle-boundary dwell, GATE becomes the inter-cycle gate, and LOOP_START /
-- CYCLE_MARKER are position markers whose line numbers in the *generated* file
-- are what the job manager counts cycles against. Moving a marker is safe;
-- deleting one is not.

-- Frame selection. `tool` is a SetToolCoord slot id (1-15) and `wobj` a
-- SetWObjCoord slot id -- neither is a speed. Don't repurpose them.
tool = 10
blend = -1
wobj = 4
offsetEnable = 1
speed = 25
Z_CLEARANCE = 10

-- Length of the cycle-boundary dwell. In `pause` gate mode the host has to land a
-- ProgramPause inside this window, so the builder makes it longer there.
BOUNDARY_MS = 1500 --{{BOUNDARY_MS}}

studs = {
--{{STUDS}}
}

--{{CYCLE_COUNT}}

for cycleIndex = 1, cycleCount do --{{LOOP_START}}
    for _, stud in ipairs(studs) do
        PointsOffsetEnable(1, stud.x, stud.y, Z_CLEARANCE, 0, 0, 0)
        PTP(zerozero, speed, -1, 0)
        PointsOffsetDisable()
        WaitMs(1000)
    end
    -- Cycle boundary. The dwell is long enough that the manager's 250 ms poll
    -- cannot step over this line, which is what makes the cycle count exact. It is
    -- the only thing that banks a cycle, so missing it entirely stalls the count.
    WaitMs(BOUNDARY_MS) --{{CYCLE_MARKER}}
--{{GATE}}
end
