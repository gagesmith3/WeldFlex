-- =========================================
-- io_monitor.lua — live DI readout, and nothing else.
--
-- Reads the two weld interlock inputs in a loop and publishes their levels to
-- the same system variables weld.lua uses, so the Weld Test page's input tiles
-- go live without a weld test having to run.
--
-- ISSUES NO MOTION. Touches no force instruction. Never writes a DO. This file
-- exists precisely so that checking a wire does not require moving the arm —
-- the only way to see either interlock used to be starting a weld test, which is
-- a lot of machine to move to answer "which pin is the stud circuit on".
--
-- -----------------------------------------------------------------------------
-- Why this file exists at all
-- -----------------------------------------------------------------------------
-- The host cannot read these inputs itself. Both routes are dead on this
-- firmware:
--
--   * The SDK's GetDI wrapper reads the robot_state_pkg cache, which CNDE never
--     fills on this FR-16.
--   * The raw XML-RPC GetDI bypass was live-disproven 2026-07-28 — both weld
--     interlock DIs read "not readable" — and it can hold the single XML-RPC
--     worker until its socket timeout, starving every other signal on the page.
--
-- Controller Lua CAN read them. weld.lua has been doing it since 2026-07-28 and
-- its published level reaches the host through GetSysVarValue, which is a real
-- RPC call rather than another cache read. This file is that same mechanism with
-- the welding taken out.
--
-- -----------------------------------------------------------------------------
-- Input contract — the harness MUST set these before running this file
-- -----------------------------------------------------------------------------
--   IO_MONITOR_RUN  1 runs the loop; anything else (including unset) makes this
--                   file define-only. Same upload gate weld.lua uses, for the
--                   same reason: the controller's post-upload check EXECUTES
--                   top-level Lua (verified live 2026-07-28), so a file that
--                   loops at its top level would run during its own upload.
--
-- Optional:
--   IO_MONITOR_MS   how long to watch, in ms. Clamped to MONITOR_MAX_MS.
--
-- -----------------------------------------------------------------------------
-- Why the loop is bounded rather than run-until-stopped
-- -----------------------------------------------------------------------------
-- Run-until-stopped would be the nicer control, but the post-upload check
-- executes top-level Lua, and it is NOT established whether that execution
-- follows a NewDofile into this file. If it does, an unbounded loop here would
-- hang the upload of its own harness with no way to interrupt it. A bounded loop
-- is correct under both readings: worst case the upload takes the monitor window
-- and then completes on its own.
--
-- The operator can still end it early — the page's Stop button stops the
-- program the normal way.
-- =========================================

-- Must match DI_STUD_ON_WORK / DI_WELD_READY in programs/weld.lua and
-- WELD_STUD_DI / WELD_READY_DI in backend/app.py. Nothing enforces that across
-- the language boundary; tests/test_lua_builder.py asserts all three agree.
local DI_STUD_ON_WORK = 1
local DI_WELD_READY   = 0

-- Same slots weld.lua publishes to, so the page needs no second code path and
-- cannot end up decoding one source with the other's meaning.
local SV_STUD_ON_WORK = 6
local SV_WELD_READY   = 7

-- Fast enough that flipping a circuit by hand looks immediate against the page's
-- 400 ms poll, slow enough not to flood the controller with GetDI calls.
local SAMPLE_MS = 200

local MONITOR_DEFAULT_MS = 45000
local MONITOR_MAX_MS     = 300000

-- The manual spells the setter SetSysVarvalue (Table 3-12), the Python SDK spells
-- it SetSysVarValue, and the manual's example is OCR-mangled enough that neither
-- casing is trustworthy. Resolved once, exactly as weld.lua does it.
local function sysVarSetter()
    if type(SetSysVarvalue) == "function" then return SetSysVarvalue end
    if type(SetSysVarValue) == "function" then return SetSysVarValue end
    return nil
end

-- Bare numbers only, for the reason weld.lua's pub() documents at length: it is
-- the one argument form that cannot throw under either reading of the docs, and
-- pcall is banned outright by the controller's upload check.
local current_di1 = -1
local current_di0 = -1

local function pub(slot, value)
    local setter = sysVarSetter()
    if setter == nil then return end

    if slot == SV_STUD_ON_WORK then current_di1 = value end
    if slot == SV_WELD_READY then current_di0 = value end

    -- Pack phase (0 for monitor), DI1, and DI0 into slot 1
    if slot == SV_STUD_ON_WORK or slot == SV_WELD_READY then
        local d1 = (current_di1 == 1 and 1 or (current_di1 == 0 and 0 or 9))
        local d0 = (current_di0 == 1 and 1 or (current_di0 == 0 and 0 or 9))
        local packed = 0 + (100 * d1) + (1000 * d0)
        setter(1, packed)
    end

    setter(slot, value)
end

-- Returns 1 when the input is active, 0 otherwise, and publishes the level.
-- GetDI is documented as `ret = GetDI(id, thread)` (Table 3-76), a bare value,
-- but this tolerates the (err, value) shape too — controller-side return shapes
-- have been wrong in this repo before and it costs nothing to accept both.
local function readDI(id)
    local a, b = GetDI(id, 0)
    local value = b
    if value == nil then
        value = a
    end
    local level = 0
    if value == 1 or value == true then
        level = 1
    end
    if value == 1 then
        level = 1
    end
    if id == DI_STUD_ON_WORK then
        pub(SV_STUD_ON_WORK, level)
    elseif id == DI_WELD_READY then
        pub(SV_WELD_READY, level)
    end
    return level
end

local function monitorIO()
    local budget = MONITOR_DEFAULT_MS
    if type(IO_MONITOR_MS) == "number"
       and IO_MONITOR_MS > 0
       and IO_MONITOR_MS <= MONITOR_MAX_MS then
        budget = IO_MONITOR_MS
    end

    print(string.format("[IO] Monitoring DI%d (stud on work) and DI%d (welder ready) for %d ms.",
        DI_STUD_ON_WORK, DI_WELD_READY, budget))

    local waited = 0
    while waited < budget do
        readDI(DI_STUD_ON_WORK)
        readDI(DI_WELD_READY)
        WaitMs(SAMPLE_MS)
        waited = waited + SAMPLE_MS
    end

    -- Hand the slots back as "unknown" on a clean finish. System variables outlive
    -- the program that wrote them, and a level left sitting in a slot is exactly
    -- how a stale reading gets shown as a live input (observed 2026-07-30, when a
    -- SEATED left over from a press was read as a stud seated right now). The page
    -- also greys these tiles whenever no program is running, so this is the second
    -- of two independent guards — an operator who stops the monitor early skips
    -- this line, and the page still must not lie.
    pub(SV_STUD_ON_WORK, -1)
    pub(SV_WELD_READY, -1)
    print("[IO] Monitor finished.")
end

-- Upload gate — see the input contract. The controller's check runs this top
-- level with no globals set, so unset must mean "define only, do nothing".
if IO_MONITOR_RUN == 1 then
    monitorIO()
end
