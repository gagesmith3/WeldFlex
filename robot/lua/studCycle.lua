-- studCycle.lua
-- Fixed base program for WeldFlex.
-- Dynamic stud coordinates and clearance height are loaded from /fruser/studs_data.lua.

studs = studs or {}

NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()

if studs == nil then
    studs = {}
end

-- Move to work origin before cycling
PTP(zerozeroJoints, 50, -1, 0)

for _, stud in ipairs(studs) do
    if stud.joints ~= nil then
        PTP(stud.joints, 50, -1, 0)
    end
    -- Weld cycle runs at stud target
    --NewDofile("/fruser/weld.lua",1,1)
    --DofileEnd()
end

-- Return to work origin
PTP(zerozeroJoints, 50, -1, 0)
