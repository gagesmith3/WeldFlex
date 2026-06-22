-- =========================================
-- FR16 STUD WELD PROGRAM
-- Work Coordinate System: WObjCoord2
-- =========================================

tool = 10
blend = -1
wobj = 2
offsetEnable = 1


safeZ = -40
downZ = 29.5

-- Initial move
Lin(CenterPoint,10,-1,2,1,0,0,safeZ,0,0,0,0,100,200)

-- =====================================

--feed
WaitMs(1000)
SPLCSetDO(1,1);
WaitMs(1000)
SPLCSetDO(1,0);

-- =========================================
-- move to safe POSITION
-- =========================================

PTP(safeHeight,25,-1,0)












