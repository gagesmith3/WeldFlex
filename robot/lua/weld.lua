-- weld.lua
-- Placeholder weld cycle: moves head down then back up via linear moves.
-- TODO: Replace with digital IO sequence to trigger stud welder hardware.
Lin(CurrentPos,10,-1,0,1,0,0,-1,0,0,0,0,100,200)
Lin(CurrentPos,10,-1,0,1,0,0,1,0,0,0,0,100,200)
