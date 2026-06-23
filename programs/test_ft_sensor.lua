-- =========================================
-- FORCE SENSOR - FIND SURFACE TEST
-- Moves along tool Z+ until contact detected
--
-- ASSUMES: sensor already active and zeroed.
-- Run from the teach pendant or WeldFlex upload.
-- =========================================

-- FT_FindSurface controller-native param order (differs from Python SDK docs):
--   FT_FindSurface(rcs, dir, axis, lin_v, lin_a, disMax, ft)
--     rcs    0=tool frame  1=base frame
--     dir    0=negative    1=positive
--     axis   1=X  2=Y  3=Z  (1-indexed in controller Lua, unlike Python SDK 0-indexed docs)
--     lin_v  approach speed (mm/s)
--     lin_a  acceleration (mm/s^2)
--     disMax max probe travel (mm)
--     ft     contact force threshold (N)

local N_TO_LBS        = 0.224809
local CONTACT_FORCE_N = 10.0    -- N to trigger stop (~2.2 lbs)
local MAX_DIST_MM     = 50.0    -- mm to travel before giving up
local SPEED_MM_S      = 3.0     -- slow for first test

local err = FT_FindSurface(0, 1, 3, SPEED_MM_S, 0.0, MAX_DIST_MM, CONTACT_FORCE_N)

if err == 0 then
    print("[FT] Surface found!")
    -- Read force at contact point
    local rerr, fx, fy, fz, mx, my, mz = FT_GetForceTorqueRCS()
    if rerr == 0 then
        local f_mag = math.sqrt(fx*fx + fy*fy + fz*fz)
        print(string.format("[FT] Fx=%.2fN  Fy=%.2fN  Fz=%.2fN", fx, fy, fz))
        print(string.format("[FT] Total force: %.2f N  =  %.3f lbs", f_mag, f_mag * N_TO_LBS))
    else
        print(string.format("[FT] Could not read force after contact (code %d)", rerr))
    end
else
    print(string.format("[FT] FT_FindSurface returned error code %d", err))
    print("[FT] Possible causes:")
    print("[FT]   - Sensor not active (run Initialize from Force Sensor page first)")
    print("[FT]   - No surface within " .. MAX_DIST_MM .. " mm")
    print("[FT]   - Contact threshold too high")
end

print("[FT] Done.")


