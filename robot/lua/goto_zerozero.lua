



-- goto_zerozero.lua
-- Minimal helper: one P2P move to calibrated zerozero.
NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()
PTP(zerozeroWF, 50, -1, 0)
