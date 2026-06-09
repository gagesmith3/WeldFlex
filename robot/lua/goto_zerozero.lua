



-- goto_zerozero.lua
-- Minimal helper: joint-space PTP to calibrated zerozero position.
NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()
PTP(zerozeroJoints, 50, -1, 0)
