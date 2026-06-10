-- studCycle.lua
-- Fixed base program for WeldFlex.
-- Dynamic stud coordinates and clearance height are loaded from /fruser/studs_data.lua.

studs = studs or {}

NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()

if studs == nil then
    studs = {}
end

local originWF = {
    zerozeroWF[1],
    zerozeroWF[2],
    zerozeroWF[3] + (clearance_z_mm or 50.0),
    zerozeroWF[4],
    zerozeroWF[5],
    zerozeroWF[6],
}

-- Move to work origin at clearance
Lin(originWF, 50, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0, 0)

for _, stud in ipairs(studs) do
    if stud.wf ~= nil then
        Lin(stud.wf, 50, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0, 0)
    end
    -- Weld cycle runs at stud target
    --NewDofile("/fruser/weld.lua",1,1)
    --DofileEnd()
end

-- Return to work origin at clearance
Lin(originWF, 50, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0, 0)
