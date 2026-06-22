-- =========================================
-- FR16 STUD WELD REPEAT PROGRAM
-- Repeats weld cycle 400 times
-- =========================================

speed = 10
blend = -1
wobj = 2
offsetEnable = 1

cycleCount = 50

-- =========================================
PTP(LIBERTYTEST2,speed,-1,0)
-- =========================================
for i = 1, cycleCount do
PTP(LIBERTYTEST1,speed,-1,0)
WaitMs(500)
SPLCSetDO(0,1)
WaitMs(100)
SPLCSetDO(0,0)
WaitMs(1000)
PTP(LIBERTYTEST2,speed,-1,0)
WaitMs(500)
end
-- =========================================
PTP(LIBERTYTEST2,speed,-1,0)