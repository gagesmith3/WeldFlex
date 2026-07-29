# Normal Operation
1) User Selects Part from "Part Library"
2) User is prompted to enter how many "cycles"
3) Job is loaded into WeldFlex Job Manager
4) user hits run
5) job runs completed cycles
6) job completes


# WeldFlex Job Manager
internal job manager that persists job status and robot controls live no matter what page the user is on.

# WeldFlex.lua
Lua script that handles the entire welding process-
1) intaking the x/y coordinate list
2) creating a loop that moves the head into position, runs the weld.lua sub program to actually weld, tracks the completion %, and inputs waits when needed by the user
3) finishes the job by returning to home


# Robot Software (Fairino)
Direct robotic control software owned and developed by fairino, weldflex connects to the robot via its sdk.


# Part Library
List of "parts" created by the user containing the x/y data of the weld location + any user inputted waits.


