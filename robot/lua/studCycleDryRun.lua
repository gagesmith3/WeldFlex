-- Dry-run: traces all stud positions without triggering the welder.
-- Used during calibration to verify robot reach and stud offsets.
NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()
PTP(zerozero, 30, -1, 0)
for _, stud in ipairs(studs) do
  PointsOffsetEnable(1, stud.x, stud.y, 0, 0, 0, 0)
  PTP(zerozero, 30, -1, 0)
  PointsOffsetDisable()
  WaitMs(300)
end
PTP(zerozero, 30, -1, 0)
