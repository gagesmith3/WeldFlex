-- studCycle.lua
-- Fixed base program for WeldFlex.
-- Dynamic stud coordinates are loaded from /fruser/studs_data.lua.

studs = studs or {}

NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()

if studs == nil then
    studs = {}
end

local Z_CLEARANCE = clearance_z_mm or 100  -- mm above zerozero Z level for all transit moves

PointsOffsetEnable(1, 0, 0, Z_CLEARANCE, 0, 0, 0)
PTP(zerozero, 50, -1, 0)
PointsOffsetDisable()

for _, stud in ipairs(studs) do
    PointsOffsetEnable(1, stud.x, stud.y, Z_CLEARANCE, 0, 0, 0)
    PTP(zerozero, 50, -1, 0)
    PointsOffsetDisable()
    WaitMs(1000)
    --NewDofile("/fruser/weld.lua",1,1)
    --DofileEnd()
end

PointsOffsetEnable(1, 0, 0, Z_CLEARANCE, 0, 0, 0)
PTP(zerozero, 50, -1, 0)
PointsOffsetDisable()

