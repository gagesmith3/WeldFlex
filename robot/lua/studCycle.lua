-- studCycle.lua
-- Fixed base program for WeldFlex.
-- Dynamic stud coordinates and clearance height are loaded from /fruser/studs_data.lua.

studs = studs or {}
clearance_z_mm = clearance_z_mm or 50.0

NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()

if studs == nil then
    studs = {}
end

-- Move to work origin at clearance height before cycling
PointsOffsetEnable(1, 0, 0, clearance_z_mm, 0, 0, 0)
PTP(zerozeroWF, 50, -1, 0)
PointsOffsetDisable()

for _, stud in ipairs(studs) do
    -- Travel to stud XY at clearance height
    PointsOffsetEnable(1, stud.x, stud.y, clearance_z_mm, 0, 0, 0)
    PTP(zerozeroWF, 50, -1, 0)
    -- Weld cycle runs with offset active: weld.lua descends to surface and retracts
    --NewDofile("/fruser/weld.lua",1,1)
    --DofileEnd()
    PointsOffsetDisable()
end

-- Return to work origin at clearance height
PointsOffsetEnable(1, 0, 0, clearance_z_mm, 0, 0, 0)
PTP(zerozeroWF, 50, -1, 0)
PointsOffsetDisable()
