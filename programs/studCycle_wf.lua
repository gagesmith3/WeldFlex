-- studCycle.lua
-- Fixed base program for WeldFlex.
-- Dynamic stud coordinates are loaded from /fruser/studs_data.lua.

studs = studs or {}

NewDofile("/fruser/studs_data_wf.lua",1,1)
DofileEnd()

if studs == nil then
    studs = {}
end


for _, stud in ipairs(studs) do
    PointsOffsetEnable(1, stud.x, stud.y, Z_CLEARANCE, 0, 0, 0)
    PTP(zerozero, 50, -1, 0)
    PointsOffsetDisable()
    WaitMs(1000)
    --NewDofile("/fruser/weld_wf.lua",1,1)
    --DofileEnd()
end


